"""Phase 4 A01-A08: real files/SQLite and process termination, no model mocks."""

import json
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from jangus.config import load_config
from jangus.execution.contracts import RuntimeFault, digest
from jangus.execution.controller import Controller
from jangus.execution.diagnostics import export, validate_bundle
from jangus.execution.files import FileTools
from jangus.execution.storage import restore


def test_a01_verified_workflow_and_event_correlation(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    result = c.execute(run_id)
    assert result["state"] == "succeeded"
    assert (workspace / "out/a.txt").read_bytes() == (workspace / "input/a.txt").read_bytes()
    assert (workspace / "out/report.json").read_bytes() == manifest.report_bytes()
    events = c.store.events(run_id)
    for action in result["actions"]:
        assert [e["kind"] for e in events if e["action_id"] == action["id"]] == [
            "intent",
            "result",
            "verified",
        ]
        assert all(e["attempt"] == 1 for e in events if e["action_id"] == action["id"])
    assert len({e["id"] for e in events}) == len(events)
    assert c.execute(run_id)["dispatches"] == 2
    assert c.status(run_id)["rules_sha256"] == digest(Path("rules.md").read_bytes())


def test_a02_existing_output_never_overwritten(workflow):
    c, workspace, manifest, _ = workflow
    target = workspace / "out/a.txt"
    target.write_bytes(b"unrelated")
    with pytest.raises(RuntimeFault, match="output_conflict"):
        c.submit(workspace, manifest)
    assert target.read_bytes() == b"unrelated"


@pytest.mark.parametrize("when", ["before_verification", "after_verification"])
def test_a03_corrupt_output_cannot_succeed(workflow, when):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)

    def corrupt(stage, action):
        if stage == when and action.id == "copy_1":
            (workspace / "out/a.txt").write_bytes(b"corrupt")

    assert c.execute(run_id, hook=corrupt)["state"] == "failed"


def test_a03_fabricated_tool_success_is_not_verification(workflow, monkeypatch):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    monkeypatch.setattr(FileTools, "execute", lambda *_args: {"success": True})
    assert c.execute(run_id)["state"] == "failed"


@pytest.mark.parametrize("failures,expected,attempts", [(1, "succeeded", 2), (99, "failed", 2)])
def test_a04_transient_retry_and_total_limit(workflow, monkeypatch, failures, expected, attempts):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    original = FileTools.execute
    calls = 0

    def unstable(self, action, report=b""):
        nonlocal calls
        if action.id == "copy_1":
            calls += 1
            if calls <= failures:
                raise OSError("synthetic-secret-must-not-be-logged")
        return original(self, action, report)

    monkeypatch.setattr(FileTools, "execute", unstable)
    result = c.execute(run_id)
    assert result["state"] == expected
    assert result["actions"][0]["attempts"] == attempts
    assert "synthetic-secret" not in json.dumps(c.store.events(run_id))
    if expected == "failed":
        assert c.execute(run_id)["actions"][0]["attempts"] == 2
        assert not (workspace / "out/a.txt").exists()


@pytest.mark.parametrize("stage", ["after_intent", "after_effect", "after_verification"])
def test_a05_actual_process_death_and_reconciliation(workflow, stage):
    c, workspace, manifest, config_file = workflow
    run_id = c.submit(workspace, manifest)
    code = """
import os,sys
from pathlib import Path
from jangus.config import load_config
from jangus.execution.controller import Controller
c=Controller(load_config(Path(sys.argv[1])))
def stop(stage, action):
    if stage==sys.argv[3] and action.id=='copy_1': os._exit(75)
c.execute(sys.argv[2],hook=stop)
"""
    killed = subprocess.run(
        [sys.executable, "-c", code, str(config_file), run_id, stage],
        capture_output=True,
        timeout=15,
    )
    assert killed.returncode == 75, killed.stderr.decode()
    before = c.status(run_id)
    assert before["actions"][0]["attempts"] == 1
    result = c.execute(run_id)
    assert result["state"] == "succeeded"
    assert result["actions"][0]["attempts"] == (2 if stage == "after_intent" else 1)
    assert result["dispatches"] == (3 if stage == "after_intent" else 2)
    assert (workspace / "out/a.txt").read_bytes() == (workspace / "input/a.txt").read_bytes()


