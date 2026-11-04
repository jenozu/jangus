from datetime import datetime
from pathlib import Path

import pytest

from jangus.agents.report import build_daily_report
from jangus.agents.spec import LoadedSpec, parse_spec
from jangus.agents.spec_registry import SpecApprovalRegistry
from jangus.directives.runner import DirectiveReport
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import AlertQueue, read_alerts
from jangus.kernel.ledger import Ledger
from jangus.kernel.lock import RunLock
from jangus.kernel.state import StateStore
from jangus.scheduler import Cron, Scheduler, latest_due

SPEC = """\
schema_version: 1
name: nightly
role: "Runs at night."
model: {{provider: ollama, name: "qwen3:8b", max_tokens: 256}}
tools: [file.list]
max_tier: T1
triggers: [{{cron: "{cron}"}}]
budget: {{calls: 5, tokens: 1000, wall_seconds: 60, spend_usd: 0}}
"""


def dt(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute)


# ---- cron ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cron", "when", "expected"),
    [
        ("0 2 * * *", dt(3, 2, 0), True),
        ("0 2 * * *", dt(3, 2, 1), False),
        ("*/15 * * * *", dt(3, 7, 45), True),
        ("*/15 * * * *", dt(3, 7, 46), False),
        ("30 1-3 * * *", dt(3, 3, 30), True),
        ("30 1-3 * * *", dt(3, 4, 30), False),
        ("0 9 * * 1", dt(2, 9, 0), True),  # 2026-11-02 is a Monday
        ("0 9 * * 1", dt(3, 9, 0), False),
        ("0 9 * * 0", dt(1, 9, 0), True),  # Sunday as 0
        ("0 9 * * 7", dt(1, 9, 0), True),  # and as 7
        ("0 9 3 * 1", dt(3, 9, 0), True),  # day-of-month OR weekday when both are set
        ("0 9 1,15 * *", dt(15, 9, 0), True),
        ("0 9 * 12 *", dt(3, 9, 0), False),
    ],
)
def test_cron_matching(cron: str, when: datetime, expected: bool) -> None:
    assert Cron.parse(cron).matches(when) is expected


@pytest.mark.parametrize(
    "bad", ["", "* * * *", "61 * * * *", "* 24 * * *", "*/0 * * * *", "a * * * *"]
)
def test_bad_cron_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        Cron.parse(bad)


def test_latest_due_coalesces_missed_slots_into_one() -> None:
    cron = Cron.parse("0 2 * * *")
    assert latest_due(cron, dt(1, 12), dt(4, 9)) == dt(4, 2)  # three nights missed: one slot
    assert latest_due(cron, dt(4, 2), dt(4, 9)) is None  # that slot is already done
    assert latest_due(cron, dt(3, 12), dt(3, 13)) is None  # nothing due yet


def test_lookback_is_bounded() -> None:
    cron = Cron.parse("0 2 * * *")
    assert latest_due(cron, dt(1), datetime(2027, 1, 1, 12)) is not None


# ---- lock -----------------------------------------------------------------------------


def test_second_lock_holder_is_refused_until_release(tmp_path: Path) -> None:
    a, b = RunLock(tmp_path / "l"), RunLock(tmp_path / "l")
    assert a.acquire() and not b.acquire()
    a.release()
    assert b.acquire()
    b.release()
    assert not (tmp_path / "l").exists()


def test_stale_lock_is_taken_over(tmp_path: Path) -> None:
    now = [1000.0]
    old = RunLock(tmp_path / "l", stale_seconds=100, clock=lambda: now[0])
    assert old.acquire()  # then the process dies without releasing
    now[0] += 50
    fresh = RunLock(tmp_path / "l", stale_seconds=100, clock=lambda: now[0])
    assert not fresh.acquire()
    now[0] += 200
    assert fresh.acquire() and fresh.took_over_stale


# ---- scheduler (B13) -------------------------------------------------------------------


