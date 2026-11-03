"""Propose-then-execute run loop (B10, B12).

One run is one fresh job: the prompt is built only from the spec, the job's instruction and
quoted data, and the results of *this* run. The model answers with proposed tool calls as
data; the kernel validates and executes each one through the executor and the answers go
back to the model as quoted data. The loop ends when the model says it is done, a budget is
exhausted, a limit is hit, the provider fails, or the attempt tracker says stop.

The runner never sets task status and never decides anything the kernel owns: models,
limits and permissions come from the approved spec and the attempt tracker. A model saying
it is "done" is a claim for the directive runner to verify.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from jangus.agents.metering import CallCharge, charge_for, estimate_tokens, record_model_call
from jangus.agents.prompt import result_block, system_prompt, tool_catalog, user_prompt
from jangus.agents.proposals import REPLY_SCHEMA, Reply, ReplyError, parse_reply, proposal_hash
from jangus.agents.providers.base import ModelRequest, ProviderHub
from jangus.agents.spec import LoadedSpec
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import AlertQueue
from jangus.kernel.budgets import BudgetBook, Dimension, Limits
from jangus.kernel.executor import AgentContext, Executor
from jangus.kernel.ledger import Ledger
from jangus.kernel.registry import Registry
from jangus.kernel.state import StateStore

_PROVIDER_FAULTS = frozenset(
    {FaultCode.PROVIDER_DISABLED, FaultCode.PROVIDER_FAILED, FaultCode.PROVIDER_BAD_RESPONSE}
)


class RunOutcome(StrEnum):
    MODEL_DONE = "model_done"  # the model claims it finished; the kernel still verifies
    BUDGET = "budget"
    LIMIT = "limit"
    PROVIDER_ERROR = "provider_error"
    STOPPED = "stopped"  # the attempt tracker has no model left to try


@dataclass(frozen=True)
class ModelChoice:
    """The model for the next call. Chosen by the kernel's attempt tracker, never by a reply."""

    provider: str
    model: str
    max_tokens: int
    role: str  # "specialist" or "step-up"
    reason: str  # "task" or "step_up"


class AttemptTracker(Protocol):
    """Kernel-side counting of attempts, limits and the escalation ladder (task 26).

    Methods that can refuse return the reason (such as "limit:max_proposals") or None.
    """

    def choose_model(self) -> ModelChoice | None: ...

    def note_model_call(self) -> str | None: ...

    def note_proposal(self, proposal_hash: str) -> str | None: ...

    def note_result(self, proposal_hash: str, result_hash: str | None) -> None: ...

    def note_elapsed_ms(self, elapsed_ms: int) -> str | None: ...

    def end_round(self, failures: Sequence[str]) -> Sequence[str]: ...

    def failure_reasons(self) -> Sequence[str]: ...


class LimitsOnlyAttempts:
    """Minimal in-memory tracker: spec ceilings only, no ladder and no repeat detection."""

    def __init__(self, loaded: LoadedSpec) -> None:
        self._spec = loaded.spec
        self._calls = 0
        self._proposals = 0
        self._reasons: list[str] = []

    def choose_model(self) -> ModelChoice | None:
        m = self._spec.model
        return ModelChoice(m.provider, m.name, m.max_tokens, "specialist", "task")

    def note_model_call(self) -> str | None:
        self._calls += 1
        return (
            "limit:max_model_calls" if self._calls > self._spec.attempts.max_model_calls else None
        )

    def note_proposal(self, proposal_hash: str) -> str | None:
        self._proposals += 1
        return (
            "limit:max_proposals" if self._proposals > self._spec.attempts.max_proposals else None
        )

    def note_result(self, proposal_hash: str, result_hash: str | None) -> None:
        return None

    def note_elapsed_ms(self, elapsed_ms: int) -> str | None:
        return None

    def end_round(self, failures: Sequence[str]) -> Sequence[str]:
        self._reasons.extend(failures)
        return tuple(failures)

    def failure_reasons(self) -> Sequence[str]:
        return tuple(self._reasons)


@dataclass(frozen=True)
class Job:
    job_id: str
    instruction: str
    inputs: Mapping[str, str] = field(default_factory=dict)  # external content: quoted as data
    tools: tuple[str, ...] | None = None  # narrows the spec's tools; can never widen them
    task_label: str = "task"
    wall_seconds: int | None = None
    extra_scopes: tuple[str, ...] = ()  # budget scopes charged in addition to the agent's


