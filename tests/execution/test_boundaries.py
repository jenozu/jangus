"""Contract, permission, native path, transaction and CLI boundaries."""

import json
import os
import sqlite3
import subprocess
import sys

import pytest
from pydantic import ValidationError

from jangus.execution import storage
from jangus.execution.contracts import Action, Manifest, Plan, RuntimeFault, digest
from jangus.execution.controller import Controller, read_manifest
from jangus.execution.files import FileTools
from jangus.execution.storage import Store


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/outside",
        "out\\file",
        "out/NUL",
        "out/a:stream",
        "out/a.",
        "out//a",
    ],
)
def test_a02_portable_path_rejection(name):
    with pytest.raises(ValidationError):
        Action(id="a", tool="file.hash", path=name, expected_sha256="a" * 64)


def test_a02_unknown_tools_arguments_and_invalid_graphs():
    with pytest.raises(ValidationError):
        Action(id="a", tool="shell", path="input/a.txt")
    with pytest.raises(ValidationError):
        Action(id="a", tool="file.list", path="input", command="anything")
    first = Action(id="a", tool="file.list", path="input")
    with pytest.raises(ValidationError):
        Plan(actions=[first, first])
    with pytest.raises(ValidationError):
        Plan(actions=[first.model_copy(update={"dependencies": ["missing"]})])
    with pytest.raises(ValidationError):
        Plan(
            actions=[
                first.model_copy(update={"dependencies": ["b"]}),
                first.model_copy(update={"id": "b", "dependencies": ["a"]}),
            ]
        )
    with pytest.raises(ValidationError):
        Plan(revision=2, actions=[first])


def test_a02_native_links_rejected(workflow, tmp_path):
    c, workspace, manifest, _ = workflow
    external = tmp_path / "private"
    external.mkdir()
    (external / "a.txt").write_bytes(b"private")
    link = workspace / "linked"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(external)],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(external, target_is_directory=True)
    try:
        with pytest.raises(RuntimeFault, match="unsafe_path"):
            c.files(workspace).read("linked/a.txt")
        with pytest.raises(RuntimeFault, match="unsafe_path"):
            c.files(workspace).write_new("linked/new.txt", b"no")
        assert not (external / "new.txt").exists()
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_a02_link_replacement_after_submission_rejected(workflow, tmp_path):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    (workspace / "out").rmdir()
    external = tmp_path / "outside"
    external.mkdir()
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(workspace / "out"), str(external)],
            capture_output=True,
        )
        assert result.returncode == 0
    else:
        (workspace / "out").symlink_to(external, target_is_directory=True)
    try:
        result = c.execute(run_id)
        assert result["state"] != "succeeded"
        assert list(external.iterdir()) == []
    finally:
        if os.name == "nt":
            (workspace / "out").rmdir()
        else:
            (workspace / "out").unlink()


