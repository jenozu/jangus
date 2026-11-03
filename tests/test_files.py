import os
from pathlib import Path
from typing import Any

import pytest

from jangus.kernel.approvals import ApprovalQueue
from jangus.kernel.executor import AgentContext, Executor
from jangus.kernel.ledger import Ledger
from jangus.kernel.registry import Registry, Tier
from jangus.kernel.state import StateStore
from jangus.tools.files import FileWorld, build_file_tools, register_file_tools, sha256_file

ALL_TOOLS = [
    "file.list",
    "file.read",
    "file.hash",
    "file.copy_new",
    "file.copy_new_tree",
    "report.write",
    "file.overwrite",
    "file.delete",
]


class Rig:
    def __init__(self, tmp: Path) -> None:
        self.src = tmp / "docs"
        self.dst = tmp / "backup"
        self.outside = tmp / "elsewhere"
        self.reports = tmp / "reports"
        for d in (self.src, self.dst, self.outside):
            d.mkdir()
        self.world = FileWorld([self.src, self.dst], self.reports)
        self.registry = Registry()
        register_file_tools(self.registry, self.world)
        self.state = StateStore.open(tmp / "state.db")
        self.ledger = Ledger.open(tmp / "ledger.jsonl")
        self.approvals = ApprovalQueue(self.state, self.ledger)
        self.executor = Executor(self.registry, self.state, self.ledger, approvals=self.approvals)
        self.state.set_budget("agent/actions", 1000)
        self.run_id = self.state.create_run("run-1", "files").id
        self.t2 = AgentContext(ALL_TOOLS, Tier.T2, "agent/actions")
        self.t3 = AgentContext(ALL_TOOLS, Tier.T3, "agent/actions")

    def call(self, tool: str, args: dict[str, Any], agent: AgentContext | None = None) -> Any:
        return self.executor.submit(self.run_id, agent or self.t2, tool, args)

    def write(self, base: Path, rel: str, text: str) -> Path:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")


# ---- registry rules ------------------------------------------------------------


def test_every_tool_declares_tier_verify_and_reconcile() -> None:
    tools = {t.name: t for t in build_file_tools(FileWorld([Path.cwd()], Path.cwd()))}
    assert set(tools) == set(ALL_TOOLS)
    assert {n for n, t in tools.items() if t.tier is Tier.T1} == {
        "file.list",
        "file.read",
        "file.hash",
    }
    assert {n for n, t in tools.items() if t.tier is Tier.T3} == {"file.overwrite", "file.delete"}
    assert all(t.verify and t.reconcile for t in tools.values() if t.tier is not Tier.T1)
    assert not tools["file.delete"].reversible and not tools["file.overwrite"].reversible


# ---- reading -------------------------------------------------------------------


def test_list_read_hash(rig: Rig) -> None:
    f = rig.write(rig.src, "a/b.txt", "hello")
    listing = rig.call("file.list", {"root": str(rig.src), "recursive": True})
    assert listing.status == "done"
    assert [e["path"] for e in listing.result["entries"]] == [str(f)]
    assert rig.call("file.read", {"path": str(f)}).result["text"] == "hello"
    assert rig.call("file.hash", {"path": str(f)}).result["sha256"] == sha256_file(f)
    cut = rig.call("file.read", {"path": str(f), "max_bytes": 2}).result
    assert cut == {"text": "he", "truncated": True}


# ---- path safety (B14) -----------------------------------------------------------


@pytest.mark.parametrize("bad", ["relative/path.txt", "/etc/../etc/passwd", "", "C:\\x\\..\\y"])
def test_relative_and_traversal_paths_are_rejected_by_the_schema(rig: Rig, bad: str) -> None:
    res = rig.call("file.read", {"path": bad})
    assert res.status == "rejected" and res.decision.reason.value == "schema_violation"


def test_path_outside_the_approved_folders_is_rejected_for_a_t2_agent(rig: Rig) -> None:
    secret = rig.write(rig.outside, "secret.txt", "private")
    res = rig.call("file.read", {"path": str(secret)})
    assert res.status == "rejected" and res.decision.reason.value == "above_max_tier"
    assert res.result is None  # nothing was read


def test_outside_path_for_a_t3_agent_is_queued_not_run(rig: Rig) -> None:
    secret = rig.write(rig.outside, "secret.txt", "private")
    res = rig.call("file.read", {"path": str(secret)}, rig.t3)
    assert res.status == "queued" and res.result is None


def test_symlink_escape_is_rejected(rig: Rig) -> None:
    secret = rig.write(rig.outside, "secret.txt", "private")
    symlink_or_skip(rig.src / "innocent.txt", secret)
    res = rig.call("file.read", {"path": str(rig.src / "innocent.txt")})
    assert res.status == "rejected" and res.result is None


