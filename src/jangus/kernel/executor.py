"""Kernel executor: the only code that runs tools.

Order for every effect: decide -> persist intent (and charge the budget) ->
write the ledger intent -> execute -> verify -> write the ledger result ->
persist the result. Models never reach this code except as proposed data.

Ledger entries carry hashes only: never arguments, results or secrets.
"""

import hashlib
import json
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any, Literal

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.ledger import Ledger
from jangus.kernel.policy import Decision, Outcome, decide
from jangus.kernel.registry import Registry, Tier, ToolSpec
from jangus.kernel.secrets import SecretBackend, redact, resolve_secrets
from jangus.kernel.state import ActionRecord, StateStore

Status = Literal["rejected", "queued", "done", "unresolved", "already_done"]


@dataclass(frozen=True)
class AgentContext:
    """What the kernel knows about the proposing agent (from its approved spec)."""

    allowed_tools: Collection[str]
    max_tier: Tier
    budget: str
    secrets_allowed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ExecResult:
    status: Status
    decision: Decision
    key: str | None = None
    result: dict[str, Any] | None = None
    result_hash: str | None = None


def _hash_json(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Executor:
    def __init__(
        self,
        registry: Registry,
        state: StateStore,
        ledger: Ledger,
        secrets: SecretBackend | None = None,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        self._registry = registry
        self._state = state
        self._ledger = ledger
        self._secrets = secrets
        self._checkpoint = checkpoint

    def _hook(self, name: str) -> None:
        if self._checkpoint is not None:
            self._checkpoint(name)

    def _require_writable(self) -> None:
        if not (self._ledger.effects_allowed and self._state.writable):
            raise RuntimeFault(FaultCode.LEDGER_UNAVAILABLE, "ledger or state blocked: no effects")

    # -- submit ------------------------------------------------------------

    def submit(
        self,
        run_id: int,
        agent: AgentContext,
        tool_name: str,
        raw_args: object,
        key: str | None = None,
    ) -> ExecResult:
        self._require_writable()
        decision = decide(
            self._registry,
            tool_name,
            raw_args,
            allowed_tools=agent.allowed_tools,
            agent_max_tier=agent.max_tier,
        )
        if decision.outcome is Outcome.REJECT:
            self._ledger.append(
                {
                    "kind": "rejected",
                    "run": run_id,
                    "tool": tool_name,
                    "reason": str(decision.reason),
                    "tier": str(decision.tier) if decision.tier else None,
                }
            )
            return ExecResult("rejected", decision)
        assert decision.args is not None and decision.payload_hash is not None  # noqa: S101
        if decision.outcome is Outcome.QUEUE:
            self._ledger.append(
                {
                    "kind": "queued",
                    "run": run_id,
                    "tool": tool_name,
                    "payload_hash": decision.payload_hash,
                }
            )
            return ExecResult("queued", decision)
        return self._run(run_id, agent, decision, key)

    def _run(
        self, run_id: int, agent: AgentContext, decision: Decision, key: str | None
    ) -> ExecResult:
        tool = self._registry.get(decision.tool)
        assert tool is not None and decision.args is not None  # noqa: S101
        assert decision.payload_hash is not None  # noqa: S101
        try:
            secrets = resolve_secrets(self._secrets, tool, agent.secrets_allowed)
        except RuntimeFault as fault:
            self._ledger.append(
                {
                    "kind": "rejected",
                    "run": run_id,
                    "tool": tool.name,
                    "reason": fault.code.value,
                }
            )
            return ExecResult("rejected", decision)
        action_key = key or _hash_json({"run": run_id, "payload": decision.payload_hash})
        args_json = json.dumps(decision.args.model_dump(mode="json"), sort_keys=True)

        record, created = self._state.record_intent(
            run_id=run_id,
            idempotency_key=action_key,
            tool=tool.name,
            payload_hash=decision.payload_hash,
            budget=agent.budget,
            cost=1,
            args_json=args_json,
        )
        if not created:
            status: Status = "already_done" if record.status == "done" else "unresolved"
            return ExecResult(status, decision, key=action_key, result_hash=record.result_hash)
        self._hook("after_intent_state")
        self._ledger.append(
            {
                "kind": "intent",
                "run": run_id,
                "key": action_key,
                "tool": tool.name,
                "tier": str(decision.tier),
                "payload_hash": decision.payload_hash,
            }
        )
        self._hook("after_intent_ledger")
        return self._execute_and_verify(tool, decision, record, decision.args, secrets)

    def _execute_and_verify(
        self,
        tool: ToolSpec,
        decision: Decision,
        record: ActionRecord,
        args: Any,
        secrets: dict[str, str],
    ) -> ExecResult:
        try:
            result = tool.execute(args, secrets)
            result, redacted = redact(result, secrets)
            self._hook("after_effect")
            result_hash = _hash_json(result)
            verified = True if tool.verify is None else bool(tool.verify(args, result))
            self._hook("after_verify")
        except Exception:  # noqa: BLE001 - any tool failure leaves the effect unknown
            return self._finish(record, decision, "unresolved", None, None, "tool_error")
        if not verified:
            return self._finish(
                record, decision, "unresolved", result, result_hash, "verify_failed"
            )
        return self._finish(
            record,
            decision,
            "done",
            result,
            result_hash,
            "verified_redacted" if redacted else "verified",
        )

    def _finish(
        self,
        record: ActionRecord,
        decision: Decision,
        status: Literal["done", "unresolved"],
        result: dict[str, Any] | None,
        result_hash: str | None,
        note: str,
    ) -> ExecResult:
        self._ledger.append(
            {
                "kind": "result",
                "key": record.idempotency_key,
                "tool": record.tool,
                "status": status,
                "result_hash": result_hash,
                "note": note,
            }
        )
        self._hook("after_result_ledger")
        self._state.record_result(record.idempotency_key, status, result_hash)
        return ExecResult(
            status, decision, key=record.idempotency_key, result=result, result_hash=result_hash
        )

    # -- recovery ----------------------------------------------------------

    def recover(
        self, secrets_allowed: Callable[[ActionRecord], frozenset[str]] | None = None
    ) -> list[ExecResult]:
        """Settle every action left at 'intent' by a crash. Never duplicates an effect.

        Tools that need secrets are settled only if the caller says which secrets the
        action's agent may use; otherwise they fail closed as unresolved.
        """
        self._require_writable()
        settled: list[ExecResult] = []
        for record in self._state.list_open_actions():
            tool = self._registry.get(record.tool)
            decision = Decision(Outcome.RUN, record.tool, payload_hash=record.payload_hash)
            if tool is None:
                settled.append(self._unresolved(record, decision, "tool_missing"))
                continue
            args = tool.args_model.model_validate(json.loads(record.args_json))
            allowed = secrets_allowed(record) if secrets_allowed is not None else frozenset()
            try:
                secrets = resolve_secrets(self._secrets, tool, allowed)
            except RuntimeFault:
                settled.append(self._unresolved(record, decision, "secret_unavailable"))
                continue
            if tool.reconcile is None:
                # Only T1 tools may lack reconcile; they have no side effects to duplicate.
                settled.append(self._execute_and_verify(tool, decision, record, args, secrets))
                continue
            found = tool.reconcile(args)
            if found.state == "applied" and found.result is not None:
                settled.append(self._adopt(tool, decision, record, args, found.result, secrets))
            elif found.state == "absent":
                self._ledger.append(
                    {"kind": "reconciled", "key": record.idempotency_key, "state": "absent"}
                )
                settled.append(self._execute_and_verify(tool, decision, record, args, secrets))
            else:
                settled.append(self._unresolved(record, decision, "reconcile_unknown"))
        return settled

    def _adopt(
        self,
        tool: ToolSpec,
        decision: Decision,
        record: ActionRecord,
        args: Any,
        result: dict[str, Any],
        secrets: dict[str, str],
    ) -> ExecResult:
        result, _ = redact(result, secrets)
        self._ledger.append(
            {"kind": "reconciled", "key": record.idempotency_key, "state": "applied"}
        )
        if tool.verify is not None and not tool.verify(args, result):
            return self._unresolved(record, decision, "verify_failed")
        return self._finish(record, decision, "done", result, _hash_json(result), "adopted")

    def _unresolved(self, record: ActionRecord, decision: Decision, note: str) -> ExecResult:
        return self._finish(record, decision, "unresolved", None, None, note)