@dataclass(frozen=True)
class ExecutedProposal:
    tool: str
    status: str
    reason: str | None
    payload_hash: str | None
    result_hash: str | None


@dataclass(frozen=True)
class RunReport:
    run_id: int
    outcome: RunOutcome
    model_calls: int
    executed: tuple[ExecutedProposal, ...]
    failures: tuple[str, ...]
    done_claimed: bool
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def agent_scope(name: str) -> str:
    return f"agent:{name}"


class Runner:
    def __init__(
        self,
        *,
        spec: LoadedSpec,
        executor: Executor,
        registry: Registry,
        state: StateStore,
        ledger: Ledger,
        hub: ProviderHub,
        budgets: BudgetBook,
        alerts: AlertQueue,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loaded = spec
        self._executor = executor
        self._registry = registry
        self._state = state
        self._ledger = ledger
        self._hub = hub
        self._budgets = budgets
        self._alerts = alerts
        self._clock = clock

    def ensure_budgets(self) -> None:
        """Define the agent's scope from its approved spec (spent amounts are kept)."""
        spec = self._loaded.spec
        b = spec.budget
        self._budgets.define(
            agent_scope(spec.name), Limits.from_spec(b.calls, b.tokens, b.wall_seconds, b.spend_usd)
        )
        self._state.set_budget(
            f"{agent_scope(spec.name)}/actions", b.calls * spec.attempts.max_proposals
        )

    def run(self, job: Job, attempts: AttemptTracker | None = None) -> RunReport:
        spec = self._loaded.spec
        tracker = attempts if attempts is not None else LimitsOnlyAttempts(self._loaded)
        scope = agent_scope(spec.name)
        scopes = [scope, *job.extra_scopes]
        run = self._state.create_run(f"run:{spec.name}:{job.job_id}", spec.name)
        allowed = tuple(t for t in spec.tools if job.tools is None or t in job.tools)
        ctx = AgentContext(
            allowed_tools=allowed,
            max_tier=spec.max_tier,
            budget=f"{scope}/actions",
            secrets_allowed=frozenset(spec.secrets),
        )
        system = system_prompt(spec.role, spec.name, tool_catalog(self._registry, allowed))
        self._ledger.append(
            {
                "kind": "run_started",
                "run": run.id,
                "agent": spec.name,
                "spec_sha256": self._loaded.sha256,
                "job": job.job_id,
            }
        )

        started = self._clock()
        wall_limit = job.wall_seconds if job.wall_seconds is not None else spec.budget.wall_seconds
        results: list[str] = []
        executed: list[ExecutedProposal] = []
        failures: list[str] = []
        calls = 0
        outcome: RunOutcome | None = None
        done_claimed = False
        note = ""

        while outcome is None:
            round_started = self._clock()
            if self._clock() - started > wall_limit:
                tracker.end_round(["limit:wall_seconds"])
                failures.append("limit:wall_seconds")
                outcome = RunOutcome.LIMIT
                break
            if any(self._budgets.exhausted(s) for s in scopes):
                outcome = RunOutcome.BUDGET
                break
            choice = tracker.choose_model()
            if choice is None:
                outcome = RunOutcome.STOPPED
                break
            hit = tracker.note_model_call()
            if hit is not None:
                tracker.end_round([hit])
                failures.append(hit)
                outcome = RunOutcome.LIMIT
                break

            request = ModelRequest(
                model=choice.model,
                system=system,
                user=user_prompt(job.instruction, job.inputs, results, tracker.failure_reasons()),
                max_tokens=choice.max_tokens,
                json_schema=REPLY_SCHEMA,
                cache_prefix=True,
            )
            calls += 1
            try:
                response = self._hub.complete(choice.provider, request)
            except RuntimeFault as fault:
                if fault.code not in _PROVIDER_FAULTS:
                    raise
                self._meter(run.id, job, choice, calls, request, None, None, "provider_error")
                failures.append(f"provider:{fault.code.value}")
                outcome = RunOutcome.PROVIDER_ERROR
                break

            charge = charge_for(request, response)
            self._meter(run.id, job, choice, calls, request, response, charge, "ok")
            try:
                self._budgets.charge_all(
                    scopes,
                    {
                        Dimension.CALLS: 1,
                        Dimension.TOKENS: charge.total_tokens,
                        Dimension.WALL_MS: response.latency_ms,
                    },
                    reason="model_call",
                )
            except RuntimeFault as fault:
                if fault.code is not FaultCode.BUDGET_EXCEEDED:
                    raise
                outcome = RunOutcome.BUDGET
                break

            round_failures: list[str] = []
            reply: Reply | None = None
            try:
                reply = parse_reply(response.text)
            except ReplyError as err:
                round_failures.append(f"unparseable_reply:{err.reason}")

            if reply is not None:
                note = reply.note
                if reply.ignored_keys:
                    self._ledger.append(
                        {
                            "kind": "reply_keys_ignored",
                            "run": run.id,
                            "keys": list(reply.ignored_keys),
                        }
                    )
                outcome = self._execute(
                    run.id, ctx, reply, tracker, results, executed, round_failures
                )
                if not reply.proposals and not reply.done:
                    round_failures.append("empty_reply")
                if reply.done and outcome is None:
                    done_claimed = True
                    outcome = RunOutcome.MODEL_DONE

            wall_hit = tracker.note_elapsed_ms(int((self._clock() - round_started) * 1000))
            if wall_hit is not None:
                round_failures.append(wall_hit)
                if outcome is None:
                    outcome = RunOutcome.LIMIT
            failures.extend(tracker.end_round(round_failures))

        self._ledger.append(
            {
                "kind": "run_ended",
                "run": run.id,
                "outcome": outcome.value,
                "model_calls": calls,
                "proposals_executed": len(executed),
                "failures": failures,
            }
        )
        return RunReport(
            run.id, outcome, calls, tuple(executed), tuple(failures), done_claimed, note
        )

    def _execute(
        self,
        run_id: int,
        ctx: AgentContext,
        reply: Reply,
        tracker: AttemptTracker,
        results: list[str],
        executed: list[ExecutedProposal],
        round_failures: list[str],
    ) -> RunOutcome | None:
        for proposal in reply.proposals:
            digest = proposal_hash(self._registry, proposal)
            refusal = tracker.note_proposal(digest)
            index = len(results) + 1
            if refusal is not None:
                self._ledger.append(
                    {
                        "kind": "proposal_refused",
                        "run": run_id,
                        "tool": proposal.tool,
                        "proposal_hash": digest,
                        "reason": refusal,
                    }
                )
                results.append(
                    result_block(index, proposal.tool, {"status": "refused", "reason": refusal})
                )
                round_failures.append(refusal)
                if refusal.startswith("limit:"):
                    return RunOutcome.LIMIT
                continue
            try:
                res = self._executor.submit(run_id, ctx, proposal.tool, proposal.args)
            except RuntimeFault as fault:
                if fault.code is FaultCode.BUDGET_EXCEEDED:
                    return RunOutcome.BUDGET
                raise
            reason = res.decision.reason.value if res.decision.reason is not None else None
            executed.append(
                ExecutedProposal(
                    proposal.tool, res.status, reason, res.decision.payload_hash, res.result_hash
                )
            )
            results.append(
                result_block(
                    index,
                    proposal.tool,
                    {"status": res.status, "reason": reason, "result": res.result},
                )
            )
            if res.status == "rejected":
                round_failures.append(f"rejected:{reason or 'refused'}")
            elif res.status == "unresolved":
                round_failures.append("unresolved_effect")
            tracker.note_result(digest, res.result_hash)
        return None

    def _meter(
        self,
        run_id: int,
        job: Job,
        choice: ModelChoice,
        attempt: int,
        request: ModelRequest,
        response: Any,
        charge: CallCharge | None,
        outcome: str,
    ) -> None:
        record_model_call(
            self._ledger,
            role=choice.role,
            reason=choice.reason,
            run_id=run_id,
            agent=self._loaded.spec.name,
            task=job.task_label,
            attempt=attempt,
            request=request,
            response=response,
            charge=charge,
            outcome=outcome,
        )
        if charge is not None and not charge.usage_known:
            self._alerts.raise_alert(
                "usage_unknown",
                "a model call reported no token usage; charged at the estimate",
                detail={"model": request.model, "est_input_tokens": estimate_tokens(request.user)},
                once=f"usage_unknown:{request.model}",
            )
