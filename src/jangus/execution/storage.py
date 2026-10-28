"""Transactional state/events, process ownership, and consistent private backups."""

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from jangus.execution.contracts import RuntimeFault, encoded
from jangus.execution.files import no_links

SCHEMA_VERSION = 1
TERMINAL = {"succeeded", "failed", "cancelled"}
TRANSITIONS = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"running", "waiting", "succeeded", "failed", "cancelled"},
    "waiting": {"running", "failed", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}
EVENTS = {
    "submitted",
    "started",
    "intent",
    "result",
    "verified",
    "retry",
    "uncertain",
    "cancel_requested",
    "cancelled",
    "failed",
    "succeeded",
    "reconciled",
}
CODES = {
    "ok",
    "io_failure",
    "unsafe_path",
    "workspace_missing",
    "workspace_changed",
    "parent_missing",
    "unsafe_file",
    "file_changed",
    "file_too_large",
    "listing_too_large",
    "tool_denied",
    "input_mismatch",
    "report_mismatch",
    "verification_failed",
    "output_conflict",
    "budget_exhausted",
    "deadline",
    "cancelled",
    "interrupted_absent",
    "output_absent",
    "policy_changed",
}
DDL = [
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN
        ('queued','running','waiting','succeeded','failed','cancelled')),
        created REAL NOT NULL, updated REAL NOT NULL, deadline REAL NOT NULL,
        config_hash TEXT NOT NULL, policy_version TEXT NOT NULL, rules_hash TEXT NOT NULL,
        workspace TEXT NOT NULL, workspace_identity TEXT NOT NULL,
        manifest TEXT NOT NULL, plan TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision=1),
        max_attempts INTEGER NOT NULL, max_dispatches INTEGER NOT NULL,
        dispatches INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0,
        error TEXT)""",
    """CREATE TABLE actions (
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        id TEXT NOT NULL, ordinal INTEGER NOT NULL, definition TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','intent','verified','failed','uncertain')),
        attempts INTEGER NOT NULL DEFAULT 0, evidence TEXT, error TEXT,
        PRIMARY KEY(run_id,id))""",
    """CREATE TABLE events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, action_id TEXT,
        attempt INTEGER, revision INTEGER NOT NULL, time REAL NOT NULL,
        kind TEXT NOT NULL, payload TEXT NOT NULL)""",
]


def safe_payload(payload: dict) -> dict:
    """Allowlist structured diagnostics instead of persisting arbitrary task text."""
    result: dict[str, object] = {}
    for key, value in payload.items():
        if key == "code" and isinstance(value, str) and value in CODES:
            result[key] = value
        elif key in {"bytes", "dispatches", "remaining_attempts"} and type(value) is int:
            result[key] = value
        elif key == "sha256" and isinstance(value, str) and len(value) == 64:
            if all(c in "0123456789abcdef" for c in value):
                result[key] = value
    return result


@contextmanager
def process_lock(path: Path):
    no_links(path)
    stream = path.open("a+b")
    locked = False
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            except OSError:
                raise RuntimeFault("executor_busy") from None
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeFault("executor_busy") from None
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


class Store:
    def __init__(self, directory: Path, max_bytes: int = 256 * 1024 * 1024):
        self.directory = directory.absolute()
        self.path = self.directory / "state.sqlite3"
        self.max_bytes = max_bytes
        no_links(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for suffix in ["", "-wal", "-shm"]:
            no_links(Path(str(self.path) + suffix))
        try:
            self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA wal_autocheckpoint=32")
            self.db.execute("PRAGMA journal_size_limit=65536")
            self.db.execute(f"PRAGMA max_page_count={max(32, max_bytes // 4096 - 32)}")
            # Opening existing state must permit read-only diagnostics/maintenance
            # even when the operational storage budget is exhausted.
            with self.transaction(check_size=False):
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if version not in {0, SCHEMA_VERSION}:
                    raise RuntimeFault("unsupported_state_version")
                if version == 0:
                    for sql in DDL:
                        self.db.execute(sql)
                    self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            if os.name != "nt":
                self.path.chmod(0o600)
        except (sqlite3.Error, OSError):
            if hasattr(self, "db"):
                self.db.close()
            raise RuntimeFault("persistence_unavailable") from None
        except RuntimeFault:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def lock(self):
        return process_lock(self.directory / "executor.lock")

    @contextmanager
    def snapshot(self):
        self.db.execute("BEGIN")
        try:
            yield
        finally:
            self.db.execute("ROLLBACK")

    @contextmanager
    def transaction(self, *, check_size=True):
        try:
            size = sum(p.stat().st_size for p in self.directory.glob("state.sqlite3*"))
            if check_size and size > self.max_bytes:
                raise RuntimeFault("storage_limit")
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.execute("COMMIT")
        except BaseException as exc:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            if isinstance(exc, (sqlite3.Error, OSError)):
                raise RuntimeFault("persistence_unavailable") from None
            raise

    def run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise RuntimeFault("run_missing")
        return dict(row)

    def actions(self, run_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM actions WHERE run_id=? ORDER BY ordinal", (run_id,)
            )
        ]

    def event(self, run_id, kind, action=None, **payload):
        if kind not in EVENTS:
            raise RuntimeFault("invalid_event")
        if not self.db.in_transaction:
            raise RuntimeFault("event_requires_transaction")
        self.db.execute(
            """INSERT INTO events
            (id,run_id,action_id,attempt,revision,time,kind,payload) VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                run_id,
                action["id"] if action else None,
                action["attempts"] if action else None,
                1,
                time.time(),
                kind,
                encoded(safe_payload(payload)).decode(),
            ),
        )

    def transition(self, run_id, state, code=None):
        if not self.db.in_transaction:
            raise RuntimeFault("transition_requires_transaction")
        row = self.run(run_id)
        if state not in TRANSITIONS[row["state"]]:
            raise RuntimeFault("invalid_transition")
        self.db.execute(
            "UPDATE runs SET state=?,error=?,updated=? WHERE id=?",
            (state, code if code in CODES else None, time.time(), run_id),
        )

    def cancel(self, run_id):
        with self.transaction():
            row = self.run(run_id)
            if row["state"] in TERMINAL or row["cancelled"]:
                return
            self.db.execute(
                "UPDATE runs SET cancelled=1,updated=? WHERE id=?", (time.time(), run_id)
            )
            self.event(run_id, "cancel_requested")

    def events(self, run_id, limit=4097):
        return [
            dict(r) | {"payload": json.loads(r["payload"])}
            for r in self.db.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY sequence LIMIT ?", (run_id, limit)
            )
        ]

    def backup(self, target: Path):
        no_links(target)
        with self.lock(), target.open("xb"):
            if os.name != "nt":
                target.chmod(0o600)
            destination = sqlite3.connect(target)
            try:
                self.db.backup(destination)
                if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeFault("backup_invalid")
            finally:
                destination.close()

    def prune(self, retention_days: int, now: float | None = None) -> int:
        cutoff = (time.time() if now is None else now) - retention_days * 86400
        with self.lock():
            # Maintenance may recover a full log without authorizing new effects.
            self.db.execute("BEGIN IMMEDIATE")
            try:
                count = self.db.execute(
                    "DELETE FROM runs WHERE state IN "
                    "('succeeded','failed','cancelled') AND updated<? "
                    "AND NOT EXISTS (SELECT 1 FROM actions WHERE actions.run_id=runs.id "
                    "AND actions.state IN ('intent','uncertain'))",
                    (cutoff,),
                ).rowcount
                self.db.execute("COMMIT")
            except sqlite3.Error:
                self.db.execute("ROLLBACK")
                raise RuntimeFault("persistence_unavailable") from None
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.execute("VACUUM")
            return count


def restore(source: Path, directory: Path):
    """Restore into an empty state directory; never overwrite a live database."""
    no_links(source)
    no_links(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / "state.sqlite3"
    with process_lock(directory / "executor.lock"):
        if any(directory.glob("state.sqlite3*")):
            raise RuntimeFault("restore_requires_empty_state")
        original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            if (
                original.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                or original.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
                or not {"runs", "actions", "events"}
                <= {
                    r[0]
                    for r in original.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                or original.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise RuntimeFault("backup_invalid")
            with target.open("xb"):
                if os.name != "nt":
                    target.chmod(0o600)
                destination = sqlite3.connect(target)
                try:
                    original.backup(destination)
                finally:
                    destination.close()
        finally:
            original.close()