def test_symlinked_directory_escape_is_rejected(rig: Rig) -> None:
    rig.write(rig.outside, "x/secret.txt", "private")
    symlink_or_skip(rig.src / "linked", rig.outside / "x")
    res = rig.call("file.list", {"root": str(rig.src / "linked")})
    assert res.status == "rejected"
    copy = rig.call(
        "file.copy_new",
        {"src": str(rig.src / "linked" / "secret.txt"), "dst": str(rig.dst / "stolen.txt")},
    )
    assert copy.status == "rejected" and not (rig.dst / "stolen.txt").exists()


def test_a_link_is_refused_even_after_owner_approval(rig: Rig) -> None:
    secret = rig.write(rig.outside, "secret.txt", "private")
    symlink_or_skip(rig.src / "innocent.txt", secret)
    args = {"path": str(rig.src / "innocent.txt")}
    first = rig.call("file.read", args, rig.t3)
    assert first.status == "queued"
    rig.approvals.approve(rig.approvals.list("pending")[0].id, "owner")
    second = rig.call("file.read", args, rig.t3)
    assert second.status != "done" or second.result is None or "private" not in str(second.result)


def test_listing_does_not_follow_links_out_of_the_tree(rig: Rig) -> None:
    rig.write(rig.outside, "secret.txt", "private")
    rig.write(rig.src, "real.txt", "ok")
    symlink_or_skip(rig.src / "out", rig.outside)
    names = [
        e["path"]
        for e in rig.call("file.list", {"root": str(rig.src), "recursive": True}).result["entries"]
    ]
    assert names == [str(rig.src / "real.txt")]


# ---- copy_new: no silent overwrite, byte-verified --------------------------------------


def test_copy_new_copies_and_verifies(rig: Rig) -> None:
    src = rig.write(rig.src, "a.txt", "payload")
    res = rig.call("file.copy_new", {"src": str(src), "dst": str(rig.dst / "a.txt")})
    assert res.status == "done" and res.result["created"] is True
    assert (rig.dst / "a.txt").read_text() == "payload"


def test_copy_new_twice_is_idempotent(rig: Rig) -> None:
    src = rig.write(rig.src, "a.txt", "payload")
    args = {"src": str(src), "dst": str(rig.dst / "a.txt")}
    assert rig.call("file.copy_new", args).status == "done"
    assert rig.call("file.copy_new", args).status == "already_done"


def test_existing_different_destination_is_never_overwritten_and_is_flagged(rig: Rig) -> None:
    src = rig.write(rig.src, "a.txt", "new content")
    dst = rig.write(rig.dst, "a.txt", "OLD CONTENT")
    res = rig.call("file.copy_new", {"src": str(src), "dst": str(dst)})
    assert res.status == "unresolved"  # corrupted or different destination detected
    assert dst.read_text() == "OLD CONTENT"  # and untouched


def test_tampered_destination_fails_verification(rig: Rig) -> None:
    src = rig.write(rig.src, "a.txt", "payload")
    dst = rig.dst / "a.txt"
    args = {"src": str(src), "dst": str(dst)}
    rig.call("file.copy_new", args)
    tool = rig.registry.get("file.copy_new")
    assert tool is not None
    model = tool.args_model.model_validate(args)
    assert tool.verify is not None and tool.verify(model, {}) is True
    dst.write_text("bit rot")
    assert tool.verify(model, {}) is False  # B14: a corrupted destination is detected
    assert tool.reconcile is not None and tool.reconcile(model).state == "unknown"


def test_reconcile_after_a_crash(rig: Rig) -> None:
    src = rig.write(rig.src, "a.txt", "payload")
    tool = rig.registry.get("file.copy_new")
    assert tool is not None and tool.reconcile is not None
    model = tool.args_model.model_validate({"src": str(src), "dst": str(rig.dst / "a.txt")})
    assert tool.reconcile(model).state == "absent"  # nothing happened: safe to run
    (rig.dst / "a.txt").write_text("payload")
    assert tool.reconcile(model).state == "applied"  # it did happen: adopt it


# ---- copy_new_tree ---------------------------------------------------------------------------


def test_copy_tree_copies_only_what_is_missing_and_deletes_nothing(rig: Rig) -> None:
    rig.write(rig.src, "a.txt", "A")
    rig.write(rig.src, "sub/b.txt", "B")
    rig.write(rig.dst, "a.txt", "A")  # already backed up
    extra = rig.write(rig.dst, "only-in-backup.txt", "keep me")
    res = rig.call("file.copy_new_tree", {"src_root": str(rig.src), "dst_root": str(rig.dst)})
    assert res.status == "done"
    assert res.result["copied"] == [str(rig.dst / "sub" / "b.txt")]
    assert res.result["already_present"] == 1 and res.result["conflicts"] == []
    assert (rig.dst / "sub" / "b.txt").read_text() == "B" and extra.read_text() == "keep me"


