"""Distinct limited advisory roles and authoritative controller verification."""

import hashlib
import json
from pathlib import Path

import pytest

from jangus.agents.context import HandoffStore
from jangus.agents.orchestration import run_coordinated_workflow
from jangus.agents.provider import ModelReply
from jangus.agents.workflow import PlanningFailure
from jangus.config import JangusConfig, PathsConfig, PermissionsConfig
from jangus.execution.contracts import FileExpectation, Manifest
from jangus.execution.controller import Controller

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fixture(tmp_path):
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / "synthetic"
    (workspace / "input").mkdir(parents=True)
    (workspace / "out").mkdir()
    content = b"synthetic-file-no-private-data\n"
    (workspace / "input/test.txt").write_bytes(content)
    manifest = Manifest(
        files=[
            FileExpectation(
                source="input/test.txt",
                destination="out/test.txt",
                sha256=hashlib.sha256(content).hexdigest(),
            )
        ],
        report="out/report.json",
    )
    config = JangusConfig(
        paths=PathsConfig(
            data_dir=str(tmp_path / "data"),
            workspace_dir=str(workspace_root),
        ),
        permissions=PermissionsConfig(allowed_tools=["file.copy", "file.report"]),
    )
    controller = Controller(config)
    try:
        yield controller, workspace, manifest
    finally:
        controller.close()


class RoleModel:
    def __init__(self, *, changes=None, on_role=None):
        self.assignments = []
        self.messages = []
        self.changes = changes or {}
        self.on_role = on_role

    def generate(self, messages, **_kwargs):
        system, user = messages
        # User message carries only serialized assignment and its exact digest.
        raw, marker = user.content.split("\nassignment_sha256=", 1)
        assignment = json.loads(raw)
        sha = hashlib.sha256(raw.encode()).hexdigest()
        assert marker == sha
        role = assignment["role"]
        self.assignments.append(assignment)
        self.messages.append((system.content, user.content))
        if self.on_role is not None:
            self.on_role(role)
        reply = {
            "role": role,
            "decision": "stop"
            if role == "diagnostician"
            else ("verified" if role == "verifier" else "ready"),
            "action_ids": assignment["focus_action_ids"],
            "assignment_sha256": sha,
        }
        reply.update(self.changes.get(role, {}))
        return ModelReply(
            value=reply,
            reported_prompt_tokens=42,
            reported_output_tokens=9,
        )


def test_complete_roles_isolated_verified_with_handoff(fixture):
    controller, workspace, manifest = fixture
    model = RoleModel()
    outcome = run_coordinated_workflow(controller, model, workspace, manifest, ROOT)
    assert outcome.controller_state == "succeeded"
    assert outcome.independent_verification is True
    assert outcome.review_status == "verified"
    assert outcome.diagnosis_status == "not_run"
    assert outcome.model_calls == 3
    assert [r.role for r in outcome.roles] == ["planner", "worker", "verifier"]
    assert all(r.reported_prompt_tokens == 42 for r in outcome.roles)
    assert all(r.estimated_prompt_tokens > 0 for r in outcome.roles)
    assert len({r.assignment_sha256 for r in outcome.roles}) == 3
    assert (workspace / "out/test.txt").read_bytes() == b"synthetic-file-no-private-data\n"
    assert (workspace / "out/report.json").read_bytes() == manifest.report_bytes()
    assert all("input/test.txt" not in str(msg) for msg in model.messages)
    assert model.assignments[0]["status"] is None
    assert model.assignments[0]["scope"] == "one_owner_manifest_workspace"
    assert model.assignments[0]["remaining_budget"]["remaining_dispatches"] >= 2
    assert all(a["expected_sha256"] for a in model.assignments[0]["actions"])
    assert model.assignments[1]["status"] is None
    assert model.assignments[2]["status"]["state"] == "succeeded"
    handoff = HandoffStore(Path(controller.config.paths.data_dir) / "handoffs")
    assert handoff.rehydrate(controller, outcome.run_id, outcome.skill_sha256)["dispatches"] == 2


