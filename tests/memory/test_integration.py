"""Phase 6 integrated A14–A17 checks over the Phase 5 coordinated model path."""

import hashlib
import json
import uuid
from pathlib import Path

import pytest
import yaml

from jangus.agents.orchestration import run_coordinated_workflow
from jangus.agents.provider import ModelReply
from jangus.config import JangusConfig, PathsConfig, PermissionsConfig
from jangus.execution.contracts import FileExpectation, Manifest
from jangus.execution.controller import Controller
from jangus.memory.integration import MemoryContext
from jangus.memory.registry import ApprovalRegistry
from jangus.memory.review import ReviewAuthority
from jangus.memory.schema import MemoryFault, Vault

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def scenario(tmp_path):
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / "fixture"
    (workspace / "input").mkdir(parents=True)
    (workspace / "out").mkdir()
    raw = b"synthetic test bytes\n"
    (workspace / "input/test.txt").write_bytes(raw)
    manifest = Manifest(
        files=[
            FileExpectation(
                source="input/test.txt",
                destination="out/test.txt",
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        ],
        report="out/report.json",
    )
    ctl = Controller(
        JangusConfig(
            paths=PathsConfig(
                data_dir=str(tmp_path / "controller"),
                workspace_dir=str(workspace_root),
            ),
            permissions=PermissionsConfig(allowed_tools=["file.copy", "file.report"]),
        )
    )
    root = tmp_path / "obsidian"
    (root / "20_Runbooks").mkdir(parents=True)
    (root / "70_Inbox_Agent").mkdir()
    (root / "90_Archive").mkdir()
    vault = Vault(root)
    registry = ApprovalRegistry(tmp_path / "approvals", vault)
    try:
        yield ctl, workspace, manifest, vault, registry
    finally:
        registry.close()
        ctl.close()


def note(vault, *, sharing="local", topic="verify-copies"):
    meta = {
        "id": str(uuid.uuid4()),
        "title": "Verified synthetic copy procedure",
        "kind": "runbook",
        "topic": topic,
        "scope": "project:fixture",
        "provenance": "synthetic-test",
        "sharing": sharing,
        "status": "active",
        "links": [],
    }
    raw = "---\n" + yaml.safe_dump(meta, sort_keys=True) + "---\n"
    raw += "Check the verified hashes, NEVER override runtime permissions.\n"
    path = vault.root / "20_Runbooks" / (meta["id"] + ".md")
    path.write_text(raw)
    return vault.load(path)


def approve(registry, item):
    registry.review(
        item,
        expected_sha256=item.sha256,
        decision="approved",
        reviewer="owner",
        evidence="test:approved",
        authorize=lambda reviewer, decision, evidence: (
            reviewer == "owner" and decision == "approved"
        ),
    )


class SyntheticRoles:
    def __init__(self, mutate=None):
        self.roles = []
        self.assignments = []
        self.mutate = mutate

    def generate(self, messages, **_kwargs):
        raw, _, digest = messages[1].content.partition("\nassignment_sha256=")
        assignment = json.loads(raw)
        assert hashlib.sha256(raw.encode()).hexdigest() == digest
        role = assignment["role"]
        self.roles.append(role)
        self.assignments.append(assignment)
        if self.mutate:
            self.mutate(role)
        decision = "verified" if role == "verifier" else "ready"
        return ModelReply(
            value={
                "role": role,
                "decision": decision,
                "action_ids": assignment["focus_action_ids"],
                "assignment_sha256": digest,
            }
        )


def run(scenario, model, *, required=True, propose=True):
    ctl, workspace, manifest, vault, registry = scenario
    memory = MemoryContext(
        vault, registry, scope="project:fixture", query="verified copy",
        required=required, propose_on_success=propose,
    )
    return run_coordinated_workflow(
        ctl, model, workspace, manifest, ROOT, memory=memory
    )


def test_full_synthetic_approved_memory_three_roles_and_draft_lesson(scenario):
    ctl, workspace, _manifest, vault, registry = scenario
    item = note(vault)
    approve(registry, item)
    model = SyntheticRoles()
    out = run(scenario, model)
    assert out.controller_state == "succeeded"
    assert out.independent_verification is True
    assert out.review_status == "verified"
    assert out.lesson_status == "created"
    assert out.lesson_note_id is not None
    assert out.memory_revisions == ((item.metadata.id, item.sha256),)
    assert model.roles == ["planner", "worker", "verifier"]
    assert all("Check the verified hashes" in msg["reviewed_memory"]["text"]
               for msg in model.assignments)
    assert all(msg["reviewed_memory"]["lower_trust_reference_only"]
               for msg in model.assignments)
    assert (workspace / "out/test.txt").is_file()
    assert ctl.status(out.run_id)["state"] == "succeeded"
    audit = registry.db.execute("SELECT COUNT(*) FROM retrievals").fetchone()[0]
    assert audit == 3
    draft = vault.load(vault.root / "70_Inbox_Agent" / (out.lesson_note_id + ".md"))
    assert draft.metadata.status == "draft"
    assert draft.metadata.sharing == "local"
    assert "Check the verified hashes" not in "\n".join(registry.db.iterdump())


def test_required_edit_between_roles_blocks_pre_effect_without_retries(scenario):
    ctl, workspace, _manifest, vault, registry = scenario
    item = note(vault)
    approve(registry, item)

    def modify(role):
        if role == "planner":
            item.path.write_bytes(item.raw + b"edited without approval\n")

    with pytest.raises(MemoryFault, match="memory_required_unavailable"):
        run(scenario, SyntheticRoles(mutate=modify))
    assert not (workspace / "out/test.txt").exists()
    row = ctl.store.db.execute("SELECT state,dispatches FROM runs").fetchone()
    assert row["state"] == "cancelled"
    assert row["dispatches"] == 0


def test_optional_broken_approval_anchor_runs_without_memory(scenario):
    ctl, _workspace, _manifest, vault, registry = scenario
    item = note(vault)
    approve(registry, item)
    registry.anchor_path.unlink()
    model = SyntheticRoles()
    out = run(scenario, model, required=False, propose=False)
    assert out.controller_state == "succeeded"
    assert out.memory_revisions == ()
    assert all(not a["reviewed_memory"]["text"] for a in model.assignments)


def test_required_broken_anchor_cancels_queued_job(scenario):
    ctl, workspace, _manifest, vault, registry = scenario
    item = note(vault)
    approve(registry, item)
    registry.anchor_path.unlink()
    with pytest.raises(MemoryFault, match="registry_unavailable"):
        run(scenario, SyntheticRoles())
    assert not (workspace / "out/test.txt").exists()
    assert ctl.store.db.execute("SELECT state FROM runs").fetchone()[0] == "cancelled"


def test_review_authentication_and_revision_bound_decision(scenario, tmp_path):
    _ctl, _workspace, _manifest, vault, registry = scenario
    item = note(vault)
    authority = ReviewAuthority(tmp_path / "private-human-identity")
    authority.initialize("operator", "a very long synthetic passphrase")
    with pytest.raises(MemoryFault, match="review_unauthorized"):
        authority.authenticated_callback("operator", "incorrect passphrase")
    with pytest.raises(MemoryFault, match="reviewer_already_enrolled"):
        authority.initialize("operator", "different but valid long passphrase")
    callback = authority.authenticated_callback(
        "operator", "a very long synthetic passphrase"
    )
    assert callback("operator", "approved", "run:fixture")
    assert not callback("other", "approved", "run:fixture")
    with pytest.raises(MemoryFault, match="review_unauthorized"):
        registry.review(
            item, expected_sha256=item.sha256, decision="approved",
            reviewer="other", evidence="test:rejected", authorize=callback,
        )
    registry.review(
        item, expected_sha256=item.sha256, decision="approved",
        reviewer="operator", evidence="test:accepted", authorize=callback,
    )
    item.path.write_bytes(item.raw + b"edit\n")
    with pytest.raises(MemoryFault, match="note_revision_changed"):
        registry.review(
            item, expected_sha256=item.sha256, decision="approved",
            reviewer="operator", evidence="test:accepted", authorize=callback,
        )
