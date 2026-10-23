"""Phase 6 A14-A17 synthetic approval, retrieval, rollback and proposal tests."""

import hashlib
import os
import sqlite3
import uuid

import pytest
import yaml

from jangus.memory.proposals import propose_lesson
from jangus.memory.registry import ApprovalRegistry
from jangus.memory.retrieval import render_for_prompt, select
from jangus.memory.schema import MemoryFault, Vault, parse_note


def authorize(_reviewer, _decision, _evidence):
    return True


@pytest.fixture
def memory(tmp_path):
    root = tmp_path / "vault"
    for name in (
        "20_Runbooks", "40_Lessons", "70_Inbox_Agent", "90_Archive"
    ):
        (root / name).mkdir(parents=True)
    vault = Vault(root)
    registry = ApprovalRegistry(tmp_path / "private-registry", vault)
    try:
        yield vault, registry
    finally:
        registry.close()


def make_note(vault, *, folder="20_Runbooks", note_id=None, **overrides):
    meta = {
        "id": note_id or str(uuid.uuid4()),
        "title": "Copy and verify a synthetic report",
        "kind": "runbook",
        "topic": "file-verification",
        "scope": "project:demo",
        "provenance": "synthetic-test",
        "sharing": "local",
        "status": "active",
        "links": [],
    }
    meta.update(overrides)
    path = vault.root / folder / (str(uuid.uuid4()) + ".md")
    payload = "---\n" + yaml.safe_dump(meta, sort_keys=True) + "---\n"
    payload += "Verify local copy and report hashes.\n"
    path.write_text(payload, encoding="utf-8")
    return vault.load(path)


def approve(registry, note, decision="approved"):
    registry.review(
        note,
        expected_sha256=note.sha256,
        decision=decision,
        reviewer="owner",
        evidence="test:record",
        authorize=authorize,
    )


def test_a14_approved_selected_and_logged_at_exact_revision(memory):
    vault, registry = memory
    note = make_note(vault)
    assert select(vault, registry, query="report", scope="project:demo") == []
    approve(registry, note)
    found = select(vault, registry, query="verify report", scope="project:demo")
    assert len(found) == 1
    assert found[0].sha256 == note.sha256
    result = render_for_prompt(
        vault, registry, found, scope="project:demo",
        destination="local", evidence="run:synthetic",
    )
    assert "Verify local copy" in result
    assert note.sha256 in result
    records = registry.db.execute("SELECT note_id,sha256,evidence FROM retrievals").fetchall()
    assert len(records) == 1
    assert records[0]["note_id"] == note.metadata.id
    assert records[0]["sha256"] == note.sha256
    assert records[0]["evidence"] == "run:synthetic"
    assert (registry.snapshots / note.sha256).read_bytes() == note.raw


def test_a15_raw_crlf_and_whitespace_edits_invalidate_approval(memory):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    original = note.sha256
    note.path.write_bytes(note.raw.replace(b"\n", b"\r\n"))
    changed = vault.load(note.path)
    assert changed.sha256 != original
    assert changed.metadata.id == note.metadata.id
    assert select(vault, registry, query="report", scope="project:demo") == []
    with pytest.raises(MemoryFault, match="memory_required_unavailable"):
        select(vault, registry, query="report", scope="project:demo", required=True)


def test_a15_recheck_rejects_edit_after_selection(memory):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    selected = select(vault, registry, query="report", scope="project:demo")
    note.path.write_bytes(note.raw + b" \n")
    with pytest.raises(MemoryFault, match="memory_revision_changed"):
        render_for_prompt(
            vault, registry, selected, scope="project:demo",
            destination="local", evidence="run:synthetic",
        )
    assert registry.db.execute("SELECT COUNT(*) FROM retrievals").fetchone()[0] == 0


def test_a15_only_explicit_review_approves_and_draft_is_never_selected(memory):
    vault, registry = memory
    draft = make_note(vault, status="draft")
    assert select(vault, registry, query="report", scope="project:demo") == []
    with pytest.raises(MemoryFault, match="note_not_eligible"):
        approve(registry, draft)
    with pytest.raises(MemoryFault, match="review_unauthorized"):
        registry.review(
            draft, expected_sha256=draft.sha256, decision="approved",
            reviewer="owner", evidence="test:record", authorize=None,
        )


def test_a15_archived_or_inbox_note_ineligible(memory):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    archive = vault.root / "90_Archive" / note.path.name
    note.path.rename(archive)
    assert select(vault, registry, query="report", scope="project:demo") == []
    inbox_note = make_note(vault, folder="70_Inbox_Agent")
    with pytest.raises(MemoryFault, match="note_not_eligible"):
        approve(registry, inbox_note)


