from pathlib import Path
from typing import Any

import pytest

from jangus.directives.models import (
    Budget,
    Evidence,
    SpecConstraints,
    directive_hash,
    issue,
    kernel_set_status,
    parse_directive,
)
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.ledger import Ledger
from jangus.kernel.registry import Tier
from tests.fakes import FakeWorld, build_registry

SPEC = SpecConstraints(
    name="backup-maintainer",
    sha256="a" * 64,
    tools=frozenset({"file.read", "file.write"}),
    max_tier=Tier.T2,
    budget=Budget(calls=40, tokens=60000, wall_seconds=900, spend_usd=0),
    max_wall_seconds=300,
    max_input_tokens=6000,
)


def good() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "dir-1",
        "issued_by": "orchestrator",
        "assignee": "backup-maintainer",
        "spec_sha256": "a" * 64,
        "goal": "Verify backup",
        "non_goals": ["Do not delete"],
        "budget": {"calls": 10, "tokens": 1000, "wall_seconds": 100, "spend_usd": 0},
        "tasks": [
            {
                "id": 1,
                "title": "Read",
                "testStrategy": "file exists",
                "runner": "kernel",
                "tools": ["file.read"],
                "steps": [{"tool": "file.read", "args": {"path": "/work/a"}}],
            },
            {
                "id": 2,
                "title": "Write",
                "dependencies": [1],
                "testStrategy": "hash matches",
                "tools": ["file.read", "file.write"],
            },
        ],
    }


def bad(raw: dict[str, Any]) -> RuntimeFault:
    with pytest.raises(RuntimeFault) as info:
        parse_directive(raw, SPEC, build_registry(FakeWorld()))
    assert info.value.code is FaultCode.DIRECTIVE_INVALID
    return info.value


def test_valid_directive_parses() -> None:
    d = parse_directive(good(), SPEC, build_registry(FakeWorld()))
    assert [t.id for t in d.tasks] == [1, 2]


def test_tool_outside_spec_rejected() -> None:
    raw = good()
    raw["tasks"][1]["tools"] = ["file.delete"]
    assert "outside the spec" in bad(raw).message


def test_wrong_spec_hash_or_assignee_rejected() -> None:
    raw = good()
    raw["spec_sha256"] = "b" * 64
    bad(raw)
    raw = good()
    raw["assignee"] = "someone-else"
    bad(raw)


@pytest.mark.parametrize("deps", [[2], [3], [1]])
def test_forward_self_and_missing_dependencies_rejected(deps: list[int]) -> None:
    raw = good()
    raw["tasks"][0]["dependencies"] = deps  # task 1 may not depend on anything
    bad(raw)


def test_duplicate_ids_rejected() -> None:
    raw = good()
    raw["tasks"][1]["id"] = 1
    bad(raw)


def test_model_supplied_status_is_ignored() -> None:
    raw = good()
    raw["tasks"][0]["status"] = "done"
    raw["tasks"][1]["status"] = "done"
    d = parse_directive(raw, SPEC)
    assert [t.status for t in d.tasks] == ["pending", "pending"]


def test_unknown_fields_rejected() -> None:
    raw = good()
    raw["tasks"][0]["log"] = "I did it"
    bad(raw)
    raw = good()
    raw["surprise"] = True
    bad(raw)


# B29
def test_kernel_step_with_tool_outside_task_tools_rejected() -> None:
    raw = good()
    raw["tasks"][0]["steps"] = [{"tool": "file.write", "args": {"path": "/work/a"}}]
    assert "not in its tools list" in bad(raw).message


def test_kernel_task_needs_steps_and_model_task_has_none() -> None:
    raw = good()
    raw["tasks"][0]["steps"] = []
    bad(raw)
    raw = good()
    raw["tasks"][1]["steps"] = [{"tool": "file.read", "args": {"path": "/work/a"}}]
    bad(raw)


def test_tier_ceiling_cannot_exceed_spec() -> None:
    raw = good()
    raw["max_tier"] = "T3"
    assert "above the spec max_tier" in bad(raw).message


def test_tool_above_directive_ceiling_rejected() -> None:
    raw = good()
    raw["max_tier"] = "T1"
    assert "above the tier ceiling" in bad(raw).message


def test_budget_cannot_exceed_spec() -> None:
    raw = good()
    raw["budget"]["calls"] = 41
    bad(raw)


# B33
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_model_calls", 7),
        ("max_proposals", 13),
        ("max_wall_seconds", 301),
        ("max_input_tokens", 6001),
    ],
)
def test_limit_loosened_above_spec_rejected(field: str, value: int) -> None:
    raw = good()
    raw["tasks"][1][field] = value
    assert "looser" in bad(raw).message


def test_limits_may_tighten() -> None:
    raw = good()
    raw["tasks"][1].update(max_model_calls=2, max_proposals=3, max_wall_seconds=60)
    parse_directive(raw, SPEC)
    raw["tasks"][1]["max_model_calls"] = 0
    bad(raw)


def test_issue_logs_hash_and_requires_pending(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.jsonl", clock=lambda: "t")
    d = parse_directive(good(), SPEC)
    digest = issue(d, SPEC, ledger)
    event = ledger.entries()[0].event
    assert event["kind"] == "directive_issued"
    assert event["hash"] == digest == directive_hash(d)
    progressed = kernel_set_status(d, 1, "in-progress", Evidence(1, False))
    with pytest.raises(RuntimeFault):
        issue(progressed, SPEC, ledger)


def test_status_only_moves_through_kernel_api_with_evidence() -> None:
    d = parse_directive(good(), SPEC)
    with pytest.raises(RuntimeFault):  # cannot skip straight to done
        kernel_set_status(d, 1, "done", Evidence(1, True))
    d1 = kernel_set_status(d, 1, "in-progress", Evidence(1, False))
    with pytest.raises(RuntimeFault):  # unverified evidence
        kernel_set_status(d1, 1, "done", Evidence(2, False))
    d2 = kernel_set_status(d1, 1, "done", Evidence(2, True))
    assert d.tasks[0].status == "pending"  # original untouched
    assert d2.tasks[0].status == "done"
    d3 = kernel_set_status(d2, 2, "in-progress", Evidence(3, False))
    assert d3.tasks[1].status == "in-progress"
    with pytest.raises(RuntimeFault):  # done is final
        kernel_set_status(d2, 1, "pending", Evidence(4, True))


def test_done_needs_dependencies_done() -> None:
    d = parse_directive(good(), SPEC)
    d = kernel_set_status(d, 2, "in-progress", Evidence(1, False))
    with pytest.raises(RuntimeFault):
        kernel_set_status(d, 2, "done", Evidence(2, True))
