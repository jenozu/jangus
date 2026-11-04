import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from jangus.agents.spec import parse_spec
from jangus.agents.spec_registry import SpecApprovalRegistry
from jangus.config import parse_config
from jangus.kernel.alerts import read_alerts
from jangus.kernel.lock import RunLock
from jangus.runtime import Runtime, open_runtime, run_standing_directive
from jangus.scheduler import Scheduler

DRAFTS = Path(__file__).resolve().parent.parent / "agents" / "drafts"
SPEC_TEXT = (DRAFTS / "backup-maintainer.yaml").read_text(encoding="utf-8")
DIRECTIVE = json.loads((DRAFTS / "backup-maintainer.directive.json").read_text(encoding="utf-8"))


class Rig:
    """The real draft spec and directive, pointed at temp folders instead of D: and E:."""

    def __init__(self, tmp: Path) -> None:
        self.src, self.dst = tmp / "Documents", tmp / "Backup"
        self.src.mkdir()
        self.dst.mkdir()
        self.specs = tmp / "specs"
        self.specs.mkdir()
        text = SPEC_TEXT.replace("D:/Documents", self.src.as_posix()).replace(
            "E:/Backup", self.dst.as_posix()
        )
        self.spec_path = self.specs / "backup-maintainer.yaml"
        self.spec_path.write_text(text, encoding="utf-8", newline="\n")
        self.set_directive(self.src, self.dst)
        cfg = parse_config(
            {
                "data_dir": str(tmp / "data"),
                "workspace_dir": str(tmp / "work"),
                "specs_dir": str(self.specs),
                "model": {"url": "http://127.0.0.1:9", "name": "none"},
            }
        )
        self.rt: Runtime = open_runtime(cfg)
        self.loaded = parse_spec(self.spec_path.read_bytes())
        self.approvals = SpecApprovalRegistry(cfg.data_dir / "spec-approvals.json", self.rt.ledger)
        self.approvals.approve(self.loaded)

    def set_directive(self, src: Path, dst: Path) -> None:
        text = json.dumps(DIRECTIVE).replace("D:/Documents", src.as_posix())
        text = text.replace("E:/Backup", dst.as_posix())
        (self.specs / "backup-maintainer.directive.json").write_text(text, encoding="utf-8")

    def file(self, base: Path, rel: str, text: str) -> Path:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run(self, slot: datetime) -> object:
        return run_standing_directive(self.rt, self.loaded, slot)

    def events(self, kind: str) -> list[dict[str, object]]:
        return [e.event for e in self.rt.ledger.entries() if e.event.get("kind") == kind]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def slot(day: int) -> datetime:
    return datetime(2026, 11, day, 2, 0)


def test_the_draft_spec_and_directive_are_valid() -> None:
    loaded = parse_spec(SPEC_TEXT.encode())
    assert loaded.spec.model.provider == "none" and loaded.spec.budget.tokens == 0
    assert loaded.spec.tools == ("file.copy_new_tree",) and loaded.spec.max_tier.value == "T2"
    assert {t["runner"] for t in DIRECTIVE["tasks"]} == {"kernel"}


def test_nightly_backup_runs_end_to_end_with_zero_model_calls(rig: Rig) -> None:
    for name in ("a.txt", "b.txt", "sub/c.txt"):
        rig.file(rig.src, name, f"content of {name}")
    rig.file(rig.dst, "a.txt", "content of a.txt")  # already backed up
    report = rig.run(slot(3))
    assert report.completed and not report.escalated  # type: ignore[attr-defined]
    for name in ("a.txt", "b.txt", "sub/c.txt"):
        assert (rig.dst / name).read_text(encoding="utf-8") == f"content of {name}"
    assert rig.events("model_call") == [] and rig.events("cache_hit") == []  # B29: zero tokens
    assert rig.rt.state.list_open_actions() == []  # nothing left unresolved
    assert [a for a in read_alerts(rig.rt.ledger) if a.level == "critical"] == []


def test_every_copy_is_checked_and_a_second_night_copies_only_what_is_new(rig: Rig) -> None:
    old = rig.file(rig.src, "old.txt", "old")
    rig.run(slot(3))
    backed_up = rig.dst / "old.txt"
    stamp = backed_up.stat().st_mtime_ns
    rig.file(rig.src, "new.txt", "new")
    assert rig.run(slot(4)).completed  # type: ignore[attr-defined]
    assert (rig.dst / "new.txt").read_text() == "new"
    assert backed_up.stat().st_mtime_ns == stamp and old.read_text() == "old"  # untouched


