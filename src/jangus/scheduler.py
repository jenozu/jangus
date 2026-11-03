"""Scheduler tick (B13): run the agents that are due, once, then stop.

Windows Task Scheduler calls `jangus tick` every few minutes. Each tick:
1. takes the one-run lock (an overlapping tick does nothing),
2. loads each spec and runs only approved ones (an edited spec is inactive),
3. for every cron trigger finds the newest scheduled minute since the last finished run.
   Missed slots coalesce: a machine that slept through three nights runs once, not three
   times,
4. runs the agent's standing directive directly. The orchestrator is not involved,
5. records the slot as finished only when the directive completed or halted. A crash leaves
   the slot open, so the next tick resumes the same directive (its steps are idempotent).

A directive that halted is not re-run by later ticks; it waits for the owner.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from jangus.agents.spec import LoadedSpec, load_spec_file
from jangus.agents.spec_registry import SpecApprovalRegistry
from jangus.directives.runner import DirectiveReport
from jangus.errors import RuntimeFault
from jangus.kernel.alerts import AlertLevel, AlertQueue
from jangus.kernel.ledger import Ledger
from jangus.kernel.lock import RunLock
from jangus.kernel.state import StateStore

MAX_LOOKBACK = timedelta(days=7)
_FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _expand(field_text: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in field_text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
            if step != 1:
                end = high
        if step < 1 or start < low or end > high or start > end:
            raise ValueError(f"cron field out of range: {field_text!r}")
        values.update(range(start, end + 1, step))
    return values


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    days_restricted: bool
    weekdays_restricted: bool

    @classmethod
    def parse(cls, text: str) -> "Cron":
        parts = text.split()
        if len(parts) != 5:
            raise ValueError("cron needs five fields")
        sets = [_expand(p, lo, hi) for p, (lo, hi) in zip(parts, _FIELDS, strict=True)]
        weekdays = {0 if d == 7 else d for d in sets[4]}
        return cls(
            frozenset(sets[0]),
            frozenset(sets[1]),
            frozenset(sets[2]),
            frozenset(sets[3]),
            frozenset(weekdays),
            parts[2] != "*",
            parts[4] != "*",
        )

    def matches(self, when: datetime) -> bool:
        if when.minute not in self.minutes or when.hour not in self.hours:
            return False
        if when.month not in self.months:
            return False
        day_ok = when.day in self.days
        weekday_ok = ((when.weekday() + 1) % 7) in self.weekdays
        if self.days_restricted and self.weekdays_restricted:
            return day_ok or weekday_ok  # standard cron: either may match
        return day_ok and weekday_ok


def latest_due(cron: Cron, since: datetime, now: datetime) -> datetime | None:
    """The newest scheduled minute in (since, now], or None. Older misses are coalesced."""
    cursor = now.replace(second=0, microsecond=0)
    floor = max(since, now - MAX_LOOKBACK)
    while cursor > floor:
        if cron.matches(cursor):
            return cursor
        cursor -= timedelta(minutes=1)
    return None


@dataclass
class TickReport:
    skipped_overlap: bool = False
    ran: list[tuple[str, str]] = field(default_factory=list)  # (agent, slot)
    problems: list[str] = field(default_factory=list)


RunAgent = Callable[[LoadedSpec, datetime], DirectiveReport]


class Scheduler:
    def __init__(
        self,
        *,
        specs_dir: Path,
        registry: SpecApprovalRegistry,
        state: StateStore,
        ledger: Ledger,
        alerts: AlertQueue,
        lock: RunLock,
        run_agent: RunAgent,
    ) -> None:
        self._specs_dir = specs_dir
        self._registry = registry
        self._state = state
        self._ledger = ledger
        self._alerts = alerts
        self._lock = lock
        self._run_agent = run_agent

    def tick(self, now: datetime) -> TickReport:
        report = TickReport()
        if not self._lock.acquire():
            self._ledger.append({"kind": "tick_skipped", "reason": "another tick is running"})
            report.skipped_overlap = True
            return report
        try:
            if self._lock.took_over_stale:
                self._alerts.raise_alert(
                    "stale_lock_taken",
                    "a tick lock from an earlier crash was taken over",
                    once=f"stale_lock:{now.date().isoformat()}",
                )
            for path in sorted(self._specs_dir.glob("*.yaml")) if self._specs_dir.is_dir() else []:
                self._tick_spec(path, now, report)
            self._ledger.append(
                {
                    "kind": "tick",
                    "at": now.isoformat(),
                    "ran": [f"{a}@{s}" for a, s in report.ran],
                    "problems": report.problems,
                }
            )
        finally:
            self._lock.release()
        return report

    def _tick_spec(self, path: Path, now: datetime, report: TickReport) -> None:
        try:
            loaded = load_spec_file(path)
        except RuntimeFault as fault:
            report.problems.append(f"{path.name}: {fault.code.value}")
            self._alerts.raise_alert(
                "spec_unreadable",
                f"{path.name} is not a valid spec",
                once=f"unreadable:{path.name}",
            )
            return
        if not self._registry.is_approved(loaded):
            report.problems.append(f"{loaded.spec.name}: not approved")
            self._alerts.raise_alert(
                "spec_not_approved",
                f"spec {loaded.spec.name} is not approved (new or edited): it did not run",
                level=AlertLevel.WARNING,
                once=f"not_approved:{loaded.spec.name}:{loaded.sha256}",
            )
            return
        for index, trigger in enumerate(loaded.spec.triggers):
            if trigger.cron is None:
                continue
            self._tick_trigger(loaded, index, Cron.parse(trigger.cron), now, report)

    def _tick_trigger(
        self, loaded: LoadedSpec, index: int, cron: Cron, now: datetime, report: TickReport
    ) -> None:
        name = loaded.spec.name
        key = f"sched:{name}:{index}"
        seen = self._state.get_value(f"{key}:seen")
        if seen is None:
            self._state.set_value(f"{key}:seen", now.isoformat())  # no catch-up for history
            return
        done = self._state.get_value(f"{key}:done")
        since = datetime.fromisoformat(done or seen)
        slot = latest_due(cron, since, now)
        if slot is None:
            return
        try:
            result = self._run_agent(loaded, slot)
        except RuntimeFault as fault:
            report.problems.append(f"{name}@{slot.isoformat()}: {fault.code.value}")
            self._alerts.raise_alert(
                "scheduled_run_failed",
                f"scheduled run of {name} could not run: {fault.code.value}",
                level=AlertLevel.CRITICAL,
                detail={"agent": name, "slot": slot.isoformat(), "code": fault.code.value},
                once=f"run_failed:{name}:{slot.isoformat()}:{fault.code.value}",
            )
            return  # slot stays open: the next tick tries again
        self._state.set_value(f"{key}:done", slot.isoformat())
        report.ran.append((name, slot.isoformat()))
        if not result.completed:
            report.problems.append(f"{name}@{slot.isoformat()}: halted")
