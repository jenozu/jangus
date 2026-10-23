"""Durable, single-executor synthetic remote work with intent-before-effect."""

import json
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path

from jangus.config import _checkout_roots
from jangus.execution.files import no_links
from jangus.execution.storage import process_lock
from jangus.remote.protocol import (
    PROTOCOL_VERSION,
    JobSpec,
    RemoteFault,
    canonical,
    sha256,
    valid_id,
)

MAX_JOBS = 500
MAX_ATTEMPTS = 2
JOB_TIMEOUT_SECONDS = 900
TERMINAL = {"succeeded", "failed", "cancelled"}


class JobLedger:
    """Each instance owns an SQLite connection; concurrent calls are serialized."""

    def __init__(self, root: Path):
        no_links(root)
        self.root = root.absolute()
        if any(
            self.root.resolve().is_relative_to(checkout)
            or checkout.is_relative_to(self.root.resolve())
            for checkout in _checkout_roots()
        ):
            raise RemoteFault("state_inside_checkout")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.outputs = self.root / "artifacts"
        no_links(self.outputs)
        self.outputs.mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / "jobs.sqlite3"
        for name in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            no_links(name)
        self._thread_lock = threading.RLock()
        try:
            self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            with self._thread_lock:
                self.db.execute("BEGIN IMMEDIATE")
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if version not in {0, 1}:
                    raise RemoteFault("unsupported_state_version")
                if version == 0:
                    self.db.execute(
                        "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, "
                        "idempotency_key TEXT NOT NULL UNIQUE, payload_sha256 TEXT NOT NULL, "
                        "spec TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL, "
                        "cancel_requested INTEGER NOT NULL, created REAL NOT NULL, "
                        "deadline REAL NOT NULL, result_sha256 TEXT, error TEXT)"
                    )
                    self.db.execute("PRAGMA user_version=1")
                self.db.execute("COMMIT")
            if os.name != "nt":
                self.path.chmod(0o600)
        except (sqlite3.Error, OSError):
            if hasattr(self, "db"):
                self.db.close()
            raise RemoteFault("state_unavailable") from None
        except BaseException:
            if hasattr(self, "db"):
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                self.db.close()
            raise

    def close(self):
        with self._thread_lock:
            self.db.close()

    def _one(self, job_id: str) -> dict:
        valid_id(job_id)
        row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise RemoteFault("job_missing")
        return dict(row)

    def _artifact(self, job_id: str) -> Path:
        valid_id(job_id)
        path = self.outputs / (job_id + ".json")
        no_links(path)
        return path

    def _expected(self, row: dict) -> tuple[bytes, dict]:
        spec = JobSpec.model_validate_json(row["spec"])
        result = {
            "version": PROTOCOL_VERSION,
            "job_id": spec.job_id,
            "payload_sha256": spec.payload_sha256,
            "content_sha256": sha256(spec.text.encode()),
            "bytes": len(spec.text.encode()),
        }
        return canonical(result), result

    def _observe(self, row: dict) -> str:
        """Return matching, absent or uncertain. Never trust only an action message."""
        path = self._artifact(row["job_id"])
        try:
            info = path.lstat()
        except FileNotFoundError:
            return "absent"
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return "uncertain"
        try:
            expected, _ = self._expected(row)
            with path.open("rb") as stream:
                raw = stream.read(len(expected) + 1)
            if raw != expected or len(raw) != info.st_size:
                return "uncertain"
            return "matching"
        except (OSError, ValueError):
            return "uncertain"

    def submit(self, spec: JobSpec) -> dict:
        """One job ID, one idempotency key, one immutable payload; no implicit execution."""
        with self._thread_lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                matches = self.db.execute(
                    "SELECT * FROM jobs WHERE job_id=? OR idempotency_key=?",
                    (spec.job_id, spec.idempotency_key),
                ).fetchall()
                if matches:
                    if (
                        len(matches) != 1
                        or matches[0]["job_id"] != spec.job_id
                        or matches[0]["idempotency_key"] != spec.idempotency_key
                        or matches[0]["payload_sha256"] != spec.payload_sha256
                        or json.loads(matches[0]["spec"]) != spec.model_dump()
                    ):
                        raise RemoteFault("idempotency_conflict")
                else:
                    count = self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                    if count >= MAX_JOBS:
                        raise RemoteFault("job_quota")
                    now = time.time()
                    self.db.execute(
                        "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            spec.job_id,
                            spec.idempotency_key,
                            spec.payload_sha256,
                            spec.model_dump_json(),
                            "queued",
                            0,
                            0,
                            now,
                            now + JOB_TIMEOUT_SECONDS,
                            None,
                            None,
                        ),
                    )
                self.db.execute("COMMIT")
            except BaseException as exc:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                if isinstance(exc, sqlite3.Error):
                    raise RemoteFault("state_unavailable") from None
                raise
            return self.status(spec.job_id)

    def status(self, job_id: str) -> dict:
        with self._thread_lock:
            row = self._one(job_id)
            result = None
            if row["result_sha256"] is not None:
                if self._observe(row) != "matching":
                    raise RemoteFault("result_integrity")
                payload, result = self._expected(row)
                if sha256(payload) != row["result_sha256"]:
                    raise RemoteFault("result_integrity")
            return {
                "version": PROTOCOL_VERSION,
                "job_id": row["job_id"],
                "idempotency_key": row["idempotency_key"],
                "payload_sha256": row["payload_sha256"],
                "kind": "synthetic.hash",
                "state": row["state"],
                "attempts": row["attempts"],
                "cancel_requested": bool(row["cancel_requested"]),
                "result_sha256": row["result_sha256"],
                "result": result,
                "error": row["error"],
            }

    def cancel(self, job_id: str) -> dict:
        with self._thread_lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                row = self._one(job_id)
                if row["state"] not in TERMINAL:
                    state = "cancelled" if row["state"] == "queued" else row["state"]
                    self.db.execute(
                        "UPDATE jobs SET cancel_requested=1,state=? WHERE job_id=?",
                        (state, job_id),
                    )
                self.db.execute("COMMIT")
            except BaseException as exc:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                if isinstance(exc, sqlite3.Error):
                    raise RemoteFault("state_unavailable") from None
                raise
            return self.status(job_id)

    def _update(self, job_id: str, state: str, digest: str | None, error: str | None):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "UPDATE jobs SET state=?,result_sha256=?,error=? WHERE job_id=?",
                (state, digest, error, job_id),
            )
            self.db.execute("COMMIT")
        except sqlite3.Error:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise RemoteFault("state_unavailable") from None

    def process_once(self, hook=None) -> str | None:
        """Replay persisted intent on restart; only verified bytes permit completion."""
        with self._thread_lock, process_lock(self.root / "worker.lock"):
            row = self.db.execute(
                "SELECT * FROM jobs WHERE state IN ('queued','intent') "
                "ORDER BY created,job_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            row = dict(row)
            job_id = row["job_id"]
            observation = self._observe(row)
            if observation == "matching":
                expected, _ = self._expected(row)
                state = "cancelled" if row["cancel_requested"] else "succeeded"
                self._update(job_id, state, sha256(expected), None)
                return job_id
            if observation == "uncertain":
                self._update(job_id, "waiting", None, "output_uncertain")
                return job_id
            if row["cancel_requested"]:
                self._update(job_id, "cancelled", None, None)
                return job_id
            if time.time() > row["deadline"]:
                self._update(job_id, "failed", None, "deadline")
                return job_id
            if row["attempts"] >= MAX_ATTEMPTS:
                self._update(job_id, "failed", None, "attempts_exhausted")
                return job_id
            # Intent and charged attempt are committed before new filesystem effect.
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute(
                    "UPDATE jobs SET state='intent',attempts=attempts+1 WHERE job_id=?",
                    (job_id,),
                )
                self.db.execute("COMMIT")
            except sqlite3.Error:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise RemoteFault("state_unavailable") from None
            if hook is not None:
                hook("after_intent", job_id)
            if self._one(job_id)["cancel_requested"]:
                self._update(job_id, "cancelled", None, None)
                return job_id
            payload, _ = self._expected(row)
            path = self._artifact(job_id)
            try:
                with path.open("xb") as stream:
                    if os.name != "nt":
                        path.chmod(0o600)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if os.name != "nt":
                    fd = os.open(self.outputs, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                if hook is not None:
                    hook("after_effect", job_id)
            except OSError:
                # An incomplete or absent effect remains an intent until next reconciliation.
                return job_id
            observation = self._observe(row)
            if observation != "matching":
                self._update(job_id, "waiting", None, "output_uncertain")
            else:
                state = "cancelled" if self._one(job_id)["cancel_requested"] else "succeeded"
                self._update(job_id, state, sha256(payload), None)
            return job_id