class Rig:
    def __init__(self, tmp: Path, cron: str = "0 2 * * *", approve: bool = True) -> None:
        self.specs = tmp / "specs"
        self.specs.mkdir()
        self.state = StateStore.open(tmp / "state.db")
        self.ledger = Ledger.open(tmp / "ledger.jsonl")
        self.alerts = AlertQueue(self.ledger, self.state)
        self.registry = SpecApprovalRegistry(tmp / "approvals.json", self.ledger)
        self.path = self.specs / "nightly.yaml"
        self.path.write_text(SPEC.format(cron=cron), encoding="utf-8")
        self.loaded = parse_spec(self.path.read_bytes())
        if approve:
            self.registry.approve(self.loaded)
        self.lock = RunLock(tmp / "tick.lock")
        self.calls: list[tuple[str, datetime]] = []
        self.completed = True
        self.fail: RuntimeFault | None = None
        self.scheduler = Scheduler(
            specs_dir=self.specs,
            registry=self.registry,
            state=self.state,
            ledger=self.ledger,
            alerts=self.alerts,
            lock=self.lock,
            run_agent=self._run,
        )

    def _run(self, loaded: LoadedSpec, slot: datetime) -> DirectiveReport:
        if self.fail is not None:
            raise self.fail
        self.calls.append((loaded.spec.name, slot))
        return DirectiveReport("d", self.completed, None, (), False, "")


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_first_tick_only_records_a_baseline(rig: Rig) -> None:
    rig.scheduler.tick(dt(1, 12))
    assert rig.calls == []  # no catch-up for history before the agent was seen


def test_due_agent_runs_once_per_slot(rig: Rig) -> None:
    rig.scheduler.tick(dt(1, 12))
    result = rig.scheduler.tick(dt(2, 2, 0))
    assert rig.calls == [("nightly", dt(2, 2, 0))] and result.ran
    rig.scheduler.tick(dt(2, 2, 1))
    rig.scheduler.tick(dt(2, 2, 5))
    assert len(rig.calls) == 1  # later ticks in the same slot do nothing


def test_missed_ticks_are_coalesced(rig: Rig) -> None:
    rig.scheduler.tick(dt(1, 12))
    rig.scheduler.tick(dt(4, 9))  # the PC slept through three nights
    assert rig.calls == [("nightly", dt(4, 2, 0))]  # one run, for the newest slot
    rig.scheduler.tick(dt(4, 9, 5))
    assert len(rig.calls) == 1


def test_overlapping_tick_does_not_double_run(rig: Rig) -> None:
    rig.scheduler.tick(dt(1, 12))
    other = RunLock(rig.lock._path)  # noqa: SLF001 - another process holds the lock
    assert other.acquire()
    result = rig.scheduler.tick(dt(2, 3))
    assert result.skipped_overlap and rig.calls == []
    assert rig.ledger.entries()[-1].event["kind"] == "tick_skipped"
    other.release()
    rig.scheduler.tick(dt(2, 3, 1))
    assert len(rig.calls) == 1  # the slot was not lost


def test_crashed_run_leaves_the_slot_open_for_the_next_tick(rig: Rig) -> None:
    rig.scheduler.tick(dt(1, 12))
    rig.fail = RuntimeFault(FaultCode.LEDGER_UNAVAILABLE, "disk full")
    result = rig.scheduler.tick(dt(2, 3))
    assert result.problems and rig.calls == []
    assert any(a.code == "scheduled_run_failed" for a in read_alerts(rig.ledger))
    rig.fail = None
    rig.scheduler.tick(dt(2, 3, 10))
    assert rig.calls == [("nightly", dt(2, 2, 0))]  # resumed, same slot


def test_halted_directive_is_not_rerun_by_later_ticks(rig: Rig) -> None:
    rig.scheduler.tick(dt(1, 12))
    rig.completed = False
    result = rig.scheduler.tick(dt(2, 3))
    assert any("halted" in p for p in result.problems)
    rig.scheduler.tick(dt(2, 4))
    assert len(rig.calls) == 1  # waits for the owner, not retried every tick


