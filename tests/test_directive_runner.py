import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from jangus.directives.models import Task
from jangus.directives.runner import HaltInfo, TaskEvidence
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import read_alerts
from jangus.kernel.budgets import Dimension
from tests.agent_harness import Harness, make_spec, reply

TOOLS = Path(__file__).resolve().parent.parent / "tools" / "taskmaster"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
tasks_tool = importlib.import_module("tasks")

READ = ("file.read", {"path": "/work/a"})


def ktask(
    tid: int, title: str, steps: list[tuple[str, dict[str, Any]]], deps: tuple[int, ...] = ()
) -> dict[str, Any]:
    return {
        "id": tid,
        "title": title,
        "dependencies": list(deps),
        "testStrategy": "every step verified",
        "runner": "kernel",
        "tools": sorted({t for t, _ in steps}),
        "steps": [{"tool": t, "args": a} for t, a in steps],
    }


def mtask(
    tid: int, title: str, tools: list[str], deps: tuple[int, ...] = (), **extra: Any
) -> dict[str, Any]:
    return {
        "id": tid,
        "title": title,
        "dependencies": list(deps),
        "testStrategy": "kernel check",
        "tools": tools,
        **extra,
    }


def statuses(report: Any) -> dict[int, str]:
    return dict(report.statuses)


def always(result: bool) -> Any:
    def verify(task: Task, evidence: TaskEvidence) -> bool:
        return result

    return verify


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# ---- B29 / B27: kernel-run tasks cost nothing; standing directives skip the orchestrator ----


def test_kernel_run_directive_completes_with_zero_model_calls(h: Harness) -> None:
    d = h.issue_directive(
        [
            ktask(1, "write", [("file.write", {"path": "/work/a"})]),
            ktask(2, "read back", [("file.read", {"path": "/work/a"})], deps=(1,)),
        ]
    )
    asked: list[str] = []

    class Gate:
        def allow(self, directive_id: str, reason: str) -> bool:
            asked.append(reason)
            return True

    report = h.directives.run(d, gate=Gate())
    assert report.completed and statuses(report) == {1: "done", 2: "done"}
    assert h.provider.requests == [] and h.escalation.requests == []  # B29: no model call
    assert h.events("model_call") == []  # and so zero tokens recorded
    assert asked == [] and not report.escalated  # B27: nobody was escalated to
    assert h.world.calls == ["write:/work/a", "read:/work/a"]
    assert h.budgets.status("directive:d1")[Dimension.TOKENS].spent == 0


def test_report_is_a_fixed_template_written_without_a_model(h: Harness) -> None:
    d = h.issue_directive([ktask(1, "write", [("file.write", {"path": "/work/a"})])])
    report = h.directives.run(d)
    assert "Result: completed" in report.text and "task 1 [done] write" in report.text
    assert "escalated to orchestrator: no" in report.text
    assert h.provider.requests == []


# ---- B25: kernel-owned status ----


def test_model_claiming_done_does_not_set_done_without_a_passing_check(h: Harness) -> None:
    h.provider.script += [reply(READ, done=True)] * 3
    h.escalation.script += [reply(("file.read", {"path": "/work/b"}), done=True)]
    d = h.issue_directive([mtask(1, "tidy", ["file.read"])])
    report = h.directives.run(d, verifiers={1: always(False)})
    assert not report.completed and statuses(report)[1] == "deferred"
    assert all(t != "done" for t in statuses(report).values())


def test_done_is_set_only_after_the_verifier_passes(h: Harness) -> None:
    h.provider.script.append(reply(READ, done=True))
    d = h.issue_directive([mtask(1, "tidy", ["file.read"])])
    report = h.directives.run(d, verifiers={1: always(True)})
    assert report.completed and statuses(report) == {1: "done"}
    done = [e for e in h.events("task_status") if e["status"] == "done"]
    assert done and done[0]["verified"] is True


def test_model_task_without_a_kernel_check_goes_to_review_and_halts(h: Harness) -> None:
    """DIRECTIVES.md rule 4: no machine check available means review for the owner."""
    h.provider.script.append(reply(READ, done=True))
    d = h.issue_directive(
        [mtask(1, "tidy", ["file.read"]), mtask(2, "next", ["file.read"], deps=(1,))]
    )
    report = h.directives.run(d)
    assert statuses(report) == {1: "review", 2: "pending"}
    assert report.halted is not None and report.halted.reason == "no_kernel_check_for_this_task"
    assert len(h.provider.requests) == 1  # task 2 never started


