import json
from pathlib import Path

import pytest

from jangus.agents.spec import (
    check_host,
    check_tools_known,
    load_spec_file,
    parse_spec,
    sha256_hex,
    to_constraints,
)
from jangus.agents.spec_registry import SpecApprovalRegistry
from jangus.directives.models import parse_directive
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.ledger import Ledger
from jangus.kernel.registry import Tier

GOOD = """\
schema_version: 1
name: backup-maintainer
role: "You maintain verified backups."
model: {provider: ollama, name: "qwen3:8b", max_tokens: 1024}
tools: [file.list, file.read, file.copy_new, report.write]
max_tier: T2
secrets: []
memory_scope: [env.backups]
triggers: [{cron: "0 2 * * *"}]
budget: {calls: 40, tokens: 60000, wall_seconds: 900, spend_usd: 0}
escalation_model: {provider: claude, name: haiku-tag, effort: low}
attempts: {default_model: 2, step_up: 1, max_model_calls: 6, max_proposals: 12}
max_input_tokens: 6000
recipes: [sort-downloads]
watchers: []
host: pc
"""


def good(old: str, new: str) -> bytes:
    assert old in GOOD, old
    return GOOD.replace(old, new).encode()


def spec_fault(raw: bytes) -> FaultCode:
    with pytest.raises(RuntimeFault) as info:
        parse_spec(raw)
    return info.value.code


def test_good_spec_loads_and_hash_covers_exact_bytes() -> None:
    loaded = parse_spec(GOOD.encode())
    assert loaded.spec.name == "backup-maintainer"
    assert loaded.spec.max_tier is Tier.T2
    assert loaded.spec.triggers[0].cron == "0 2 * * *"
    assert loaded.sha256 == sha256_hex(GOOD.encode())
    # A whitespace-only edit is still a different spec for approval purposes.
    assert parse_spec(GOOD.encode() + b"\n").sha256 != loaded.sha256


def test_repo_orchestrator_draft_parses_but_is_only_a_draft() -> None:
    draft = Path(__file__).parents[1] / "agents" / "drafts" / "orchestrator.yaml"
    loaded = load_spec_file(draft)
    assert loaded.spec.tools == ("agent.start", "approval.request", "spec.draft", "report.write")
    assert loaded.spec.max_tier is Tier.T2
    assert loaded.spec.secrets == ()
    assert loaded.spec.budget.daily_escalations == 5


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"- a\n- list\n",
        b"just a string",
        GOOD.encode() + b"surprise: 1\n",  # unknown field
        GOOD.encode() + b"host: pc\n",  # duplicate key
        good("max_tier: T2", "max_tier: T9"),
        GOOD.encode() + b"max_tier: T3\n",
        good("schema_version: 1", "schema_version: 2"),
        GOOD.replace("backup-maintainer", "Bad_Name").encode(),
        GOOD.replace("max_tokens: 1024", 'max_tokens: "1024"').encode(),  # no string coercion
        GOOD.replace("max_tokens: 1024", "max_tokens: 0").encode(),
        GOOD.replace("provider: ollama", "provider: openai").encode(),
        GOOD.replace("tools: [file.list,", "tools: [file.list, file.list,").encode(),  # duplicate
        GOOD.replace('{cron: "0 2 * * *"}', '{cron: "daily"}').encode(),
        GOOD.replace('{cron: "0 2 * * *"}', '{cron: "0 2 * * *", manual: true}').encode(),
        GOOD.replace("host: pc", "host: moon").encode(),
        GOOD.replace("spend_usd: 0", "spend_usd: -1").encode(),
        GOOD.replace("host: pc", "host: pc\nrequired_paths: [relative/dir]").encode(),
        GOOD.replace("name: backup-maintainer", "name: &n backup-maintainer").encode(),  # anchor
        GOOD.encode() + b"---\nname: second-document\n",
        b"\xff\xfe not utf8",
        GOOD.replace("role:", "when: 2026-01-01\nrole:").encode(),  # non-JSON value (date)
    ],
)
def test_malformed_or_hostile_specs_are_rejected(raw: bytes) -> None:
    assert spec_fault(raw) is FaultCode.SPEC_INVALID


def test_alias_expansion_bomb_is_rejected_before_expansion() -> None:
    bomb = (
        b"a: &a [x,x,x,x,x,x,x,x,x]\n"
        b"b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]\n"
        b"c: &c [*b,*b,*b,*b,*b,*b]\n"
    )
    assert spec_fault(bomb) is FaultCode.SPEC_INVALID


