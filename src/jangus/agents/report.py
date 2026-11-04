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


@dataclass(frozen=True)
class DailyReport:
    day: str
    usage: UsageReport
    attempts: dict[str, int]
    step_ups: int
    halted: list[dict[str, Any]]
    completed: int
    ticks: int
    cache_hits: int
    estimated_tokens_saved: int
    alerts: dict[str, int]

    def render(self) -> str:
        lines = [f"Daily report {self.day}", self.usage.render()]
        lines.append(
            f"Directives: {self.completed} completed, {len(self.halted)} halted; "
            f"ticks: {self.ticks}"
        )
        for halt in self.halted:
            lines.append(f"  HALTED {halt.get('directive')}: {halt.get('reason')}")
        failed = ", ".join(f"{k}={v}" for k, v in sorted(self.attempts.items())) or "none"
        lines.append(f"Failed attempts per task: {failed}; step-ups: {self.step_ups}")
        lines.append(
            f"Cache hits: {self.cache_hits} (estimated {self.estimated_tokens_saved} tokens saved; "
            "an estimate, not a measurement)"
        )
        alerts = ", ".join(f"{k}x{v}" for k, v in sorted(self.alerts.items())) or "none"
        lines.append(f"Alerts: {alerts}")
        return "\n".join(lines)


def build_daily_report(ledger: Ledger, day: str) -> DailyReport:
    """Everything here is read from the ledger for one ISO date; no model is involved."""
    usage = build_usage_report(ledger, day)
    attempts: dict[str, int] = defaultdict(int)
    halted: list[dict[str, Any]] = []
    alerts: dict[str, int] = defaultdict(int)
    completed = ticks = cache_hits = 0
    for entry in ledger.entries():
        if not entry.ts.startswith(day):
            continue
        event = entry.event
        kind = event.get("kind")
        if kind == "attempt_failed":
            attempts[str(event["task"])] += 1
        elif kind == "directive_halted":
            halted.append(event)
        elif kind == "directive_completed":
            completed += 1
        elif kind == "tick":
            ticks += 1
        elif kind == "cache_hit":
            cache_hits += 1
        elif kind == "alert":
            alerts[str(event["code"])] += 1
    per_call = usage.totals["tokens"] // usage.totals["calls"] if usage.totals["calls"] else 0
    return DailyReport(
        day,
        usage,
        dict(attempts),
        usage.step_ups,
        halted,
        completed,
        ticks,
        cache_hits,
        cache_hits * per_call,
        dict(alerts),
    )