def test_a15_missing_private_link_prevents_leak(memory):
    vault, registry = memory
    linked = make_note(vault, sharing="local")
    parent = make_note(vault, links=[linked.metadata.id], sharing="external-ok")
    approve(registry, parent)
    assert select(vault, registry, query="report", scope="project:demo") == []
    approve(registry, linked)
    assert select(vault, registry, query="report", scope="project:demo", destination="local")
    assert select(vault, registry, query="report", scope="project:demo", destination="external") == []
    from jangus.memory.retrieval import Selection

    with pytest.raises(MemoryFault, match="memory_revision_changed"):
        render_for_prompt(
            vault,
            registry,
            [Selection(parent.metadata.id, parent.sha256, parent.metadata.scope, parent.metadata.sharing)],
            scope="project:demo",
            destination="external",
            evidence="run:synthetic",
        )


def test_a15_review_does_not_grant_different_scope(memory):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    assert select(vault, registry, query="report", scope="project:other") == []


def test_a15_renamed_note_uses_stable_id_and_exact_bytes(memory):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    note.path.rename(note.path.with_name("renamed.md"))
    found = select(vault, registry, query="report", scope="project:demo")
    assert [x.note_id for x in found] == [note.metadata.id]


def test_a15_duplicates_fail_closed(memory):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    (vault.root / "40_Lessons/duplicate.md").write_bytes(note.raw)
    assert select(vault, registry, query="report", scope="project:demo") == []
    with pytest.raises(MemoryFault, match="duplicate_note_id"):
        select(vault, registry, query="report", scope="project:demo", required=True)


def test_a15_conflicting_topics_need_review_not_blind_merging(memory):
    vault, registry = memory
    first = make_note(vault)
    second = make_note(vault)
    approve(registry, first)
    approve(registry, second)
    selected = select(vault, registry, query="report", scope="project:demo")
    assert len(selected) == 1


def test_a15_retired_note_cannot_reactivate_with_prior_approval(memory):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    approve(registry, note, decision="retired")
    assert select(vault, registry, query="report", scope="project:demo") == []
    with pytest.raises(MemoryFault, match="note_retired"):
        approve(registry, note)


def test_a17_restore_old_sqlite_without_anchor_cannot_reactivate(memory, tmp_path):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    backup = tmp_path / "old.sqlite"
    other = sqlite3.connect(backup)
    registry.db.backup(other)
    other.close()
    approve(registry, note, decision="retired")
    registry.close()
    os.replace(backup, registry.db_path)
    with pytest.raises(MemoryFault, match="registry_unavailable"):
        ApprovalRegistry(registry.directory, vault)


def test_a17_missing_registry_anchor_disables_optional_memory(memory):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    registry.anchor_path.unlink()
    assert select(vault, registry, query="report", scope="project:demo") == []
    with pytest.raises(MemoryFault, match="registry_unavailable"):
        select(vault, registry, query="report", scope="project:demo", required=True)


def test_a16_proposal_is_idempotent_and_never_approved(memory):
    vault, registry = memory
    fields = {"scope": "project:demo", "summary": "Check a verified report.", "evidence": "run:42"}
    first = propose_lesson(vault, **fields)
    second = propose_lesson(vault, **fields)
    assert first.created is True
    assert second.created is False
    assert first.note_id == second.note_id
    note = vault.load(vault.root / first.relative)
    assert note.metadata.status == "draft"
    assert select(vault, registry, query="report", scope="project:demo") == []


def test_a16_human_edit_preserved_and_conflict_returned(memory):
    vault, _registry = memory
    fields = {"scope": "project:demo", "summary": "Check a verified report.", "evidence": "run:42"}
    first = propose_lesson(vault, **fields)
    path = vault.root / first.relative
    path.write_bytes(path.read_bytes() + b"Human edit stays.\n")
    original = path.read_bytes()
    with pytest.raises(MemoryFault, match="proposal_conflict"):
        propose_lesson(vault, **fields)
    assert path.read_bytes() == original
    assert len(list((vault.root / "70_Inbox_Agent").glob("*.md"))) == 1


def test_a17_malformed_note_and_link_ambiguity_fail_closed(memory):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    broken = vault.root / "20_Runbooks/broken.md"
    broken.write_text("---\nid: bad\nid: duplicate\n---\nbody")
    assert select(vault, registry, query="report", scope="project:demo") == []


def test_no_raw_private_note_content_in_registry(memory):
    vault, registry = memory
    note = make_note(vault)
    approve(registry, note)
    dumped = "\n".join(registry.db.iterdump())
    assert "Verify local copy and report hashes" not in dumped
    assert hashlib.sha256(note.raw).hexdigest() in dumped
