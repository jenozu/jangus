"""Persist consultation reservations before outbound calls without prompt text."""

import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from jangus.execution.files import no_links
from jangus.integrations.advice.contracts import AdviceFault, AdvicePolicy
from jangus.paths import checkout_roots


class ConsultationLedger:
    def __init__(self, directory: Path, session_id: str, policy: AdvicePolicy):
        try:
            if str(uuid.UUID(session_id)) != session_id:
                raise ValueError("noncanonical")
        except ValueError:
            raise AdviceFault("invalid_session") from None
        no_links(directory)
        home = directory.expanduser().resolve()
        self.directory = home
        if any(home.is_relative_to(root) or root.is_relative_to(home) for root in checkout_roots()):
            raise AdviceFault("ledger_inside_checkout")
        home.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.path = home / "consultations.sqlite3"
        self.anchor = home / "consultation-anchor.json"
        no_links(self.path)
        no_links(self.anchor)
        existing = self.path.exists()
        if existing != self.anchor.exists():
            raise AdviceFault("ledger_identity_missing")
        self.policy = policy
        self.session_id = session_id
        revision = hashlib.sha256(
            json.dumps(policy.model_dump(), sort_keys=True).encode()
        ).hexdigest()
        try:
            self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and existing:
                raise AdviceFault("ledger_upgrade_required")
            if version not in {0, 2}:
                raise AdviceFault("ledger_upgrade_required")
            if version == 0:
                self.db.execute(
                    "CREATE TABLE meta(instance TEXT NOT NULL, generation INTEGER NOT NULL)"
                )
                self.db.execute("INSERT INTO meta VALUES (?,0)", (secrets.token_hex(32),))
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS sessions("
                "id TEXT PRIMARY KEY,revision TEXT NOT NULL,calls INTEGER NOT NULL,"
                "tokens INTEGER NOT NULL,cost INTEGER NOT NULL)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS events("
                "id INTEGER PRIMARY KEY,session TEXT NOT NULL,provider TEXT NOT NULL,"
                "evidence_digest TEXT NOT NULL,result_digest TEXT,status TEXT NOT NULL,"
                "created REAL NOT NULL)"
            )
            self.db.execute("BEGIN IMMEDIATE")
            if version == 0:
                self.db.execute("PRAGMA user_version=2")
            self.db.execute(
                "INSERT OR IGNORE INTO sessions VALUES (?,?,0,0,0)",
                (session_id, revision),
            )
            stored = self.db.execute(
                "SELECT revision FROM sessions WHERE id=?", (session_id,)
            ).fetchone()[0]
            if stored != revision:
                raise AdviceFault("policy_revision_changed")
            self.db.execute("COMMIT")
            if existing:
                self._check_anchor()
            else:
                content = self._anchor_bytes()
                with self.anchor.open("xb") as stream:
                    if os.name != "nt":
                        self.anchor.chmod(0o600)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
        except (sqlite3.Error, OSError):
            if hasattr(self, "db"):
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                self.db.close()
            raise AdviceFault("ledger_unavailable") from None
        except BaseException:
            if hasattr(self, "db"):
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                self.db.close()
            raise

    def _anchor_bytes(self) -> bytes:
        row = self.db.execute("SELECT instance,generation FROM meta").fetchone()
        if row is None or not isinstance(row["instance"], str):
            raise AdviceFault("ledger_identity_invalid")
        return json.dumps(
            {"version": 2, "instance": row["instance"], "generation": row["generation"]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def _check_anchor(self):
        try:
            no_links(self.anchor)
            if self.anchor.read_bytes() != self._anchor_bytes():
                raise AdviceFault("ledger_identity_mismatch")
        except (OSError, sqlite3.Error):
            raise AdviceFault("ledger_identity_missing") from None

    def _sync_anchor(self):
        """After each commit, a crash before anchor replacement fails closed on restart."""
        tmp = self.directory / ("consultation-anchor-" + secrets.token_hex(12) + ".tmp")
        try:
            with tmp.open("xb") as stream:
                if os.name != "nt":
                    tmp.chmod(0o600)
                stream.write(self._anchor_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.anchor)
        except (OSError, sqlite3.Error):
            raise AdviceFault("ledger_unavailable") from None
        finally:
            tmp.unlink(missing_ok=True)

    def close(self):
        self.db.close()

    def has_unresolved_codex(self, context_digest: str) -> bool:
        """Only a persisted matching, already charged Codex result allows fallback."""
        try:
            self._check_anchor()
            row = self.db.execute(
                "SELECT 1 FROM events WHERE session=? AND provider='codex' "
                "AND evidence_digest=? AND status IN ('failed','unresolved') "
                "LIMIT 1",
                (self.session_id, context_digest),
            ).fetchone()
        except sqlite3.Error:
            raise AdviceFault("ledger_unavailable") from None
        return row is not None

    def reserve(self, provider: str, tokens: int, cost: int, evidence_digest: str) -> int:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self._check_anchor()
            r = self.db.execute(
                "SELECT calls,tokens,cost FROM sessions WHERE id=?", (self.session_id,)
            ).fetchone()
            if (
                r["calls"] >= self.policy.max_calls
                or r["tokens"] + tokens > self.policy.max_estimated_tokens
                or r["cost"] + cost > self.policy.max_reserved_microdollars
            ):
                raise AdviceFault("consultation_budget_exhausted")
            self.db.execute(
                "UPDATE sessions SET calls=calls+1,tokens=tokens+?,cost=cost+? WHERE id=?",
                (tokens, cost, self.session_id),
            )
            self.db.execute(
                "INSERT INTO events(session,provider,evidence_digest,result_digest,"
                "status,created) VALUES (?,?,?,NULL,'reserved',?)",
                (self.session_id, provider, evidence_digest, time.time()),
            )
            event_id = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.db.execute("UPDATE meta SET generation=generation+1")
            self.db.execute("COMMIT")
            self._sync_anchor()
            return event_id
        except sqlite3.Error:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise AdviceFault("ledger_unavailable") from None
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def finish(self, event_id: int, result_digest: str | None, status: str):
        if status not in {"advisory", "unresolved", "failed"}:
            raise AdviceFault("invalid_disposition")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self._check_anchor()
            self.db.execute(
                "UPDATE events SET status=?,result_digest=? "
                "WHERE id=? AND session=? AND status='reserved'",
                (status, result_digest, event_id, self.session_id),
            )
            if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                raise AdviceFault("ledger_unavailable")
            self.db.execute("UPDATE meta SET generation=generation+1")
            self.db.execute("COMMIT")
            self._sync_anchor()
        except sqlite3.Error:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise AdviceFault("ledger_unavailable") from None
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def usage(self) -> dict:
        self._check_anchor()
        r = self.db.execute(
            "SELECT calls,tokens,cost FROM sessions WHERE id=?", (self.session_id,)
        ).fetchone()
        return dict(r)