def test_oversize_spec_rejected() -> None:
    assert spec_fault(b"#" * 70_000) is FaultCode.SPEC_INVALID


def test_error_text_does_not_echo_spec_values() -> None:
    raw = GOOD.replace("max_tier: T2", "max_tier: SECRETCANARY").encode()
    with pytest.raises(RuntimeFault) as info:
        parse_spec(raw)
    assert "SECRETCANARY" not in str(info.value)
    assert "max_tier" in str(info.value)


def test_unknown_tools_refused_against_registry() -> None:
    loaded = parse_spec(GOOD.encode())
    check_tools_known(loaded.spec, {"file.list", "file.read", "file.copy_new", "report.write"})
    with pytest.raises(RuntimeFault) as info:
        check_tools_known(loaded.spec, {"file.list"})
    assert info.value.code is FaultCode.SPEC_INVALID


# ---- B09: approval registry -------------------------------------------------


@pytest.fixture
def reg(tmp_path: Path) -> SpecApprovalRegistry:
    ledger = Ledger.open(tmp_path / "ledger.jsonl", clock=lambda: "t")
    return SpecApprovalRegistry(tmp_path / "cfg" / "approvals.json", ledger, clock=lambda: "now")


def test_b09_unapproved_spec_cannot_run(reg: SpecApprovalRegistry) -> None:
    loaded = parse_spec(GOOD.encode())
    assert not reg.is_approved(loaded)
    with pytest.raises(RuntimeFault) as info:
        reg.require_approved(loaded)
    assert info.value.code is FaultCode.SPEC_NOT_APPROVED


def test_b09_approved_spec_runs_and_edit_deactivates_until_reapproved(
    reg: SpecApprovalRegistry,
) -> None:
    v1 = parse_spec(GOOD.encode())
    reg.approve(v1, note="first")
    reg.require_approved(v1)

    edited = parse_spec(good("max_tier: T2", "max_tier: T3"))  # privilege increase by editing
    assert edited.spec.name == v1.spec.name
    with pytest.raises(RuntimeFault) as info:
        reg.require_approved(edited)
    assert info.value.code is FaultCode.SPEC_NOT_APPROVED

    reg.approve(edited)
    reg.require_approved(edited)
    # One active hash per name: the old content is no longer approved.
    assert not reg.is_approved(v1)


def test_approval_survives_a_new_registry_object_and_lists(
    reg: SpecApprovalRegistry, tmp_path: Path
) -> None:
    reg.approve(parse_spec(GOOD.encode()), note="n")
    again = SpecApprovalRegistry(tmp_path / "cfg" / "approvals.json")
    [entry] = again.entries()
    assert (entry.name, entry.note, entry.approved_at) == ("backup-maintainer", "n", "now")


def test_revoke_removes_approval(reg: SpecApprovalRegistry) -> None:
    loaded = parse_spec(GOOD.encode())
    reg.approve(loaded)
    assert reg.revoke("backup-maintainer") is True
    assert not reg.is_approved(loaded)
    assert reg.revoke("backup-maintainer") is False


def test_ledger_records_approval_and_revocation(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "l.jsonl", clock=lambda: "t")
    reg = SpecApprovalRegistry(tmp_path / "a.json", ledger)
    v1 = parse_spec(GOOD.encode())
    v2 = parse_spec(good("max_tier: T2", "max_tier: T1"))
    reg.approve(v1)
    reg.approve(v2)
    reg.revoke("backup-maintainer")
    events = [e.event for e in ledger.entries()]
    assert [e["type"] for e in events] == ["spec_approved", "spec_approved", "spec_revoked"]
    assert events[0]["replaces"] is None
    assert events[1]["replaces"] == v1.sha256
    assert events[1]["sha256"] == v2.sha256
    assert ledger.verify_chain() == 3


