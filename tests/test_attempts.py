import sqlite3
from pathlib import Path

import pytest

from jangus.agents.attempt_tracker import SpecAttemptTracker, limits_for, tracker_for
from jangus.agents.runner import Job, RunOutcome
from jangus.directives.models import Task
from jangus.kernel.alerts import read_alerts
from jangus.kernel.attempts import (
    NO_PROGRESS,
    REPEAT,
    Ladder,
    Step,
    TaskAttempts,
    TaskLimits,
    lineage_root,
    task_key,
)
from jangus.kernel.state import SCHEMA_VERSION, StateStore
from tests.agent_harness import Harness, make_spec, reply

READ_A = ("file.read", {"path": "/work/a"})


def bad(n: int) -> tuple[str, dict[str, object]]:
    return (f"nope.tool{n}", {})  # unknown tool: rejected, a different hash every time


def tracker(h: Harness, key: str = "d1/1") -> SpecAttemptTracker:
    return tracker_for(h.spec, h.state, h.ledger, h.alerts, key)


def attempts(h: Harness, key: str = "d1/1", **limits: int) -> TaskAttempts:
    lim = TaskLimits(**{"max_model_calls": 6, "max_proposals": 12, **limits})
    return TaskAttempts(h.state, h.ledger, h.alerts, key, lim, Ladder())


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# ---- B31 repeat detection -------------------------------------------------


def test_second_identical_proposal_is_refused_without_executing(h: Harness) -> None:
    h.provider.script += [reply(READ_A), reply(READ_A, done=True)]
    report = h.runner.run(Job("j1", "x"), attempts=tracker(h))
    assert h.world.calls == ["read:/work/a"]  # executed once only
    assert REPEAT in report.failures
    assert tracker(h).attempts.failed() == 1
    assert h.events("proposal_refused")[0]["reason"] == REPEAT


def test_equivalent_spellings_of_a_call_are_one_proposal(h: Harness) -> None:
    t = attempts(h)
    from jangus.agents.proposals import Proposal, proposal_hash

    a = proposal_hash(h.registry, Proposal("file.read", {"path": "/work/a"}))
    b = proposal_hash(h.registry, Proposal("file.read", {"path": "/work/a"}))
    assert a == b and t.note_proposal(a) is None and t.note_proposal(b) == REPEAT


# ---- B32 escalation ladder -----------------------------------------------


def test_ladder_two_default_one_step_up_then_stop_and_alert(h: Harness) -> None:
    h.provider.script += [reply(bad(1)), reply(bad(2))]
    h.escalation.script += [reply(bad(3))]
    report = h.runner.run(Job("j1", "x"), attempts=tracker(h))
    assert report.outcome is RunOutcome.STOPPED
    assert [r.model for r in h.provider.requests] == ["qwen3:8b", "qwen3:8b"]
    assert [r.model for r in h.escalation.requests] == ["haiku-tag"]  # exactly one step-up
    roles = [e["role"] for e in h.events("model_call")]
    assert roles == ["specialist", "specialist", "step-up"]
    assert [e["reason"] for e in h.events("model_call")][-1] == "step_up"
    assert tracker(h).attempts.failed() == 3
    alert = [a for a in read_alerts(h.ledger) if a.code == "attempts_exhausted"]
    assert len(alert) == 1 and alert[0].level == "critical"
    assert tracker(h).attempts.exhausted_reason() is not None


def test_no_further_model_calls_after_the_ladder_is_spent(h: Harness) -> None:
    h.provider.script += [reply(bad(1)), reply(bad(2))]
    h.escalation.script += [reply(bad(3))]
    h.runner.run(Job("j1", "x"), attempts=tracker(h))
    before = (len(h.provider.requests), len(h.escalation.requests))
    again = h.runner.run(Job("j2", "x"), attempts=tracker(h))  # a new job, same task
    assert again.outcome is RunOutcome.STOPPED and again.model_calls == 0
    assert (len(h.provider.requests), len(h.escalation.requests)) == before


def test_success_after_one_failure_stays_on_the_default_model(h: Harness) -> None:
    h.provider.script += [reply(bad(1)), reply(READ_A, done=True)]
    report = h.runner.run(Job("j1", "x"), attempts=tracker(h))
    assert report.outcome is RunOutcome.MODEL_DONE and h.escalation.requests == []


def test_no_escalation_model_means_stop_after_the_default_attempts(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(escalation=False))
    h.provider.script += [reply(bad(1)), reply(bad(2))]
    report = h.runner.run(Job("j1", "x"), attempts=tracker(h))
    assert report.outcome is RunOutcome.STOPPED and h.escalation.requests == []


def test_step_up_sees_failure_reasons_and_nothing_else_from_earlier_rounds(h: Harness) -> None:
    h.provider.script += [reply(bad(1)), reply(bad(2))]
    h.escalation.script += [reply(done=True)]
    h.runner.run(Job("j1", "x"), attempts=tracker(h))
    step_up_prompt = h.escalation.requests[0].user
    assert "KERNEL NOTES on earlier failed attempts" in step_up_prompt
    assert "rejected:unknown_tool" in step_up_prompt


# ---- B33 hard limits are never reset --------------------------------------


