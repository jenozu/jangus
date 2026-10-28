"""Private SQLite approval ledger with out-of-database anti-rollback anchor.

The authorization callback must be supplied by trusted human-review UI code.
Neither a Markdown note nor an agent may grant approval.
"""

import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from jangus.execution.files import no_links
from jangus.execution.storage import process_lock
from jangus.memory.schema import MemoryFault, Note, Vault
from jangus.paths import checkout_roots

ALLOWED_DECISIONS = {"approved", "revoked", "retired"}


class ApprovalRegistry:
    def __init__(self, directory: Path, vault: Vault):
        self.vault = vault
        no_links(directory)
        self.directory = directory.resolve()
        if any(
            self.directory.is_relative_to(checkout) or checkout.is_relative_to(self.directory)
            for checkout in checkout_roots()
        ):
            raise MemoryFault("registry_inside_checkout")
        if self.directory.is_relative_to(vault.root) or vault.root.is_relative_to(self.directory):
            raise MemoryFault("registry_overlaps_vault")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        no_links(self.directory)
        self.db_path = self.directory / "approvals.sqlite3"
        self.anchor_path = self.directory / "approval-anchor.json"
        self.snapshots = self.directory / "snapshots"
        self.snapshots.mkdir(exist_ok=True, mode=0o700)
        no_links(self.snapshots)
        no_links(self.db_path)
        no_links(self.anchor_path)
        existed = self.db_path.exists()
        if not existed and self.anchor_path.exists():
            raise MemoryFault("registry_rollback_detected")
        try:
            self.db = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            if not existed:
                with self._initial_transaction():
                    self.db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    self.db.execute(
                        "CREATE TABLE reviews (sequence INTEGER PRIMARY KEY, "
                        "note_id TEXT NOT NULL, sha256 TEXT NOT NULL, decision TEXT NOT NULL, "
                        "scope TEXT NOT NULL, sharing TEXT NOT NULL, reviewer TEXT NOT NULL, "
                        "evidence TEXT NOT NULL, recorded REAL NOT NULL)"
                    )
                    self.db.execute(
                        "CREATE TABLE retrievals (sequence INTEGER PRIMARY KEY, "
                        "note_id TEXT NOT NULL, sha256 TEXT NOT NULL, scope TEXT NOT NULL, "
                        "destination TEXT NOT NULL, evidence TEXT NOT NULL, recorded REAL NOT NULL)"
                    )
                    self.db.execute(
                        "INSERT INTO meta VALUES (?,?)", ("instance", secrets.token_hex(16))
                    )
                    self.db.execute("INSERT INTO meta VALUES (?,?)", ("generation", "0"))
                self._write_anchor()
            if os.name != "nt":
                self.db_path.chmod(0o600)
            self._check_anchor()
        except (sqlite3.Error, OSError, KeyError, ValueError) as exc:
            self.db.close()
            if isinstance(exc, MemoryFault):
                raise
            raise MemoryFault("registry_unavailable") from None

    def close(self):
        self.db.close()

    def _initial_transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def transaction():
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

        return transaction()

    def _meta(self) -> dict:
        return {row["key"]: row["value"] for row in self.db.execute("SELECT * FROM meta")}

    def _write_anchor(self):
        raw = self._meta()
        data = json.dumps(
            {"instance": raw["instance"], "generation": int(raw["generation"])},
            sort_keys=True,
        ).encode()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
                temporary = Path(stream.name)
                if os.name != "nt":
                    os.chmod(temporary, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.anchor_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _check_anchor(self):
        no_links(self.db_path)
        no_links(self.anchor_path)
        try:
            anchor = json.loads(self.anchor_path.read_bytes())
            meta = self._meta()
            if (
                set(anchor) != {"instance", "generation"}
                or anchor["instance"] != meta["instance"]
                or type(anchor["generation"]) is not int
                or anchor["generation"] != int(meta["generation"])
            ):
                raise ValueError("different generation")
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            raise MemoryFault("registry_unavailable") from None

    def _commit(self, sql: str, params: tuple):
        with process_lock(self.directory / "approval-writer.lock"):
            self._check_anchor()
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute(sql, params)
                self.db.execute(
                    "UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='generation'"
                )
                self.db.execute("COMMIT")
            except sqlite3.Error:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise MemoryFault("registry_unavailable") from None
            # If this write fails the mismatch fails closed on every subsequent operation.
            try:
                self._write_anchor()
            except OSError:
                raise MemoryFault("registry_unavailable") from None

    def _snapshot(self, note: Note):
        path = self.snapshots / note.sha256
        no_links(path)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != note.sha256:
                raise MemoryFault("snapshot_changed")
            return
        try:
            with path.open("xb") as stream:
                stream.write(note.raw)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                path.chmod(0o600)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != note.sha256:
                raise MemoryFault("snapshot_changed") from None
        except OSError:
            raise MemoryFault("registry_unavailable") from None

    def decision(self, note_id: str) -> dict | None:
        self._check_anchor()
        row = self.db.execute(
            "SELECT * FROM reviews WHERE note_id=? ORDER BY sequence DESC LIMIT 1",
            (note_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def review(
        self,
        note: Note,
        *,
        expected_sha256: str,
        decision: str,
        reviewer: str,
        evidence: str,
        authorize: Callable[[str, str, str], bool] | None,
    ):
        """Requires trusted external authorization and exact expected revision."""
        if (
            decision not in ALLOWED_DECISIONS
            or not re.fullmatch(r"[a-zA-Z0-9._@-]{2,80}", reviewer)
            or not re.fullmatch(r"[a-zA-Z0-9._:/#-]{2,160}", evidence)
            or authorize is None
            or not authorize(reviewer, decision, evidence)
        ):
            raise MemoryFault("review_unauthorized")
        current = self.vault.load(note.path)
        if current.metadata.id != note.metadata.id or current.sha256 != expected_sha256:
            raise MemoryFault("note_revision_changed")
        if current.sha256 != note.sha256:
            raise MemoryFault("note_revision_changed")
        if current.metadata.status != "active" and decision == "approved":
            raise MemoryFault("note_not_eligible")
        if current.relative.split("/")[0] in {"70_Inbox_Agent", "90_Archive"}:
            raise MemoryFault("note_not_eligible")
        previous = self.decision(note.metadata.id)
        if previous and previous["decision"] == "retired":
            raise MemoryFault("note_retired")
        if decision == "approved":
            self._snapshot(current)
        self._commit(
            "INSERT INTO reviews "
            "(note_id,sha256,decision,scope,sharing,reviewer,evidence,recorded) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                current.metadata.id,
                current.sha256,
                decision,
                current.metadata.scope,
                current.metadata.sharing,
                reviewer,
                evidence,
                time.time(),
            ),
        )

    def approved(self, note: Note, scope: str, destination: str) -> bool:
        self._check_anchor()
        row = self.decision(note.metadata.id)
        return bool(
            row
            and row["decision"] == "approved"
            and row["sha256"] == note.sha256
            and row["scope"] == note.metadata.scope
            and row["sharing"] == note.metadata.sharing
            and note.metadata.status == "active"
            and note.relative.split("/")[0] not in {"70_Inbox_Agent", "90_Archive"}
            and destination in {"local", "external"}
            and (scope == note.metadata.scope or note.metadata.scope == "global")
            and (destination == "local" or note.metadata.sharing == "external-ok")
        )

    def record_selection(self, note: Note, scope: str, destination: str, evidence: str):
        """Persistence precedes handing reviewed text to a model prompt."""
        if not re.fullmatch(r"[a-zA-Z0-9._:/#-]{2,160}", evidence):
            raise MemoryFault("invalid_evidence")
        if not self.approved(note, scope, destination):
            raise MemoryFault("memory_not_approved")
        self._commit(
            "INSERT INTO retrievals (note_id,sha256,scope,destination,evidence,recorded) "
            "VALUES (?,?,?,?,?,?)",
            (note.metadata.id, note.sha256, scope, destination, evidence, time.time()),
        )