def test_out_of_task_tool_call_is_rejected(h: Harness) -> None:
    h.provider.script += [
        reply(("file.write", {"path": "/work/x"})),  # the task only allows file.read
        reply(READ, done=True),
    ]
    d = h.issue_directive([mtask(1, "read only", ["file.read"])])
    h.directives.run(d, verifiers={1: always(True)})
    assert h.world.calls == ["read:/work/a"]  # the write never ran
    assert h.events("attempt_failed")[0]["reasons"] == ["rejected:tool_not_allowed"]


def test_next_task_sees_only_verified_dependency_results(h: Harness) -> None:
    h.provider.script.append(reply(READ, done=True))
    d = h.issue_directive(
        [
            ktask(1, "write", [("file.write", {"path": "/work/a"})]),
            mtask(2, "check", ["file.read"], deps=(1,)),
        ]
    )
    h.directives.run(d, verifiers={2: always(True)})
    prompt = h.provider.requests[0].user
    assert '<data label="verified-result-task-1">' in prompt
    assert "1 verified action(s)" in prompt


# ---- B26: stop rules ----


def test_budget_exhaustion_halts_the_directive_and_alerts(h: Harness) -> None:
    h.provider.script += [reply(READ)] * 5
    d = h.issue_directive(
        [mtask(1, "loop", ["file.read"])],
        budget={"calls": 1, "tokens": 50_000, "wall_seconds": 300, "spend_usd": 0.5},
    )
    report = h.directives.run(d, verifiers={1: always(True)})
    assert report.halted is not None and report.halted.reason == "budget_exhausted"
    assert len(h.provider.requests) == 1
    codes = [a.code for a in read_alerts(h.ledger)]
    assert "directive_halted" in codes and "budget_reached" in codes


def test_unresolved_effect_halts_the_directive(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(extra_tools=("file.flaky",)))
    h.provider.script.append(reply(("file.flaky", {"path": "/work/a"}), done=True))
    d = h.issue_directive(
        [mtask(1, "risky", ["file.flaky"]), mtask(2, "after", ["file.read"], deps=(1,))]
    )
    report = h.directives.run(d, verifiers={1: always(True)})
    assert report.halted is not None and report.halted.reason == "unresolved_effect"
    assert statuses(report) == {1: "deferred", 2: "pending"}
    assert any(a.code == "directive_halted" for a in read_alerts(h.ledger))


def test_kernel_run_step_that_does_not_verify_halts(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(extra_tools=("file.flaky",)))
    d = h.issue_directive([ktask(1, "risky", [("file.flaky", {"path": "/work/a"})])])
    report = h.directives.run(d)
    assert report.halted is not None and report.halted.reason == "unresolved_effect"
    assert h.provider.requests == []  # kernel-run: no step-up, no model


def test_t3_call_waits_for_approval_and_halts(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(max_tier="T3"), with_approvals=True)
    h.provider.script.append(reply(("file.delete", {"path": "/work/a"}), done=True))
    d = h.issue_directive([mtask(1, "delete", ["file.delete"])])
    report = h.directives.run(d, verifiers={1: always(True)})
    assert report.halted is not None and report.halted.reason == "awaiting_t3_approval"
    assert h.world.calls == []  # nothing deleted
    assert h.approvals is not None and len(h.approvals.list("pending")) == 1


def test_rejected_t3_approval_halts_the_directive(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(max_tier="T3"), with_approvals=True)

    def owner_rejects_then_model_finishes(_: Any) -> str:
        assert h.approvals is not None
        h.approvals.reject(h.approvals.list("pending")[0].id, "owner")
        return reply(done=True)

    h.provider.script += [
        reply(("file.delete", {"path": "/work/a"})),
        owner_rejects_then_model_finishes,
    ]
    d = h.issue_directive([mtask(1, "delete", ["file.delete"])])
    report = h.directives.run(d, verifiers={1: always(True)})
    assert report.halted is not None and report.halted.reason == "t3_approval_rejected"
    assert h.world.calls == []


def test_two_failed_attempts_halt_when_the_spec_has_no_step_up(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(escalation=False))
    h.provider.script += [
        reply(READ, done=True),
        reply(("file.read", {"path": "/work/b"}), done=True),
    ]
    d = h.issue_directive([mtask(1, "tidy", ["file.read"])])
    report = h.directives.run(d, verifiers={1: always(False)})
    assert report.halted is not None and "attempts exhausted" in report.halted.reason
    assert len(h.provider.requests) == 2 and h.escalation.requests == []


def test_ladder_with_step_up_stops_after_three(h: Harness) -> None:
    h.provider.script += [
        reply(READ, done=True),
        reply(("file.read", {"path": "/work/b"}), done=True),
    ]
    h.escalation.script += [reply(("file.read", {"path": "/work/c"}), done=True)]
    d = h.issue_directive([mtask(1, "tidy", ["file.read"])])
    report = h.directives.run(d, verifiers={1: always(False)})
    assert report.halted is not None
    assert (len(h.provider.requests), len(h.escalation.requests)) == (2, 1)


