"""Read-only release gates never mistake green CI for owner acceptance."""

import json
from pathlib import Path

import pytest

from jangus.release.readiness import ReadinessFault, parse_backlog, report

ROOT = Path(__file__).resolve().parents[2]


def backlog():
    return json.loads((ROOT / ".taskmaster/tasks/tasks.json").read_text())


def _cycle(data):
    for row in data["master"]["tasks"]:
        row["status"] = "pending"
    data["master"]["tasks"][0]["dependencies"] = ["30"]


def test_current_roadmap_is_explicitly_not_accepted():
    status = report(backlog())
    assert status["gates"]["foundation"]["taskmaster_complete"]
    assert status["gates"]["foundation"]["done"] == 11
    assert not status["gates"]["local_mvp"]["taskmaster_complete"]
    assert status["gates"]["local_mvp"]["outstanding_task_ids"][0] == 12
    assert status["next_unblocked_task_ids"] == [12]
    assert status["owner_acceptance"] == "not_evaluated_by_backlog_check"
    assert status["live_vps_verified"] == "not_evaluated"
    assert not status["deployment_or_merges_performed"]


@pytest.mark.parametrize(
    ("edit", "error"),
    [
        (lambda x: x["master"]["tasks"].append(x["master"]["tasks"][0]), "backlog_invalid"),
        (
            lambda x: x["master"]["tasks"][12].update({"status": "done"}),
            "completed_dependency_incomplete",
        ),
        (
            lambda x: x["master"]["tasks"][11].update({"dependencies": ["9999"]}),
            "dependency_missing",
        ),
        (
            _cycle,
            "dependency_cycle",
        ),
        (
            lambda x: x["master"]["tasks"][12].update({"id": "12"}),
            "backlog_invalid",
        ),
        (lambda x: x["master"]["tasks"][0].update({"status": "pretend-done"}), "backlog_invalid"),
        (lambda x: x["master"]["tasks"].pop(), "required_tasks_missing"),
    ],
)
def test_invalid_backlogs_fail_closed(edit, error):
    data = backlog()
    edit(data)
    with pytest.raises(ReadinessFault, match=error):
        parse_backlog(data)


def test_backlog_completion_does_not_assert_live_evidence():
    data = backlog()
    for row in data["master"]["tasks"]:
        row["status"] = "done"
    value = report(data)
    assert value["gates"]["local_mvp"]["taskmaster_complete"]
    assert value["owner_acceptance"] == "not_evaluated_by_backlog_check"
    assert value["live_vault_verified"] == "not_evaluated"
    assert value["provider_access_verified"] == "not_evaluated"
