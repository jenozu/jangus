"""Phase 5 simulated provider, review, isolated plan, and durable context checks."""

import hashlib
import json
from pathlib import Path

import pytest

from jangus.agents.context import HandoffStore, bounded_output, estimate_tokens
from jangus.agents.provider import Message, ModelFailure, ModelReply, OllamaProvider
from jangus.agents.skills import SkillFailure, SkillRegistry, load_rules
from jangus.agents.workflow import PlanningFailure, run_verified_workflow
from jangus.config import JangusConfig, ModelConfig, PathsConfig, PermissionsConfig
from jangus.execution.contracts import FileExpectation, Manifest
from jangus.execution.controller import RULES_HASH, Controller

ROOT = Path(__file__).resolve().parents[2]


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, amount):
        return self.payload[:amount]


class FakeOpener:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        return FakeResponse(self.data)


class MockPlanner:
    def __init__(self, ids):
        self.ids = ids
        self.messages = None

    def generate(self, messages, **_kwargs):
        self.messages = messages
        return ModelReply(value={"action_ids": self.ids})


@pytest.fixture
def synthetic(tmp_path):
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / "demo"
    (workspace / "input").mkdir(parents=True)
    (workspace / "out").mkdir()
    content = b"synthetic-not-private\n"
    (workspace / "input" / "test.txt").write_bytes(content)
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
            data_dir=str(tmp_path / "state"),
            workspace_dir=str(workspace_root),
        ),
        permissions=PermissionsConfig(allowed_tools=["file.copy", "file.report"]),
    )
    controller = Controller(config)
    try:
        yield controller, workspace, manifest
    finally:
        controller.close()


def test_mock_local_planner_executes_only_verified_canonical_plan(synthetic):
    controller, workspace, manifest = synthetic
    planner = MockPlanner(["copy_1", "report"])
    outcome = run_verified_workflow(controller, planner, workspace, manifest, ROOT)
    assert outcome.state == "succeeded"
    assert (workspace / "out/test.txt").read_bytes() == b"synthetic-not-private\n"
    assert (workspace / "out/report.json").read_bytes() == manifest.report_bytes()
    assert outcome.rules_sha256 == RULES_HASH
    assert outcome.usage_source == "provider_reported"
    assert "input/test.txt" not in str(planner.messages)
    handoff = HandoffStore(Path(controller.config.paths.data_dir) / "handoffs")
    current = handoff.rehydrate(controller, outcome.run_id, outcome.skill_sha256)
    assert current["dispatches"] == 2
    assert all(row["state"] == "verified" for row in current["actions"])


def test_rejected_model_plan_cannot_dispatch(synthetic):
    controller, workspace, manifest = synthetic
    with pytest.raises(PlanningFailure, match="plan_not_authorized"):
        run_verified_workflow(
            controller,
            MockPlanner(["file.write_arbitrary"]),
            workspace,
            manifest,
            ROOT,
        )
    assert not (workspace / "out/test.txt").exists()
    rows = controller.store.db.execute("SELECT id,state,dispatches FROM runs").fetchall()
    assert len(rows) == 1
    assert rows[0]["state"] == "cancelled"
    assert rows[0]["dispatches"] == 0


def test_reviewed_skill_and_rules_revisions(tmp_path):
    text, sha = load_rules(ROOT, RULES_HASH)
    assert sha == RULES_HASH
    assert "R03" in text
    registry = SkillRegistry(ROOT)
    skill = registry.load("file-workflow", {"file.copy", "file.report"})
    assert skill.sha256 == hashlib.sha256(skill.text.encode()).hexdigest()
    assert skill.required_tools == {"file.copy", "file.report"}
    with pytest.raises(SkillFailure, match="skill_tool_denied"):
        registry.load("file-workflow", {"file.report"})
    with pytest.raises(SkillFailure, match="skill_unreviewed"):
        registry.load("unreviewed", set())
    copy = tmp_path / "review"
    (copy / "skills/file-workflow").mkdir(parents=True)
    (copy / "skills/catalog.json").write_bytes((ROOT / "skills/catalog.json").read_bytes())
    (copy / "skills/file-workflow/SKILL.md").write_text(skill.text + "unreviewed change")
    with pytest.raises(SkillFailure, match="skill_revision_changed"):
        SkillRegistry(copy).load("file-workflow", {"file.copy", "file.report"})