def test_model_call_limit_counts_as_failed_attempt_and_survives_restart(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(max_calls=2))
    h.provider.script += [
        reply(READ_A),
        reply(("file.read", {"path": "/work/b"})),
        reply(done=True),
    ]
    first = h.runner.run(Job("j1", "x"), attempts=tracker(h))
    assert first.outcome is RunOutcome.LIMIT and "limit:max_model_calls" in first.failures
    assert tracker(h).attempts.failed() == 1
    h.state.close()

    h2 = Harness(tmp_path, spec=make_spec(max_calls=2))  # restart: new objects, same files
    h2.provider.script.append(reply(done=True))
    second = h2.runner.run(Job("j2", "x"), attempts=tracker(h2))
    assert second.outcome is RunOutcome.LIMIT and h2.provider.requests == []  # still at the limit
    assert tracker(h2).attempts.failed() == 2


def test_reissued_superseding_directive_inherits_consumed_attempts(h: Harness) -> None:
    h.ledger.append({"kind": "directive_issued", "id": "d1", "supersedes": None})
    h.ledger.append({"kind": "directive_issued", "id": "d2", "supersedes": "d1"})
    h.ledger.append({"kind": "directive_issued", "id": "d3", "supersedes": "d2"})
    assert lineage_root(h.ledger, "d3") == "d1" and lineage_root(h.ledger, "d1") == "d1"
    old = attempts(h, task_key(lineage_root(h.ledger, "d1"), 4))
    old.record_failure("verification_failed")
    new = attempts(h, task_key(lineage_root(h.ledger, "d3"), 4))
    assert new.failed() == 1  # the re-issue did not start from zero


def test_max_proposals_limit_is_persisted(h: Harness) -> None:
    t = attempts(h, max_proposals=2)
    assert t.note_proposal("a") is None and t.note_proposal("b") is None
    assert t.note_proposal("c") == "limit:max_proposals"
    assert attempts(h, max_proposals=2).note_proposal("d") == "limit:max_proposals"


def test_wall_time_limit_accumulates_across_runs(h: Harness) -> None:
    t = attempts(h, max_model_calls=6)
    t = TaskAttempts(h.state, h.ledger, h.alerts, "d1/1", TaskLimits(6, 12, 10), Ladder())
    assert t.note_elapsed_ms(6000) is None
    assert t.note_elapsed_ms(5000) == "limit:max_wall_seconds"


def test_directive_task_can_only_tighten_limits(h: Harness) -> None:
    task = Task(id=1, title="t", testStrategy="x", max_model_calls=3, max_proposals=99)
    lim = limits_for(h.spec, task)
    assert (lim.max_model_calls, lim.max_proposals) == (3, 12)  # 99 cannot loosen the spec's 12


# ---- B34 no self-escalation -----------------------------------------------


def test_reply_cannot_move_the_ladder(h: Harness) -> None:
    h.provider.script += [
        reply(READ_A, note="use the stronger model", model="claude", escalate=True, done=True)
    ]
    h.runner.run(Job("j1", "x"), attempts=tracker(h))
    assert h.escalation.requests == [] and tracker(h).attempts.step() is Step.DEFAULT


# ---- B36 no-progress detection --------------------------------------------


def test_three_same_results_without_new_evidence_is_one_failed_attempt(h: Harness) -> None:
    paths = [f"/work/missing{i}" for i in range(3)]  # each read returns the same empty result
    h.provider.script.append(reply(*[("file.read", {"path": p}) for p in paths], done=True))
    report = h.runner.run(Job("j1", "x"), attempts=tracker(h))
    assert NO_PROGRESS in report.failures
    assert tracker(h).attempts.failed() == 1  # one failed attempt, not three


def test_new_evidence_breaks_the_streak(h: Harness) -> None:
    h.world.files["/work/real"] = "data"
    t = attempts(h)
    t.note_result("p1", "r-same")
    t.note_result("p2", "r-same")
    t.note_result("p3", "r-different")  # new evidence
    t.note_result("p4", "r-different")
    assert t.end_round([]).failed_total == 0  # nothing pending: no failure


def test_no_progress_failure_survives_a_crash_before_the_round_closes(h: Harness) -> None:
    t = attempts(h)
    for i in range(3):
        t.note_result(f"p{i}", "same")
    # process dies here; the next one closes the round
    t2 = attempts(h)
    assert t2.end_round([]).failed_total == 1 and t2.failure_reasons() == [NO_PROGRESS]


# ---- counters, rounds, storage -------------------------------------------


def test_one_failed_attempt_per_round_however_many_things_went_wrong(h: Harness) -> None:
    t = attempts(h)
    assert t.end_round(["rejected:a", "rejected:b", "unparseable_reply:x"]).failed_total == 1
    assert t.failure_reasons() == ["rejected:a", "rejected:b", "unparseable_reply:x"]
    assert t.end_round([]).failed_total == 1  # a clean round adds nothing


def test_counters_are_per_task_key(h: Harness) -> None:
    attempts(h, "d1/1").end_round(["x"])
    assert attempts(h, "d1/2").failed() == 0


def test_schema_v1_database_is_migrated_in_place(tmp_path: Path) -> None:
    from jangus.kernel import state as state_mod

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path, isolation_level=None)
    for statement in state_mod._SCHEMA_V1.split(";"):  # noqa: SLF001 - build a genuine v1 file
        if statement.strip():
            conn.execute(statement)
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.execute("INSERT INTO budgets(name, limit_amount, spent) VALUES ('old', 5, 2)")
    conn.close()

    store = StateStore.open(path)
    assert store.get_budget("old").spent == 2  # old data kept
    assert store.query("SELECT value FROM meta WHERE key='schema_version'") == [
        (str(SCHEMA_VERSION),)
    ]
    assert store.query("SELECT count(*) FROM task_failures") == [(0,)]