def test_a05_partial_effect_waits_without_retry_or_overwrite(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)

    class Crash(BaseException):
        pass

    def crash(stage, action):
        if stage == "after_intent":
            (workspace / action.path).write_bytes(b"partial")
            raise Crash

    with pytest.raises(Crash):
        c.execute(run_id, hook=crash)
    result = c.execute(run_id)
    assert result["state"] == "waiting"
    assert result["actions"][0]["state"] == "uncertain"
    assert c.execute(run_id)["dispatches"] == 1
    assert (workspace / "out/a.txt").read_bytes() == b"partial"


@pytest.mark.parametrize("stage", ["queued", "after_intent", "after_effect"])
def test_a06_cancel_prevents_new_work_and_reconciles_effect(workflow, stage):
    c, workspace, manifest, config_file = workflow
    run_id = c.submit(workspace, manifest)
    if stage == "queued":
        c.store.cancel(run_id)

    def cancel(at, _action):
        if at == stage:
            # A separate process can request cancellation while executor owns its lock.
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "jangus",
                    "run",
                    "cancel",
                    run_id,
                    "--config",
                    str(config_file),
                ],
                capture_output=True,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr

    result = c.execute(run_id, hook=cancel)
    assert result["state"] == "cancelled"
    assert not (workspace / "out/report.json").exists()
    assert (workspace / "out/a.txt").exists() == (stage == "after_effect")
    assert result["actions"][0]["state"] == ("verified" if stage == "after_effect" else "pending")


def test_a06_cancel_uncertain_records_unresolved_effect(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)

    class Crash(BaseException):
        pass

    def stop(stage, action):
        if stage == "after_intent":
            (workspace / action.path).write_bytes(b"partial")
            raise Crash

    with pytest.raises(Crash):
        c.execute(run_id, hook=stop)
    c.store.cancel(run_id)
    status = c.execute(run_id)
    assert status["state"] == "cancelled"
    assert status["actions"][0]["state"] == "uncertain"


def test_a07_failed_intent_transaction_blocks_effect(workflow, monkeypatch):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    original = c.store.event

    def unavailable(run_id, kind, *args, **kwargs):
        if kind == "intent":
            raise sqlite3.OperationalError("synthetic secret full disk")
        return original(run_id, kind, *args, **kwargs)

    monkeypatch.setattr(c.store, "event", unavailable)
    with pytest.raises(RuntimeFault, match="persistence_unavailable"):
        c.execute(run_id)
    assert not (workspace / "out/a.txt").exists()
    assert c.status(run_id)["dispatches"] == 0


def test_a07_result_persistence_failure_preserves_intent_for_restart(workflow, monkeypatch):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    original = c.store.event

    def unavailable(run_id, kind, *args, **kwargs):
        if kind == "result":
            raise sqlite3.OperationalError("full")
        return original(run_id, kind, *args, **kwargs)

    monkeypatch.setattr(c.store, "event", unavailable)
    with pytest.raises(RuntimeFault, match="persistence_unavailable"):
        c.execute(run_id)
    assert c.status(run_id)["actions"][0]["state"] == "intent"
    monkeypatch.setattr(c.store, "event", original)
    result = c.execute(run_id)
    assert result["state"] == "succeeded"
    assert result["actions"][0]["attempts"] == 1