def test_unapproved_or_edited_spec_does_not_run(tmp_path: Path) -> None:
    rig = Rig(tmp_path, approve=False)
    rig.scheduler.tick(dt(1, 12))
    rig.scheduler.tick(dt(2, 3))
    assert rig.calls == [] and any(a.code == "spec_not_approved" for a in read_alerts(rig.ledger))

    rig2_dir = tmp_path / "second"
    rig2_dir.mkdir()
    approved = Rig(rig2_dir)
    approved.path.write_text(SPEC.format(cron="0 2 * * *") + "\n# edited\n", encoding="utf-8")
    approved.scheduler.tick(dt(1, 12))
    approved.scheduler.tick(dt(2, 3))
    assert approved.calls == []  # an edited spec is inactive until re-approved


def test_unreadable_spec_is_reported_not_fatal(rig: Rig) -> None:
    (rig.specs / "broken.yaml").write_text("not: [valid", encoding="utf-8")
    result = rig.scheduler.tick(dt(1, 12))
    assert any("broken.yaml" in p for p in result.problems)


def test_manual_and_escalation_triggers_are_never_scheduled(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.path.write_text(
        SPEC.format(cron="0 2 * * *").replace(
            'triggers: [{cron: "0 2 * * *"}]', "triggers: [{manual: true}]"
        ),
        encoding="utf-8",
    )
    rig.registry.approve(parse_spec(rig.path.read_bytes()))
    rig.scheduler.tick(dt(1, 12))
    rig.scheduler.tick(dt(2, 3))
    assert rig.calls == []


def test_schedule_state_survives_a_restart(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.scheduler.tick(dt(1, 12))
    rig.scheduler.tick(dt(2, 3))
    rig.state.close()
    again = Rig.__new__(Rig)
    again.__dict__.update(rig.__dict__)
    again.state = StateStore.open(tmp_path / "state.db")
    again.scheduler = Scheduler(
        specs_dir=rig.specs,
        registry=rig.registry,
        state=again.state,
        ledger=rig.ledger,
        alerts=AlertQueue(rig.ledger, again.state),
        lock=RunLock(tmp_path / "tick.lock"),
        run_agent=rig._run,  # noqa: SLF001
    )
    again.scheduler.tick(dt(2, 3, 30))
    assert len(rig.calls) == 1


# ---- daily report (B30) -------------------------------------------------------------------


def test_daily_report_is_built_from_the_ledger(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    day = datetime.now().date().isoformat()
    rig.ledger.append({"kind": "model_call", "role": "specialist", "reason": "task", "run": 1,
                       "agent": "nightly", "task": "d/1", "input_tokens": 100,
                       "output_tokens": 20, "cached_tokens": 0, "cost_usd_micros": 5})  # fmt: skip
    rig.ledger.append({"kind": "model_call", "role": "step-up", "reason": "step_up", "run": 1,
                       "agent": "nightly", "task": "d/1", "input_tokens": 200,
                       "output_tokens": 40, "cached_tokens": 0, "cost_usd_micros": 9})  # fmt: skip
    rig.ledger.append(
        {"kind": "attempt_failed", "task": "d/1", "failed_attempts": 1, "reasons": []}
    )
    rig.ledger.append({"kind": "cache_hit", "run": 1, "task": "d/2", "model": "m"})
    rig.ledger.append({"kind": "directive_halted", "directive": "d", "reason": "budget_exhausted"})
    rig.ledger.append({"kind": "directive_completed", "directive": "e", "reason": None})
    rig.alerts.raise_alert("budget_reached", "x")
    report = build_daily_report(rig.ledger, day)
    assert report.usage.totals == {"calls": 2, "tokens": 360, "cost_usd_micros": 14}
    assert report.step_ups == 1 and report.attempts == {"d/1": 1}
    assert report.cache_hits == 1 and report.estimated_tokens_saved == 180
    assert len(report.halted) == 1 and report.completed == 1
    assert report.alerts == {"budget_reached": 1}
    text = report.render()
    assert "HALTED d: budget_exhausted" in text and "an estimate, not a measurement" in text
    assert build_daily_report(rig.ledger, "1999-01-01").usage.totals["calls"] == 0


def test_tick_events_are_counted(rig: Rig) -> None:
    rig.scheduler.tick(dt(1, 12))
    rig.scheduler.tick(dt(2, 3))
    day = datetime.now().date().isoformat()
    assert build_daily_report(rig.ledger, day).ticks == 2
