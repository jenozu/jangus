"""Persist consultation reservations before outbound calls without prompt text."""

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

from jangus.config import _checkout_roots
from jangus.execution.files import no_links
from jangus.integrations.advice.contracts import AdviceFault, AdvicePolicy


class ConsultationLedger:
    def __init__(self, directory: Path, session_id: str, policy: AdvicePolicy):
        try:
            if str(uuid.UUID(session_id)) != session_id:
                raise ValueError("noncanonical")
        except ValueError:
            raise AdviceFault("invalid_session") from None
        no_links(directory)
        home = directory.expanduser().resolve()
        if any(
            home.is_relative_to(root) or root.is_relative_to(home) for root in _checkout_roots()
        ):
            raise AdviceFault("ledger_inside_checkout")
        home.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.path = home / "consultations.sqlite3"
        no_links(self.path)
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
        except (sqlite3.Error, OSError):
            if hasattr(self, "db") and self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise AdviceFault("ledger_unavailable") from None
        except BaseException:
            if hasattr(self, "db") and self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()

    def reserve(self, provider: str, tokens: int, cost: int, evidence_digest: str) -> int:
        try:
            self.db.execute("BEGIN IMMEDIATE")
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
            self.db.execute("COMMIT")
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
        try:
            self.db.execute(
                "UPDATE events SET status=?,result_digest=? WHERE id=? AND session=?",
                (status, result_digest, event_id, self.session_id),
            )
            if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                raise AdviceFault("ledger_unavailable")
        except sqlite3.Error:
            raise AdviceFault("ledger_unavailable") from None

    def usage(self) -> dict:
        r = self.db.execute(
            "SELECT calls,tokens,cost FROM sessions WHERE id=?", (self.session_id,)
        ).fetchone()
        return dict(r)