def test_unwritable_ledger_means_no_approval(tmp_path: Path) -> None:
    blocked = tmp_path / "ledger-dir-missing" / "l.jsonl"
    ledger = Ledger.open(blocked, clock=lambda: "t")  # parent does not exist: append fails
    reg = SpecApprovalRegistry(tmp_path / "a.json", ledger)
    loaded = parse_spec(GOOD.encode())
    with pytest.raises(RuntimeFault) as info:
        reg.approve(loaded)
    assert info.value.code is FaultCode.LEDGER_UNAVAILABLE
    assert not reg.is_approved(loaded)
    assert not (tmp_path / "a.json").exists()


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        json.dumps({"schema_version": 2, "approved": {}}),
        json.dumps({"schema_version": 1}),
        json.dumps({"schema_version": 1, "approved": {}, "extra": 1}),
        json.dumps({"schema_version": 1, "approved": {"x": {"sha256": "short"}}}),
        json.dumps(
            {
                "schema_version": 1,
                "approved": {"x": {"sha256": "a" * 64, "approved_at": "t", "note": 5}},
            }
        ),
    ],
)
def test_corrupt_registry_fails_closed(tmp_path: Path, content: str) -> None:
    path = tmp_path / "a.json"
    path.write_text(content, encoding="utf-8")
    reg = SpecApprovalRegistry(path)
    loaded = parse_spec(GOOD.encode())
    with pytest.raises(RuntimeFault) as info:
        reg.is_approved(loaded)
    assert info.value.code is FaultCode.SPEC_REGISTRY_INVALID
    with pytest.raises(RuntimeFault):
        reg.approve(loaded)  # does not overwrite a corrupt file blindly


def test_registry_write_leaves_no_temp_files(reg: SpecApprovalRegistry, tmp_path: Path) -> None:
    reg.approve(parse_spec(GOOD.encode()))
    names = sorted(p.name for p in (tmp_path / "cfg").iterdir())
    assert names == ["approvals.json"]


def test_models_cannot_reach_approval_through_registered_tool_names() -> None:
    """Structural: the spec/approval API takes parsed specs, never model output, and no
    tool in the fake registry used by the executor tests exposes approval."""
    from tests.fakes import FakeWorld, build_registry

    registry = build_registry(FakeWorld())
    names = [
        t.name for t in (registry.get(n) for n in ("file.read", "file.write", "file.delete")) if t
    ]
    assert not any("approve" in n or "spec" in n for n in names)


# ---- B35: host check --------------------------------------------------------


def test_b35_host_requirements() -> None:
    vps = parse_spec(
        GOOD.replace("host: pc", "host: vps\nsecrets: [shop_token]\nrequired_paths: [/data/x]")
        .replace("secrets: []\n", "")
        .encode()
    )
    ok = {"secret": lambda n: True, "path": lambda p: True}
    check_host(vps.spec, running_host="vps", secret_available=ok["secret"], path_exists=ok["path"])

    def fault(**kw):  # type: ignore[no-untyped-def]
        with pytest.raises(RuntimeFault) as info:
            check_host(vps.spec, **kw)
        assert info.value.code is FaultCode.HOST_REQUIREMENTS_UNMET
        return str(info.value)

    assert "host 'vps'" in fault(
        running_host="pc", secret_available=lambda n: True, path_exists=lambda p: True
    )
    assert "shop_token" in fault(
        running_host="vps", secret_available=lambda n: False, path_exists=lambda p: True
    )
    assert "/data/x" in fault(
        running_host="vps", secret_available=lambda n: True, path_exists=lambda p: False
    )


# ---- bridge to directives ---------------------------------------------------


def _directive(spec_hash: str, calls: int = 10) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "d1",
        "issued_by": "owner",
        "assignee": "backup-maintainer",
        "spec_sha256": spec_hash,
        "goal": "g",
        "budget": {"calls": calls, "tokens": 1000, "wall_seconds": 60, "spend_usd": 0},
        "tasks": [
            {
                "id": 1,
                "title": "t",
                "testStrategy": "s",
                "tools": ["file.read"],
                "max_model_calls": 3,
            }
        ],
    }


def test_constraints_from_spec_narrow_directives(reg: SpecApprovalRegistry) -> None:
    loaded = parse_spec(GOOD.encode())
    constraints = to_constraints(loaded)
    assert constraints.sha256 == loaded.sha256
    assert constraints.max_model_calls == 6
    parse_directive(_directive(loaded.sha256), constraints)
    with pytest.raises(RuntimeFault):  # budget looser than the spec
        parse_directive(_directive(loaded.sha256, calls=41), constraints)
    raw = _directive(loaded.sha256)
    raw["tasks"][0]["tools"] = ["file.delete"]  # type: ignore[index]
    with pytest.raises(RuntimeFault):  # tool outside the spec
        parse_directive(raw, constraints)