def test_a07_storage_budget_blocks_dispatch(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    c.store.max_bytes = 1
    with pytest.raises(RuntimeFault, match="storage_limit"):
        c.execute(run_id)
    assert not (workspace / "out/a.txt").exists()


def test_a08_export_excludes_secrets_and_has_verified_receipts(workflow, tmp_path):
    c, workspace, manifest, _ = workflow
    secret = b"password=synthetic-canary-private\nBearer fake-private-token"
    (workspace / "input/a.txt").write_bytes(secret)
    manifest = manifest.model_copy(
        update={"files": [manifest.files[0].model_copy(update={"sha256": digest(secret)})]}
    )
    run_id = c.submit(workspace, manifest)
    c.execute(run_id)
    with c.store.transaction():
        c.store.event(
            run_id,
            "retry",
            code="io_failure",
            authorization=secret.decode(),
            password=secret.decode(),
            raw_input=secret.decode(),
        )
    target = tmp_path / "diagnostics.zip"
    export(c, run_id, target)
    assert validate_bundle(target)
    with zipfile.ZipFile(target) as archive:
        all_data = b"".join(archive.read(name) for name in archive.namelist())
        assert b"synthetic-canary-private" not in all_data
        assert b"fake-private-token" not in all_data
        assert str(workspace).encode() not in all_data
        assert "verification.json" in archive.namelist()
        assert json.loads(archive.read("omissions.json"))["omitted"]


def test_backup_restore_preserves_pending_intent_and_budgets(workflow, tmp_path):
    c, workspace, manifest, config_file = workflow
    run_id = c.submit(workspace, manifest)

    class Crash(BaseException):
        pass

    def crash(stage, _action):
        if stage == "after_effect":
            raise Crash

    with pytest.raises(Crash):
        c.execute(run_id, hook=crash)
    backup = tmp_path / "private-backup.sqlite3"
    c.store.backup(backup)
    with pytest.raises(RuntimeFault, match="restore_requires_empty_state"):
        restore(backup, c.store.directory)
    # Restore to the original configured location after explicitly archiving old state.
    c.close()
    c.store.directory.rename(tmp_path / "archived-state")
    restore(backup, c.store.directory)
    recovered = Controller(load_config(config_file))
    try:
        result = recovered.execute(run_id)
        assert result["state"] == "succeeded"
        assert result["actions"][0]["attempts"] == 1
    finally:
        recovered.close()


def test_deadline_and_retention_do_not_reset_unfinished_runs(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    with c.store.transaction():
        c.store.db.execute("UPDATE runs SET deadline=? WHERE id=?", (time.time() - 1, run_id))
    assert c.store.prune(14, now=time.time() + 90 * 86400) == 0
    assert c.execute(run_id)["error"] == "deadline"
    assert not (workspace / "out/a.txt").exists()
    assert c.store.prune(14, now=time.time() + 90 * 86400) == 1
    with pytest.raises(RuntimeFault, match="run_missing"):
        c.status(run_id)


def test_cancellation_wins_during_final_verification(workflow, monkeypatch):
    from jangus.execution import controller

    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    original = controller.verify
    calls = 0

    def verify_and_cancel(files, action):
        nonlocal calls
        calls += 1
        result = original(files, action)
        if calls == 4:
            c.store.cancel(run_id)
        return result

    monkeypatch.setattr(controller, "verify", verify_and_cancel)
    assert c.execute(run_id)["state"] == "cancelled"


def test_cancelled_uncertainty_is_not_pruned(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)

    class Crash(BaseException):
        pass

    def crash(stage, action):
        if stage == "after_intent":
            (workspace / action.path).write_bytes(b"partial")
            raise Crash

    with pytest.raises(Crash):
        c.execute(run_id, hook=crash)
    c.store.cancel(run_id)
    assert c.execute(run_id)["state"] == "cancelled"
    assert c.store.prune(14, now=time.time() + 90 * 86400) == 0
    assert c.status(run_id)["actions"][0]["state"] == "uncertain"


def test_total_dispatch_limit_survives_restart(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    with c.store.transaction():
        c.store.db.execute("UPDATE runs SET max_dispatches=1 WHERE id=?", (run_id,))

    class Crash(BaseException):
        pass

    def stop(stage, _action):
        if stage == "after_intent":
            raise Crash

    with pytest.raises(Crash):
        c.execute(run_id, hook=stop)
    result = c.execute(run_id)
    assert result["error"] == "budget_exhausted"
    assert result["dispatches"] == 1
    assert not (workspace / "out/a.txt").exists()


def test_readonly_database_blocks_new_effects(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    c.store.db.execute("PRAGMA query_only=ON")
    try:
        with pytest.raises(RuntimeFault, match="persistence_unavailable"):
            c.execute(run_id)
        assert not (workspace / "out/a.txt").exists()
    finally:
        c.store.db.execute("PRAGMA query_only=OFF")