def test_registered_reads_hashes_lists_and_permission_checks(workflow):
    c, workspace, _manifest, _ = workflow
    files = c.files(workspace)
    expected = digest((workspace / "input/a.txt").read_bytes())
    for tool in ["file.read", "file.hash"]:
        result = files.execute(
            Action(id="read", tool=tool, path="input/a.txt", expected_sha256=expected)
        )
        assert result["sha256"] == expected
    assert files.execute(Action(id="list", tool="file.list", path="input")) == {
        "entries": ["a.txt"]
    }
    denied = FileTools(workspace, set())
    with pytest.raises(RuntimeFault, match="tool_denied"):
        denied.execute(Action(id="list", tool="file.list", path="input"))
    (workspace / "input/large.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(RuntimeFault, match="file_too_large"):
        files.read("input/large.txt")
    os.link(workspace / "input/a.txt", workspace / "input/hard.txt")
    with pytest.raises(RuntimeFault, match="unsafe_file"):
        files.read("input/hard.txt")


def test_stale_revision_and_changed_permissions_do_not_dispatch(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    with pytest.raises(RuntimeFault, match="stale_plan"):
        c.execute(run_id, revision=2)
    changed = Controller(
        c.config.model_copy(
            update={"permissions": c.config.permissions.model_copy(update={"allowed_tools": []})}
        )
    )
    try:
        with pytest.raises(RuntimeFault, match="policy_changed"):
            changed.execute(run_id)
    finally:
        changed.close()
    assert not (workspace / "out/a.txt").exists()


def test_migration_and_transaction_rollback(tmp_path, monkeypatch):
    original = storage.DDL
    monkeypatch.setattr(storage, "DDL", [*original, "INVALID SQL"])
    with pytest.raises(RuntimeFault, match="persistence_unavailable"):
        Store(tmp_path / "state")
    db = sqlite3.connect(tmp_path / "state/state.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    assert not db.execute("SELECT name FROM sqlite_master WHERE name='runs'").fetchall()
    db.close()
    monkeypatch.setattr(storage, "DDL", original)
    store = Store(tmp_path / "state")
    try:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 1
        with pytest.raises(RuntimeFault, match="persistence_unavailable"), store.transaction():
            store.db.execute("INVALID SQL")
        assert not store.db.in_transaction
    finally:
        store.close()


def test_invalid_transition_and_duplicate_run_id_roll_back(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    with pytest.raises(RuntimeFault, match="invalid_transition"), c.store.transaction():
        c.store.transition(run_id, "succeeded")
    with pytest.raises(RuntimeFault, match="persistence_unavailable"), c.store.transaction():
        c.store.db.execute("INSERT INTO runs SELECT * FROM runs WHERE id=?", (run_id,))
    assert c.status(run_id)["state"] == "queued"


def test_exclusive_executor_and_cli_flow(workflow, tmp_path):
    c, workspace, manifest, config_file = workflow
    file = tmp_path / "manifest.json"
    file.write_text(manifest.model_dump_json(), encoding="utf-8")

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "jangus", *args, "--config", str(config_file)],
            capture_output=True,
            text=True,
            timeout=15,
        )

    result = cli("run", "submit", "--workspace", str(workspace), "--manifest", str(file))
    assert result.returncode == 0, result.stderr
    run_id = json.loads(result.stdout)["run_id"]
    with c.store.lock():
        result = cli("run", "execute", run_id)
        assert result.returncode == 2
        assert "executor_busy" in result.stderr
    result = cli("run", "execute", run_id)
    assert result.returncode == 0, result.stderr
    assert json.loads(cli("run", "status", run_id).stdout)["state"] == "succeeded"
    assert cli("run", "export", run_id, "--output", str(tmp_path / "export.zip")).returncode == 0
    assert cli("state", "backup", "--output", str(tmp_path / "backup.sqlite3")).returncode == 0


def test_invalid_manifest_errors_do_not_echo_values(tmp_path):
    file = tmp_path / "manifest.json"
    for content in [
        '{"secret":"synthetic-canary"}',
        '{"report":"a","report":"b"}',
        "x" * (64 * 1024 + 1),
    ]:
        file.write_text(content, encoding="utf-8")
        with pytest.raises(RuntimeFault) as exc:
            read_manifest(file)
        assert "synthetic-canary" not in str(exc.value)


def test_manifest_collisions(workflow):
    _, _, manifest, _ = workflow
    value = manifest.model_dump()
    value["report"] = "OUT/A.TXT"
    with pytest.raises(ValidationError):
        Manifest.model_validate(value)


def test_new_foreign_output_after_submission_is_not_adopted(workflow):
    c, workspace, manifest, _ = workflow
    run_id = c.submit(workspace, manifest)
    content = (workspace / "input/a.txt").read_bytes()
    (workspace / "out/a.txt").write_bytes(content)
    result = c.execute(run_id)
    assert result["state"] == "failed"
    assert result["error"] == "output_conflict"
    assert result["dispatches"] == 0
    assert (workspace / "out/a.txt").read_bytes() == content


@pytest.mark.skipif(os.name == "nt", reason="POSIX named pipes only")
def test_special_file_is_rejected_without_blocking(workflow):
    c, workspace, _manifest, _ = workflow
    os.mkfifo(workspace / "input/pipe")
    with pytest.raises(RuntimeFault, match="unsafe_file"):
        c.files(workspace).read("input/pipe")


def test_future_state_version_and_fake_backup_rejected(tmp_path):
    from jangus.execution.storage import restore

    state = tmp_path / "future"
    state.mkdir()
    db = sqlite3.connect(state / "state.sqlite3")
    db.execute("PRAGMA user_version=99")
    db.close()
    with pytest.raises(RuntimeFault, match="unsupported_state_version"):
        Store(state)
    fake = tmp_path / "fake.sqlite3"
    db = sqlite3.connect(fake)
    db.execute("PRAGMA user_version=1")
    db.close()
    with pytest.raises(RuntimeFault, match="backup_invalid"):
        restore(fake, tmp_path / "restored")
    assert not (tmp_path / "restored/state.sqlite3").exists()