def test_a_corrupted_destination_is_detected_flagged_and_never_overwritten(rig: Rig) -> None:
    rig.file(rig.src, "a.txt", "original")
    rig.run(slot(3))
    (rig.dst / "a.txt").write_text("bit rot")  # the backup copy goes bad
    rig.file(rig.src, "b.txt", "second file")
    report = rig.run(slot(4))
    assert not report.completed  # type: ignore[attr-defined]
    assert report.halted is not None and report.halted.reason == "unresolved_effect"  # type: ignore[attr-defined]
    assert (rig.dst / "a.txt").read_text() == "bit rot"  # reported, not silently "fixed"
    assert any(a.code == "directive_halted" for a in read_alerts(rig.rt.ledger))


def test_steps_outside_the_approved_folders_are_rejected(rig: Rig, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    rig.file(elsewhere, "secret.txt", "private")
    rig.set_directive(elsewhere, rig.dst)  # a template that tries to read outside required_paths
    report = rig.run(slot(3))
    assert not report.completed and report.halted is not None  # type: ignore[attr-defined]
    assert "above_max_tier" in report.halted.reason  # type: ignore[attr-defined]
    assert not (rig.dst / "secret.txt").exists()


def test_rerunning_the_same_slot_after_a_crash_changes_nothing(rig: Rig) -> None:
    rig.file(rig.src, "a.txt", "A")
    rig.run(slot(3))
    first_ledger = len(rig.rt.ledger.entries())
    again = rig.run(slot(3))  # same slot: same directive id
    assert again.completed  # type: ignore[attr-defined]
    assert len(rig.events("directive_issued")) == 1  # issued once
    assert len(rig.rt.ledger.entries()) - first_ledger < 5  # only the completion record


def test_the_agent_still_works_on_day_after_day_with_a_small_daily_budget(rig: Rig) -> None:
    """Agent budgets are per day: a nightly agent must not run out of budget over a week."""
    for day in range(3, 10):
        rig.file(rig.src, f"day{day}.txt", str(day))
        assert rig.run(slot(day)).completed, day  # type: ignore[attr-defined]
    assert len(list(rig.dst.glob("day*.txt"))) == 7


def test_missing_backup_drive_stops_the_run_and_alerts(rig: Rig) -> None:
    os.rename(rig.dst, rig.dst.with_name("unplugged"))
    scheduler = Scheduler(
        specs_dir=rig.specs,
        registry=rig.approvals,
        state=rig.rt.state,
        ledger=rig.rt.ledger,
        alerts=rig.rt.alerts,
        lock=RunLock(rig.rt.cfg.data_dir / "tick.lock"),
        run_agent=lambda loaded, when: run_standing_directive(rig.rt, loaded, when),
    )
    scheduler.tick(datetime(2026, 11, 2, 12, 0))
    result = scheduler.tick(datetime(2026, 11, 3, 3, 0))
    assert result.problems and any(
        a.code == "scheduled_run_failed" for a in read_alerts(rig.rt.ledger)
    )
    os.rename(rig.dst.with_name("unplugged"), rig.dst)  # drive plugged back in
    rig.file(rig.src, "a.txt", "A")
    scheduler.tick(datetime(2026, 11, 3, 3, 20))
    assert (rig.dst / "a.txt").read_text() == "A"  # the open slot ran on the next tick


def test_scheduler_runs_the_backup_once_after_missed_nights(rig: Rig) -> None:
    rig.file(rig.src, "a.txt", "A")
    scheduler = Scheduler(
        specs_dir=rig.specs,
        registry=rig.approvals,
        state=rig.rt.state,
        ledger=rig.rt.ledger,
        alerts=rig.rt.alerts,
        lock=RunLock(rig.rt.cfg.data_dir / "tick.lock"),
        run_agent=lambda loaded, when: run_standing_directive(rig.rt, loaded, when),
    )
    scheduler.tick(datetime(2026, 11, 1, 12, 0))
    result = scheduler.tick(datetime(2026, 11, 5, 9, 0))  # four nights missed
    assert result.ran == [("backup-maintainer", "2026-11-05T02:00:00")]
    assert (rig.dst / "a.txt").exists() and len(rig.events("directive_completed")) == 1