def test_copy_tree_reports_a_conflict_and_does_not_overwrite(rig: Rig) -> None:
    rig.write(rig.src, "a.txt", "SOURCE")
    dst = rig.write(rig.dst, "a.txt", "CORRUPTED")
    rig.write(rig.src, "b.txt", "B")
    res = rig.call("file.copy_new_tree", {"src_root": str(rig.src), "dst_root": str(rig.dst)})
    assert res.status == "unresolved"  # verification failed: needs the owner
    assert dst.read_text() == "CORRUPTED"
    assert (rig.dst / "b.txt").read_text() == "B"  # the clean file was still copied


def test_copy_tree_respects_max_files_and_finishes_on_the_next_run(rig: Rig) -> None:
    for i in range(5):
        rig.write(rig.src, f"f{i}.txt", str(i))
    args = {"src_root": str(rig.src), "dst_root": str(rig.dst), "max_files": 2}
    first = rig.call("file.copy_new_tree", args)
    assert len(first.result["copied"]) == 2 and first.result["remaining"] == 3
    rig.run_id = rig.state.create_run("run-2", "files").id
    second = rig.call("file.copy_new_tree", {**args, "max_files": 10})
    assert len(second.result["copied"]) == 3 and second.result["remaining"] == 0


def test_copy_tree_reconcile(rig: Rig) -> None:
    rig.write(rig.src, "a.txt", "A")
    tool = rig.registry.get("file.copy_new_tree")
    assert tool is not None and tool.reconcile is not None
    model = tool.args_model.model_validate({"src_root": str(rig.src), "dst_root": str(rig.dst)})
    assert tool.reconcile(model).state == "absent"
    rig.write(rig.dst, "a.txt", "A")
    assert tool.reconcile(model).state == "applied"
    rig.write(rig.dst, "a.txt", "X")
    assert tool.reconcile(model).state == "unknown"


# ---- T3: overwrite and delete queue, never run silently ---------------------------------------


def test_overwrite_and_delete_are_rejected_for_a_t2_agent(rig: Rig) -> None:
    src = rig.write(rig.src, "a.txt", "new")
    dst = rig.write(rig.dst, "a.txt", "old")
    assert rig.call("file.overwrite", {"src": str(src), "dst": str(dst)}).status == "rejected"
    assert rig.call("file.delete", {"path": str(dst)}).status == "rejected"
    assert dst.read_text() == "old"


def test_overwrite_and_delete_queue_for_a_t3_agent_and_run_only_after_approval(rig: Rig) -> None:
    src = rig.write(rig.src, "a.txt", "new")
    dst = rig.write(rig.dst, "a.txt", "old")
    over = {"src": str(src), "dst": str(dst)}
    assert rig.call("file.overwrite", over, rig.t3).status == "queued"
    assert dst.read_text() == "old"  # nothing happened yet
    rig.approvals.approve(rig.approvals.list("pending")[0].id, "owner")
    assert rig.call("file.overwrite", over, rig.t3).status == "done"
    assert dst.read_text() == "new"

    assert rig.call("file.delete", {"path": str(dst)}, rig.t3).status == "queued"
    assert dst.exists()
    rig.approvals.approve(rig.approvals.list("pending")[0].id, "owner")
    assert rig.call("file.delete", {"path": str(dst)}, rig.t3).status == "done"
    assert not dst.exists()


def test_approval_is_bound_to_the_exact_payload(rig: Rig) -> None:
    a = rig.write(rig.dst, "a.txt", "1")
    b = rig.write(rig.dst, "b.txt", "2")
    rig.call("file.delete", {"path": str(a)}, rig.t3)
    rig.approvals.approve(rig.approvals.list("pending")[0].id, "owner")
    assert rig.call("file.delete", {"path": str(b)}, rig.t3).status == "queued"  # not covered
    assert b.exists()


# ---- report.write ----------------------------------------------------------------


def test_report_write_creates_a_new_file_and_is_idempotent(rig: Rig) -> None:
    res = rig.call("report.write", {"name": "backup-2026-11-03.txt", "content": "ok: 3 files"})
    assert res.status == "done"
    assert (rig.reports / "backup-2026-11-03.txt").read_text() == "ok: 3 files"
    again = rig.call("report.write", {"name": "backup-2026-11-03.txt", "content": "ok: 3 files"})
    assert again.status == "already_done"


def test_report_write_never_replaces_an_existing_report(rig: Rig) -> None:
    rig.call("report.write", {"name": "r.txt", "content": "first"})
    rig.run_id = rig.state.create_run("run-2", "files").id
    res = rig.call("report.write", {"name": "r.txt", "content": "second"})
    assert res.status == "unresolved"
    assert (rig.reports / "r.txt").read_text() == "first"


@pytest.mark.parametrize("name", ["../x.txt", "a/b.txt", ".hidden", "", "x" * 81, "a b"])
def test_report_names_must_be_plain(rig: Rig, name: str) -> None:
    assert rig.call("report.write", {"name": name, "content": "x"}).status == "rejected"
