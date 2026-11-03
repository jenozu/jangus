"""Per-call metering: every model call writes one ledger record (B37, V2-FR32).

Measured usage comes from the provider. If the provider reports none (`usage_unknown`),
the call is charged at the pre-call estimate, which is conservative and flagged.
Kernel-run work makes no model call and so writes no model_call record: its tokens are 0.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from jangus.agents.providers.base import ModelRequest, ModelResponse
from jangus.kernel.ledger import Ledger

ROLES = ("kernel-run", "specialist", "step-up", "orchestrator")


def estimate_tokens(text: str) -> int:
    """Rough input estimate: bytes / 3 until calibrated against measured values."""
    return math.ceil(len(text.encode("utf-8")) / 3)


@dataclass(frozen=True)
class CallCharge:
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    usage_known: bool
    est_input_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def charge_for(request: ModelRequest, response: ModelResponse) -> CallCharge:
    est = estimate_tokens(request.system + request.user)
    usage = response.usage
    if usage.known:
        assert usage.input_tokens is not None and usage.output_tokens is not None  # noqa: S101
        return CallCharge(
            usage.input_tokens, usage.output_tokens, usage.cached_tokens or 0, True, est
        )
    # usage_unknown: charge the worst case we can see
    return CallCharge(est, request.max_tokens, 0, False, est)


def record_model_call(
    ledger: Ledger,
    *,
    role: str,
    reason: str,
    run_id: int,
    agent: str,
    task: str,
    attempt: int,
    request: ModelRequest,
    response: ModelResponse | None,
    charge: CallCharge | None,
    outcome: str,
    cost_micros: int = 0,
    extra: dict[str, Any] | None = None,
) -> None:
    ledger.append(
        {
            "kind": "model_call",
            "role": role,
            "reason": reason,
            "run": run_id,
            "agent": agent,
            "task": task,
            "attempt": attempt,
            "provider": response.provider if response else None,
            "model": request.model,
            "input_tokens": charge.input_tokens if charge else 0,
            "output_tokens": charge.output_tokens if charge else 0,
            "cached_tokens": charge.cached_tokens if charge else 0,
            "usage_known": charge.usage_known if charge else None,
            "est_input_tokens": charge.est_input_tokens
            if charge
            else estimate_tokens(request.system + request.user),
            "latency_ms": response.latency_ms if response else 0,
            "cost_usd_micros": cost_micros,
            "outcome": outcome,
            **(extra or {}),
        }
    )


def usage_from_events(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Calls, tokens and spend per role from `model_call` events (B30). Every role is listed;
    kernel-run is always zero because kernel-run work makes no model call."""
    totals: dict[str, dict[str, int]] = {
        role: {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "cost_usd_micros": 0,
        }
        for role in ROLES
    }
    for event in events:
        if event.get("kind") != "model_call":
            continue
        row = totals.setdefault(str(event["role"]), dict.fromkeys(totals["specialist"], 0))
        row["calls"] += 1
        for key in ("input_tokens", "output_tokens", "cached_tokens", "cost_usd_micros"):
            row[key] += int(event.get(key, 0))
    return totals


def usage_by_role(ledger: Ledger) -> dict[str, dict[str, int]]:
    return usage_from_events(entry.event for entry in ledger.entries())