def test_worker_rejection_blocks_effect_and_preserves_cancelled_run(fixture):
    controller, workspace, manifest = fixture
    model = RoleModel(changes={"worker": {"decision": "reject"}})
    with pytest.raises(PlanningFailure, match="worker_rejected"):
        run_coordinated_workflow(controller, model, workspace, manifest, ROOT)
    assert not (workspace / "out/test.txt").exists()
    row = controller.store.db.execute("SELECT id,state,dispatches FROM runs").fetchone()
    assert row["state"] == "cancelled"
    assert row["dispatches"] == 0
    handoff = HandoffStore(Path(controller.config.paths.data_dir) / "handoffs")
    assert (
        handoff.rehydrate(
            controller,
            row["id"],
            {
                "file-workflow": hashlib.sha256(
                    (ROOT / "skills/file-workflow/SKILL.md").read_bytes()
                ).hexdigest()
            },
        )["state"]
        == "cancelled"
    )


@pytest.mark.parametrize(
    "tampered",
    [
        {"action_ids": ["new_admin_action"]},
        {"assignment_sha256": "0" * 64},
        {"role": "verifier"},
        {"tools": ["shell.exec"]},
        {"decision": "verified"},
    ],
)
def test_pre_execution_spoofed_role_rejected(fixture, tampered):
    controller, workspace, manifest = fixture
    model = RoleModel(changes={"worker": tampered})
    with pytest.raises(PlanningFailure):
        run_coordinated_workflow(controller, model, workspace, manifest, ROOT)
    assert not (workspace / "out/test.txt").exists()
    row = controller.store.db.execute("SELECT state,dispatches FROM runs").fetchone()
    assert row["state"] == "cancelled"
    assert row["dispatches"] == 0


def test_post_effect_advisor_disagreement_cannot_rewrite_truth(fixture):
    controller, workspace, manifest = fixture
    model = RoleModel(changes={"verifier": {"decision": "disagree"}})
    outcome = run_coordinated_workflow(controller, model, workspace, manifest, ROOT)
    assert outcome.controller_state == "succeeded"
    assert outcome.independent_verification is True
    assert outcome.review_status == "disagree"
    assert controller.status(outcome.run_id)["state"] == "succeeded"
    assert all(
        action["state"] == "verified" for action in controller.status(outcome.run_id)["actions"]
    )


def test_post_effect_invalid_model_does_not_hide_verified_controller_outcome(fixture):
    controller, workspace, manifest = fixture
    model = RoleModel(changes={"verifier": {"action_ids": ["unknown"]}})
    outcome = run_coordinated_workflow(controller, model, workspace, manifest, ROOT)
    assert outcome.review_status == "unavailable"
    assert outcome.model_calls == 3  # Includes failed/unusable advisory calls.
    assert outcome.independent_verification is True
    assert controller.status(outcome.run_id)["state"] == "succeeded"


def test_diagnostician_receives_safe_failure_state_without_retrying(fixture):
    controller, workspace, manifest = fixture

    def mutate(role):
        if role == "worker":
            (workspace / "input/test.txt").write_bytes(b"changed-after-submission\n")

    model = RoleModel(on_role=mutate)
    outcome = run_coordinated_workflow(controller, model, workspace, manifest, ROOT)
    assert outcome.controller_state == "failed"
    assert outcome.review_status == "not_run"
    assert outcome.diagnosis_status == "stop"
    assert [r.role for r in outcome.roles] == ["planner", "worker", "diagnostician"]
    assert model.assignments[-1]["status"]["state"] == "failed"
    assert controller.status(outcome.run_id)["dispatches"] <= 2
    assert not (workspace / "out/test.txt").exists()
    assert controller.status(outcome.run_id)["state"] == "failed"
    assert not any("changed-after-submission" in str(msg) for msg in model.messages)
