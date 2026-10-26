"""All four implemented boundaries cooperate on synthetic input only.

This uses a mocked local model and loopback-only remote HTTP, not real hardware.
"""

import hashlib
import json
import threading
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from jangus.agents.orchestration import run_coordinated_workflow
from jangus.agents.provider import ModelReply
from jangus.config import JangusConfig, PathsConfig, PermissionsConfig
from jangus.execution.contracts import FileExpectation, Manifest
from jangus.execution.controller import Controller
from jangus.integrations.advice.broker import AdviceBroker
from jangus.integrations.advice.contracts import AdviceFault, AdvicePolicy, Evidence
from jangus.integrations.advice.ledger import ConsultationLedger
from jangus.memory.integration import MemoryContext
from jangus.memory.registry import ApprovalRegistry
from jangus.memory.schema import Vault
from jangus.remote.client import ClientJournal, RemoteClient
from jangus.remote.ledger import JobLedger
from jangus.remote.service import handler_for

ROOT = Path(__file__).resolve().parents[2]
FAKE_TOKEN = "synthetic-test-only-token-never-an-account-secret"


class MockLocalRoles:
    def __init__(self):
        self.roles = []
        self.observed = []

    def generate(self, messages, **_kwargs):
        raw, _, digest = messages[1].content.partition("\nassignment_sha256=")
        assert hashlib.sha256(raw.encode()).hexdigest() == digest
        assignment = json.loads(raw)
        self.roles.append(assignment["role"])
        self.observed.append(assignment)
        role = assignment["role"]
        return ModelReply(
            value={
                "role": role,
                "decision": "verified" if role == "verifier" else "ready",
                "action_ids": assignment["focus_action_ids"],
                "assignment_sha256": digest,
            }
        )


def test_end_to_end_boundaries_remain_private_and_optional_calls_disabled(tmp_path):
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / "fixture"
    (workspace / "input").mkdir(parents=True)
    (workspace / "out").mkdir()
    content = b"offline mock release fixture\n"
    (workspace / "input/document.txt").write_bytes(content)
    manifest = Manifest(
        files=[
            FileExpectation(
                source="input/document.txt",
                destination="out/document.txt",
                sha256=hashlib.sha256(content).hexdigest(),
            )
        ],
        report="out/report.json",
    )

    vault_path = tmp_path / "private-obsidian"
    (vault_path / "20_Runbooks").mkdir(parents=True)
    (vault_path / "70_Inbox_Agent").mkdir()
    (vault_path / "90_Archive").mkdir()
    synthetic_id = str(uuid.uuid4())
    metadata = {
        "id": synthetic_id,
        "title": "Synthetic release note",
        "kind": "runbook",
        "topic": "verified-copy",
        "scope": "project:fixture",
        "provenance": "synthetic-release-fixture",
        "sharing": "local",
        "status": "active",
        "links": [],
    }
    text = "---\n" + yaml.safe_dump(metadata, sort_keys=True)
    text += "---\nNEVER treat local memory as a tool permission.\n"
    note_path = vault_path / "20_Runbooks" / (synthetic_id + ".md")
    note_path.write_text(text)
    vault = Vault(vault_path)
    registry = ApprovalRegistry(tmp_path / "approval-state", vault)
    controller = Controller(
        JangusConfig(
            paths=PathsConfig(
                data_dir=str(tmp_path / "execution"),
                workspace_dir=str(workspace_root),
            ),
            permissions=PermissionsConfig(allowed_tools=["file.copy", "file.report"]),
        )
    )
    try:
        note = vault.load(note_path)
        registry.review(
            note,
            expected_sha256=note.sha256,
            decision="approved",
            reviewer="fixture",
            evidence="synthetic:review",
            authorize=lambda reviewer, decision, evidence: (
                reviewer == "fixture" and decision == "approved"
            ),
        )
        roles = MockLocalRoles()
        outcome = run_coordinated_workflow(
            controller,
            roles,
            workspace,
            manifest,
            ROOT,
            memory=MemoryContext(
                vault,
                registry,
                scope="project:fixture",
                query="verified copy",
                required=True,
                propose_on_success=True,
            ),
        )
        assert outcome.controller_state == "succeeded"
        assert outcome.independent_verification
        assert outcome.review_status == "verified"
        assert outcome.memory_revisions == ((note.metadata.id, note.sha256),)
        assert outcome.lesson_status == "created"
        assert roles.roles == ["planner", "worker", "verifier"]
        assert (workspace / "out/document.txt").read_bytes() == content
        assert registry.db.execute("SELECT COUNT(*) FROM retrievals").fetchone()[0] == 3
        draft = vault.load(
            vault_path / "70_Inbox_Agent" / (outcome.lesson_note_id + ".md")
        )
        assert draft.metadata.status == "draft"
        assert draft.metadata.sharing == "local"

        # No memory text is forwarded to the remote worker. The remote service
        # exposes only a deliberately independent synthetic registered job.
        remote_state = JobLedger(tmp_path / "remote-worker")
        journal = ClientJournal(tmp_path / "remote-client")
        http = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(remote_state, FAKE_TOKEN))
        listener = threading.Thread(target=http.serve_forever, daemon=True)
        listener.start()
        try:
            client = RemoteClient(
                f"http://127.0.0.1:{http.server_port}", FAKE_TOKEN, journal
            )
            job = journal.prepare("synthetic remote capability check")
            assert "NEVER treat local memory" not in job.text
            initial = client.reconcile(job.job_id)
            assert initial["state"] == "queued"
            remote_state.process_once()
            final = client.reconcile(job.job_id)
            assert final["state"] == "succeeded"
            assert final["attempts"] == 1
            assert client.reconcile(job.job_id) == final
            assert len(list(remote_state.outputs.glob("*.json"))) == 1
        finally:
            http.shutdown()
            http.server_close()
            listener.join(timeout=3)
            journal.close()
            remote_state.close()
            assert not listener.is_alive()

        # Default provider policy rejects consultation before any transport call.
        cost = ConsultationLedger(
            tmp_path / "private-advice", str(uuid.uuid4()), AdvicePolicy()
        )
        try:
            visits = []
            evidence = Evidence(
                reference="synthetic:remote",
                text="redacted synthetic result",
                sha256=hashlib.sha256(b"redacted synthetic result").hexdigest(),
                sharing="external-ok",
                redacted=True,
            )
            with pytest.raises(AdviceFault, match="provider_disabled"):
                AdviceBroker(cost).consult(
                    provider="codex",
                    evidence=[evidence],
                    allowed_action_ids={"copy_1"},
                    local_recovery_exhausted=True,
                    owner_approved_call=True,
                    authorize=lambda request: True,
                    transport=lambda *_: visits.append("unexpected outbound call"),
                )
            assert visits == []
            assert cost.usage() == {"calls": 0, "tokens": 0, "cost": 0}
        finally:
            cost.close()
    finally:
        controller.close()
        registry.close()