def test_handoff_is_non_authoritative_and_preserves_exhausted_attempts(synthetic):
    controller, workspace, manifest = synthetic
    run_id = controller.submit(workspace, manifest)
    with controller.store.transaction():
        controller.store.db.execute(
            "UPDATE actions SET attempts=2,state='uncertain' WHERE run_id=? AND id='copy_1'",
            (run_id,),
        )
        controller.store.db.execute("UPDATE runs SET dispatches=2 WHERE id=?", (run_id,))
    handoff = HandoffStore(Path(controller.config.paths.data_dir) / "handoffs")
    handoff.save(controller, run_id, {"file-workflow": "a" * 64})
    stored = handoff._path(run_id)
    old = json.loads(stored.read_text())
    old["dispatches"] = 0
    old["actions"][0]["remaining_attempts"] = 99
    stored.write_text(json.dumps(old))
    current = handoff.rehydrate(controller, run_id, {"file-workflow": "a" * 64})
    assert current["dispatches"] == 2
    assert current["actions"][0]["remaining_attempts"] == 0
    assert current["actions"][0]["state"] == "uncertain"
    with pytest.raises(ValueError, match="handoff_revision_changed"):
        handoff.rehydrate(controller, run_id, {"file-workflow": "b" * 64})


def test_context_estimates_explicit_and_bounded():
    result = bounded_output("secret evidence\u20ac" * 20, max_bytes=11, reference="event-1")
    assert result["truncated"] is True
    assert result["estimate_only"] is True
    assert len(result["excerpt"].encode()) <= 11
    assert result["estimated_tokens"] == estimate_tokens(result["excerpt"])
    assert result["sha256"] == hashlib.sha256(("secret evidence\u20ac" * 20).encode()).hexdigest()


def test_ollama_reports_provider_usage_without_leaking_response():
    fake = FakeOpener(
        json.dumps(
            {
                "message": {"content": '{"action_ids":["copy_1","report"]}'},
                "prompt_eval_count": 12,
                "eval_count": 5,
            }
        ).encode()
    )
    provider = OllamaProvider(ModelConfig(name="local-model"), opener=fake)
    result = provider.generate([Message(role="user", content="synthetic")])
    assert result.value == {"action_ids": ["copy_1", "report"]}
    assert result.reported_prompt_tokens == 12
    assert result.reported_output_tokens == 5
    assert result.usage_source == "provider_reported"
    assert len(fake.calls) == 1
    assert b'"stream":false' in fake.calls[0][0].data
    assert b"synthetic" in fake.calls[0][0].data


@pytest.mark.parametrize(
    "data",
    [
        b'{"message":{"content":"[]"}}',
        b'{"message":{"content":"{bad"}}',
        b'{"message":{"content":"{}"},"message":{"content":"{}"}}',
        b'{"no_message":true}',
    ],
)
def test_invalid_provider_output_fails_closed(data):
    provider = OllamaProvider(ModelConfig(name="local-model"), opener=FakeOpener(data))
    with pytest.raises(ModelFailure, match="invalid_provider_response"):
        provider.generate([Message(role="user", content="synthetic")])


def test_no_model_or_cancelled_model_fails_closed():
    with pytest.raises(ModelFailure, match="model_unconfigured"):
        OllamaProvider(ModelConfig())
    fake = FakeOpener(b'{"message":{"content":"{}"}}')
    provider = OllamaProvider(ModelConfig(name="local-model"), opener=fake)
    with pytest.raises(ModelFailure, match="cancelled"):
        provider.generate([Message(role="user", content="synthetic")], cancelled=lambda: True)
    assert fake.calls == []
