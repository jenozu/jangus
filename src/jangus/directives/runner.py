"""Directive runner: run a directive one task at a time with kernel-owned status (B24-B26).

The model never sets status. A task becomes `done` only after the kernel's own check passes:
for kernel-run tasks, every scripted step executed and verified by its tool; for model tasks,
a verifier supplied for that task. A model task with no verifier ends in `review` for the owner
(DIRECTIVES.md rule 4). Status changes are ledger events, so a restart resumes from them and
a deferred task is never silently retried.

Stop rules: budget exhausted, an unresolved effect, a T3 call waiting for or refused approval,
or the attempt ladder spent. Each defers (or reviews) the task, halts the directive, queues an
alert and writes the report from a fixed template. The orchestrator is called only if the
directive says `escalate` and the gate allows it; this runner never calls a model for
completion, confirmation or the report.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from jangus.agents.attempt_tracker import tracker_for
from jangus.agents.runner import ExecutedProposal, Job, Runner, RunOutcome
from jangus.agents.spec import LoadedSpec
from jangus.directives.models import (
    Directive,
    Evidence,
    Task,
    TaskStatus,
    directive_hash,
    kernel_set_status,
)
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import AlertLevel, AlertQueue
from jangus.kernel.approvals import ApprovalQueue
from jangus.kernel.attempts import Step, lineage_root, task_key
from jangus.kernel.budgets import BudgetBook, Limits
from jangus.kernel.ledger import Ledger
from jangus.kernel.state import StateStore


@dataclass(frozen=True)
class TaskEvidence:
    """Kernel facts about what a task did. Built from executor results, never from model text."""

    executed: tuple[ExecutedProposal, ...]
    ledger_seq: int


Verifier = Callable[[Task, TaskEvidence], bool]


class EscalationGate(Protocol):
    """Decides whether a halted directive may call the orchestrator (daily cap, task 25)."""

    def allow(self, directive_id: str, reason: str) -> bool: ...


@dataclass(frozen=True)
class HaltInfo:
    directive_id: str
    task_id: int
    reason: str


@dataclass(frozen=True)
class DirectiveReport:
    directive_id: str
    completed: bool
    halted: HaltInfo | None
    statuses: tuple[tuple[int, str], ...]
    escalated: bool
    text: str


_RESTORABLE = frozenset({"in-progress", "done", "review", "deferred"})


def _invalid(reason: str) -> RuntimeFault:
    return RuntimeFault(FaultCode.DIRECTIVE_INVALID, reason)


def directive_scope(directive_id: str) -> str:
    return f"directive:{directive_id}"


class DirectiveRunner:
    def __init__(
        self,
        *,
        loaded: LoadedSpec,
        runner: Runner,
        state: StateStore,
        ledger: Ledger,
        budgets: BudgetBook,
        alerts: AlertQueue,
        approvals: ApprovalQueue | None = None,
    ) -> None:
        self._loaded = loaded
        self._runner = runner
        self._state = state
        self._ledger = ledger
        self._budgets = budgets
        self._alerts = alerts
        self._approvals = approvals

    # -- public ------------------------------------------------------------

    def run(
        self,
        directive: Directive,
        *,
        verifiers: Mapping[int, Verifier] | None = None,
        gate: EscalationGate | None = None,
        on_escalate: Callable[[HaltInfo], None] | None = None,
    ) -> DirectiveReport:
        self._require_issued(directive)
        spec = self._loaded.spec
        if directive.assignee != spec.name or directive.spec_sha256 != self._loaded.sha256:
            raise _invalid("directive does not match the loaded spec")
        scope = directive_scope(directive.id)
        b = directive.budget
        self._budgets.define(
            scope, Limits.from_spec(b.calls, b.tokens, b.wall_seconds, b.spend_usd)
        )
        root = lineage_root(self._ledger, directive.id)
        current = self._restore(directive)
        results: dict[int, str] = {}
        halt: HaltInfo | None = None
        verifiers = verifiers or {}

        for task in current.tasks:
            status = self._status(current, task.id)
            if status == "done":
                results[task.id] = self._summary_from_ledger(directive.id, task.id)
                continue
            if status in ("deferred", "review"):
                halt = HaltInfo(directive.id, task.id, self._last_reason(directive.id, task.id))
                break
            if any(self._status(current, d) != "done" for d in task.dependencies):
                current = self._set(directive, current, task.id, "deferred", "dependency_not_done")
                halt = self._halt(directive, task, "dependency_not_done")
                break
            current = self._set(directive, current, task.id, "in-progress", "started")
            if task.runner == "kernel":
                outcome = self._run_kernel_task(directive, task, root)
            else:
                outcome = self._run_model_task(
                    directive, task, root, results, verifiers.get(task.id)
                )
            if outcome[0] == "done":
                current = self._set(directive, current, task.id, "done", "verified", verified=True)
                results[task.id] = outcome[1]
                self._ledger.append(
                    {
                        "kind": "task_summary",
                        "directive": directive.id,
                        "task": task.id,
                        "text": outcome[1],
                    }
                )
                continue
            new_status: TaskStatus = "review" if outcome[0] == "review" else "deferred"
            current = self._set(directive, current, task.id, new_status, outcome[1])
            halt = self._halt(directive, task, outcome[1])
            break

        escalated = False
        if (
            halt is not None
            and directive.escalate
            and gate is not None
            and gate.allow(directive.id, halt.reason)
        ):
            escalated = True
            if on_escalate is not None:
                on_escalate(halt)
        completed = halt is None and all(t.status == "done" for t in current.tasks)
        report = self._report(directive, current, halt, completed, escalated)
        self._ledger.append(
            {
                "kind": "directive_completed" if completed else "directive_halted",
                "directive": directive.id,
                "hash": directive_hash(directive),
                "reason": halt.reason if halt else None,
            }
        )
        return report

    # -- one task ----------------------------------------------------------

    def _run_kernel_task(self, directive: Directive, task: Task, root: str) -> tuple[str, str]:
        """Scripted steps through the executor. Zero model calls, zero tokens."""
        spec = self._loaded.spec
        run = self._state.create_run(f"kdir:{directive.id}:{task.id}", spec.name)
        ctx = self._runner.agent_context(task.tools)
        executed: list[ExecutedProposal] = []
        attempts = tracker_for(
            self._loaded, self._state, self._ledger, self._alerts, task_key(root, task.id), task
        ).attempts
        for step in task.steps:
            res = self._runner.executor.submit(run.id, ctx, step.tool, step.args)
            reason = res.decision.reason.value if res.decision.reason else None
            executed.append(
                ExecutedProposal(
                    step.tool, res.status, reason, res.decision.payload_hash, res.result_hash
                )
            )
            if res.status in ("done", "already_done"):
                continue
            if res.status == "queued":
                return ("deferred", "awaiting_t3_approval")
            if res.status == "unresolved":
                attempts.record_failure("unresolved_effect")
                return ("deferred", "unresolved_effect")
            attempts.record_failure(f"rejected:{reason or 'refused'}")
            return ("deferred", f"step_failed:{step.tool}:{reason or 'refused'}")
        return ("done", self._summarize(executed))

    def _run_model_task(
        self,
        directive: Directive,
        task: Task,
        root: str,
        results: Mapping[int, str],
        verifier: Verifier | None,
    ) -> tuple[str, str]:
        spec = self._loaded.spec
        scope = directive_scope(directive.id)
        tracker = tracker_for(
            self._loaded, self._state, self._ledger, self._alerts, task_key(root, task.id), task
        )
        inputs = {
            f"verified-result-task-{d}": results[d] for d in task.dependencies if d in results
        }
        guard = spec.attempts.default_model + spec.attempts.step_up + 3
        run_ids: set[int] = set()
        for _ in range(guard):
            if tracker.attempts.step() is Step.STOP:
                return ("deferred", tracker.attempts.exhausted_reason() or "attempts_exhausted")
            if self._budgets.exhausted(scope) or self._budgets.exhausted(f"agent:{spec.name}"):
                return ("deferred", "budget_exhausted")
            job = Job(
                job_id=f"{directive.id}:{task.id}:{tracker.attempts.failed()}:{len(run_ids)}",
                instruction=f"{task.title}\n{task.details}".strip(),
                inputs=inputs,
                tools=task.tools,
                task_label=f"{directive.id}/{task.id}",
                wall_seconds=task.max_wall_seconds,
                extra_scopes=(scope,),
            )
            report = self._runner.run(job, attempts=tracker)
            run_ids.add(report.run_id)
            rejected = self._rejected_approval(run_ids)
            if rejected:
                return ("deferred", "t3_approval_rejected")
            if any(e.status == "unresolved" for e in report.executed):
                tracker.attempts.record_failure("unresolved_effect")
                return ("deferred", "unresolved_effect")
            if any(e.status == "queued" for e in report.executed):
                return ("deferred", "awaiting_t3_approval")
            if report.outcome is RunOutcome.BUDGET:
                return ("deferred", "budget_exhausted")
            if report.outcome is RunOutcome.PROVIDER_ERROR:
                return ("deferred", "provider_error")
            if report.outcome is RunOutcome.STOPPED:
                return ("deferred", tracker.attempts.exhausted_reason() or "attempts_exhausted")
            if report.outcome is RunOutcome.MODEL_DONE:
                evidence = TaskEvidence(report.executed, 0)
                if verifier is None:
                    return ("review", "no_kernel_check_for_this_task")
                if verifier(task, evidence):
                    return ("done", self._summarize(list(report.executed)))
                tracker.attempts.record_failure("verification_failed")
            # LIMIT, or a failed verification: the failure is counted; loop checks the ladder
        return ("deferred", "attempts_exhausted")

    # -- helpers -----------------------------------------------------------

    def _rejected_approval(self, run_ids: set[int]) -> bool:
        if self._approvals is None:
            return False
        return any(a.run_id in run_ids for a in self._approvals.list("rejected"))

    @staticmethod
    def _summarize(executed: list[ExecutedProposal]) -> str:
        done = [e for e in executed if e.status in ("done", "already_done")]
        hashes = ",".join((e.result_hash or "-")[:12] for e in done)
        return f"{len(done)} verified action(s); result hashes: {hashes}"

    def _require_issued(self, directive: Directive) -> None:
        digest = directive_hash(directive)
        for entry in self._ledger.entries():
            e = entry.event
            if (
                e.get("kind") == "directive_issued"
                and e.get("id") == directive.id
                and e.get("hash") == digest
            ):
                return
        raise _invalid("directive was not issued (no matching ledger record)")

    def _restore(self, directive: Directive) -> Directive:
        current = directive
        for entry in self._ledger.entries():
            e = entry.event
            if e.get("kind") != "task_status" or e.get("directive") != directive.id:
                continue
            status = str(e["status"])
            task_id = int(e["task"])
            if status in _RESTORABLE:
                try:
                    current = kernel_set_status(
                        current,
                        task_id,
                        cast(TaskStatus, status),
                        Evidence(entry.seq, verified=bool(e.get("verified"))),
                    )
                except RuntimeFault:
                    continue  # a replayed duplicate transition: already applied
        return current

    @staticmethod
    def _status(directive: Directive, task_id: int) -> str:
        return next(t.status for t in directive.tasks if t.id == task_id)

    def _set(
        self,
        directive: Directive,
        current: Directive,
        task_id: int,
        status: TaskStatus,
        reason: str,
        *,
        verified: bool = False,
    ) -> Directive:
        entry = self._ledger.append(
            {
                "kind": "task_status",
                "directive": directive.id,
                "task": task_id,
                "status": status,
                "reason": reason,
                "verified": verified,
            }
        )
        return kernel_set_status(
            current,
            task_id,
            status,
            Evidence(entry.seq, verified=verified),
        )

    def _last_reason(self, directive_id: str, task_id: int) -> str:
        reason = "unknown"
        for entry in self._ledger.entries():
            e = entry.event
            if (
                e.get("kind") == "task_status"
                and e.get("directive") == directive_id
                and e.get("task") == task_id
            ):
                reason = str(e.get("reason"))
        return reason

    def _summary_from_ledger(self, directive_id: str, task_id: int) -> str:
        text = ""
        for entry in self._ledger.entries():
            e = entry.event
            if (
                e.get("kind") == "task_summary"
                and e.get("directive") == directive_id
                and e.get("task") == task_id
            ):
                text = str(e.get("text"))
        return text

    def _halt(self, directive: Directive, task: Task, reason: str) -> HaltInfo:
        self._alerts.raise_alert(
            "directive_halted",
            f"directive {directive.id} halted at task {task.id}: {reason}",
            level=AlertLevel.CRITICAL,
            detail={"directive": directive.id, "task": task.id, "reason": reason},
            once=f"halt:{directive.id}:{task.id}:{reason}",
        )
        return HaltInfo(directive.id, task.id, reason)

    def _report(
        self,
        directive: Directive,
        current: Directive,
        halt: HaltInfo | None,
        completed: bool,
        escalated: bool,
    ) -> DirectiveReport:
        spent: dict[str, Any] = {
            dim.value: rec.spent
            for dim, rec in self._budgets.status(directive_scope(directive.id)).items()
        }
        lines = [
            f"Directive {directive.id} for {directive.assignee}",
            f"Goal: {directive.goal}",
            "Result: "
            + (
                "completed"
                if completed
                else f"HALTED at task {halt.task_id}: {halt.reason}"
                if halt
                else "incomplete"
            ),
        ]
        lines += [f"  task {t.id} [{t.status}] {t.title}" for t in current.tasks]
        lines.append(
            "Spent: "
            + ", ".join(f"{k}={v}" for k, v in sorted(spent.items()))
            + f"; escalated to orchestrator: {'yes' if escalated else 'no'}"
        )
        return DirectiveReport(
            directive.id,
            completed,
            halt,
            tuple((t.id, t.status) for t in current.tasks),
            escalated,
            "\n".join(lines),
        )