# ---- halting is final; restart and re-issue do not give a fresh start ----


def test_restart_resumes_from_the_ledger_and_does_not_redo_or_retry(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.provider.script.append(reply(READ))
    tasks_spec = [
        ktask(1, "write", [("file.write", {"path": "/work/a"})]),
        mtask(2, "loop", ["file.read"], deps=(1,)),
    ]
    d = h.issue_directive(
        tasks_spec, budget={"calls": 1, "tokens": 50_000, "wall_seconds": 300, "spend_usd": 0.5}
    )
    first = h.directives.run(d, verifiers={2: always(True)})
    assert statuses(first) == {1: "done", 2: "deferred"}
    h.state.close()

    h2 = Harness(tmp_path)  # restart: new objects over the same state and ledger files
    second = h2.directives.run(d, verifiers={2: always(True)})
    assert statuses(second) == {1: "done", 2: "deferred"}
    assert h2.provider.requests == [] and h2.world.calls == []  # nothing was redone
    assert second.halted == HaltInfo("d1", 2, "budget_exhausted")


def test_superseding_directive_inherits_consumed_attempts(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=make_spec(escalation=False))
    h.provider.script += [
        reply(READ, done=True),
        reply(("file.read", {"path": "/work/b"}), done=True),
    ]
    first = h.issue_directive([mtask(1, "tidy", ["file.read"])], "d1")
    h.directives.run(first, verifiers={1: always(False)})
    second = h.issue_directive([mtask(1, "tidy", ["file.read"])], "d2", supersedes="d1")
    report = h.directives.run(second, verifiers={1: always(True)})
    assert report.halted is not None and "attempts exhausted" in report.halted.reason
    assert len(h.provider.requests) == 2  # the re-issue made no new model call


# ---- escalation hook ----


def test_halt_calls_the_orchestrator_hook_only_when_flagged_and_allowed(h: Harness) -> None:
    calls: list[HaltInfo] = []

    class Gate:
        def __init__(self, allow: bool) -> None:
            self._allow = allow

        def allow(self, directive_id: str, reason: str) -> bool:
            return self._allow

    def run_with(escalate: bool, allow: bool, did: str) -> bool:
        h.provider.script.append(reply(READ, done=True))
        d = h.issue_directive([mtask(1, "x", ["file.read"])], did, escalate=escalate)
        return h.directives.run(d, gate=Gate(allow), on_escalate=calls.append).escalated

    assert run_with(True, True, "e1") is True and len(calls) == 1
    assert run_with(True, False, "e2") is False  # daily cap reached: queued for the owner
    assert run_with(False, True, "e3") is False  # directive did not ask for escalation
    assert len(calls) == 1


# ---- guards ----


def test_unissued_or_mismatched_directive_is_refused(h: Harness) -> None:
    from jangus.directives.models import Directive

    d = h.issue_directive([ktask(1, "write", [("file.write", {"path": "/work/a"})])])
    changed: Directive = d.model_copy(update={"goal": "something else"})
    with pytest.raises(RuntimeFault) as info:
        h.directives.run(changed)  # the hash no longer matches what was issued
    assert info.value.code is FaultCode.DIRECTIVE_INVALID


# ---- tasks.py validate lints directive files ----


def test_validate_lints_a_directive_file(tmp_path: Path) -> None:
    good = {
        "goal": "g",
        "assignee": "a",
        "tasks": [
            ktask(1, "k", [("file.read", {"path": "/x"})]),
            mtask(2, "m", ["file.read"], deps=(1,)),
        ],
    }
    path = tmp_path / "d.json"
    import json

    path.write_text(json.dumps(good), encoding="utf-8")
    tasks_list = tasks_tool.load_directive(str(path))
    assert tasks_tool.validate(tasks_list, directive=True)[0] == []

    bad = dict(good)
    bad["tasks"] = [
        {**good["tasks"][0], "steps": []},
        {**good["tasks"][1], "steps": [{"tool": "x", "args": {}}]},
    ]
    errors = tasks_tool.validate(bad["tasks"], directive=True)[0]
    assert any("needs non-empty steps" in e for e in errors)
    assert any("must not have steps" in e for e in errors)
    cyc = [{**good["tasks"][0], "dependencies": [2]}, good["tasks"][1]]
    assert tasks_tool.validate(cyc, directive=True)[0]  # forward dependency is an error
    assert tasks_tool.load_directive(str(tmp_path / "missing.json")) is None
