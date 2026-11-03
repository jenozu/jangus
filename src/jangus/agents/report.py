"""Usage report written by the kernel from the ledger, with no model (B30, V2-FR26).

Calls, tokens and spend per role, per run and per agent; the orchestrator calls with their
reasons; step-ups; and the costliest tasks. Because it is computed from the same ledger the
budgets are charged from, the totals match. Scheduling and delivery of the daily report is
task 19.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from jangus.agents.metering import usage_from_events
from jangus.kernel.ledger import Ledger


@dataclass(frozen=True)
class UsageReport:
    day: str | None
    by_role: dict[str, dict[str, int]]
    by_run: dict[int, dict[str, int]]
    by_agent: dict[str, dict[str, int]]
    orchestrator_calls: list[dict[str, Any]]
    step_ups: int
    top_tasks: list[tuple[str, int]]
    totals: dict[str, int]

    def render(self) -> str:
        def usd(micros: int) -> str:
            return f"${micros / 1_000_000:.4f}"

        lines = [f"Usage report{' for ' + self.day if self.day else ''}"]
        lines.append("Per role:")
        for role, row in self.by_role.items():
            tokens = row["input_tokens"] + row["output_tokens"]
            lines.append(
                f"  {role}: {row['calls']} calls, {tokens} tokens, {usd(row['cost_usd_micros'])}"
            )
        t = self.totals
        lines.append(
            f"Total: {t['calls']} calls, {t['tokens']} tokens, {usd(t['cost_usd_micros'])}"
        )
        lines.append(
            f"Step-ups: {self.step_ups}; orchestrator calls: {len(self.orchestrator_calls)}"
        )
        for call in self.orchestrator_calls:
            lines.append(f"  orchestrator: {call.get('reason')}")
        for task, tokens in self.top_tasks:
            lines.append(f"  costly task {task}: {tokens} tokens")
        return "\n".join(lines)


def build_usage_report(ledger: Ledger, day: str | None = None) -> UsageReport:
    """`day` is an ISO date such as 2026-11-03; None means the whole ledger."""
    calls: list[dict[str, Any]] = []
    orchestrator: list[dict[str, Any]] = []
    for entry in ledger.entries():
        if day is not None and not entry.ts.startswith(day):
            continue
        event = entry.event
        if event.get("kind") == "model_call":
            calls.append(event)
        elif event.get("kind") in ("escalation", "escalation_queued"):
            orchestrator.append(event)
    by_role = usage_from_events(calls)
    by_run: dict[int, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "tokens": 0, "cost_usd_micros": 0}
    )
    by_agent: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "tokens": 0, "cost_usd_micros": 0}
    )
    per_task: dict[str, int] = defaultdict(int)
    for c in calls:
        tokens = int(c["input_tokens"]) + int(c["output_tokens"])
        for row in (by_run[int(c["run"])], by_agent[str(c["agent"])]):
            row["calls"] += 1
            row["tokens"] += tokens
            row["cost_usd_micros"] += int(c.get("cost_usd_micros", 0))
        per_task[str(c["task"])] += tokens
    totals = {
        "calls": len(calls),
        "tokens": sum(int(c["input_tokens"]) + int(c["output_tokens"]) for c in calls),
        "cost_usd_micros": sum(int(c.get("cost_usd_micros", 0)) for c in calls),
    }
    return UsageReport(
        day,
        by_role,
        dict(by_run),
        dict(by_agent),
        orchestrator,
        sum(1 for c in calls if c["role"] == "step-up"),
        sorted(per_task.items(), key=lambda kv: (-kv[1], kv[0]))[:5],
        totals,
    )
