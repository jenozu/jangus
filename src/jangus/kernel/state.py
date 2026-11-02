"""Durable kernel state: runs, actions and budgets in SQLite.

WAL with synchronous=FULL. Intent and the budget charge are written in one
transaction before any side effect. If the database cannot be written, the
store blocks every further write (fail closed).
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

from jangus.errors import FaultCode, RuntimeFault

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
CREATE TABLE runs (
    id INTEGER PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
) STRICT;
CREATE TABLE actions (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    tool TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'intent',
    result_hash TEXT
) STRICT;
CREATE TABLE approvals (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    tool TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    decided_by TEXT,
    decided_at INTEGER
) STRICT;
CREATE TABLE budgets (
    name TEXT PRIMARY KEY,
    limit_amount INTEGER NOT NULL CHECK (limit_amount >= 0),
    spent INTEGER NOT NULL DEFAULT 0 CHECK (spent >= 0)
) STRICT;
"""

ActionStatus = Literal["intent", "done", "failed", "unresolved"]
T = TypeVar("T")


@dataclass(frozen=True)
class RunRecord:
    id: int
    idempotency_key: str
    kind: str
    status: str


@dataclass(frozen=True)
class ActionRecord:
    id: int
    run_id: int
    idempotency_key: str
    tool: str
    payload_hash: str
    args_json: str
    attempts: int
    status: str
    result_hash: str | None


@dataclass(frozen=True)
class BudgetRecord:
    name: str
    limit_amount: int
    spent: int


class StateStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._blocked = False

    @classmethod
    def open(cls, path: Path) -> "StateStore":
        try:
            conn = sqlite3.connect(path, isolation_level=None)
            conn.execute("PRAGMA foreign_keys=ON")
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise RuntimeFault(FaultCode.STATE_UNAVAILABLE, "WAL mode unavailable")
            conn.execute("PRAGMA synchronous=FULL")
            version = cls._init_or_check(conn)
        except sqlite3.Error as exc:
            raise RuntimeFault(FaultCode.STATE_UNAVAILABLE, f"cannot open state: {exc}") from exc
        if version != SCHEMA_VERSION:
            conn.close()
            raise RuntimeFault(
                FaultCode.STATE_SCHEMA_MISMATCH,
                f"state schema is {version}, expected {SCHEMA_VERSION}",
            )
        return cls(conn)

    @staticmethod
    def _init_or_check(conn: sqlite3.Connection) -> int:
        conn.execute("BEGIN IMMEDIATE")
        try:
            has_meta = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
            ).fetchone()
            if has_meta is None:
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return int(row[0])

    @property
    def writable(self) -> bool:
        """False once any write has failed; the kernel must start no new effects."""
        return not self._blocked

    def close(self) -> None:
        self._conn.close()

    # -- generic helpers for kernel modules built on the same database ----

    def transaction(self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Run `work` in one write transaction; any failure blocks later writes."""
        return self._write(work)

    def query(self, sql: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        try:
            return [tuple(row) for row in self._conn.execute(sql, params).fetchall()]
        except sqlite3.Error as exc:
            raise RuntimeFault(FaultCode.STATE_UNAVAILABLE, f"cannot read state: {exc}") from exc

    # -- internals ---------------------------------------------------------

    def _write(self, work: Callable[[sqlite3.Connection], T]) -> T:
        if self._blocked:
            raise RuntimeFault(FaultCode.STATE_UNAVAILABLE, "state is blocked after a failed write")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            self._blocked = True
            raise RuntimeFault(FaultCode.STATE_UNAVAILABLE, f"cannot write state: {exc}") from exc
        try:
            result = work(self._conn)
            self._conn.execute("COMMIT")
            return result
        except RuntimeFault:
            self._rollback()
            raise
        except sqlite3.Error as exc:
            self._rollback()
            self._blocked = True
            raise RuntimeFault(FaultCode.STATE_UNAVAILABLE, f"cannot write state: {exc}") from exc

    def _rollback(self) -> None:
        try:
            self._conn.execute("ROLLBACK")
        except sqlite3.Error:
            self._blocked = True

    # -- runs --------------------------------------------------------------

    def create_run(self, idempotency_key: str, kind: str) -> RunRecord:
        def work(conn: sqlite3.Connection) -> RunRecord:
            row = conn.execute(
                "SELECT id, idempotency_key, kind, status FROM runs WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO runs(idempotency_key, kind) VALUES (?, ?)",
                    (idempotency_key, kind),
                )
                row = conn.execute(
                    "SELECT id, idempotency_key, kind, status FROM runs WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
            elif row[2] != kind:
                raise RuntimeFault(
                    FaultCode.IDEMPOTENCY_CONFLICT, "run key already used for a different kind"
                )
            return RunRecord(*row)

        return self._write(work)

    # -- budgets -----------------------------------------------------------

    def set_budget(self, name: str, limit_amount: int) -> BudgetRecord:
        def work(conn: sqlite3.Connection) -> BudgetRecord:
            conn.execute(
                "INSERT INTO budgets(name, limit_amount) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET limit_amount=excluded.limit_amount",
                (name, limit_amount),
            )
            return self._budget(conn, name)

        return self._write(work)

    @staticmethod
    def _budget(conn: sqlite3.Connection, name: str) -> BudgetRecord:
        row = conn.execute(
            "SELECT name, limit_amount, spent FROM budgets WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            raise RuntimeFault(FaultCode.BUDGET_EXCEEDED, f"no budget named {name!r}")
        return BudgetRecord(*row)

    def get_budget(self, name: str) -> BudgetRecord:
        try:
            return self._budget(self._conn, name)
        except sqlite3.Error as exc:
            raise RuntimeFault(FaultCode.STATE_UNAVAILABLE, f"cannot read state: {exc}") from exc

    # -- actions -----------------------------------------------------------

    def record_intent(
        self,
        *,
        run_id: int,
        idempotency_key: str,
        tool: str,
        payload_hash: str,
        budget: str,
        cost: int,
        args_json: str = "{}",
    ) -> tuple[ActionRecord, bool]:
        """Persist intent and charge the budget in one transaction.

        Returns (record, created). A repeated key with the same payload returns
        the existing record with created=False and charges nothing. The same
        key with a different payload is a conflict.
        """

        def work(conn: sqlite3.Connection) -> tuple[ActionRecord, bool]:
            existing = self._action_by_key(conn, idempotency_key)
            if existing is not None:
                if existing.payload_hash != payload_hash or existing.tool != tool:
                    raise RuntimeFault(
                        FaultCode.IDEMPOTENCY_CONFLICT,
                        "action key already used for a different payload",
                    )
                return existing, False
            if cost < 0:
                raise RuntimeFault(FaultCode.BUDGET_EXCEEDED, "negative cost")
            current = self._budget(conn, budget)
            if current.spent + cost > current.limit_amount:
                raise RuntimeFault(
                    FaultCode.BUDGET_EXCEEDED, f"budget {budget!r} would be exceeded"
                )
            conn.execute("UPDATE budgets SET spent = spent + ? WHERE name=?", (cost, budget))
            conn.execute(
                "INSERT INTO actions(run_id, idempotency_key, tool, payload_hash, args_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, idempotency_key, tool, payload_hash, args_json),
            )
            created = self._action_by_key(conn, idempotency_key)
            assert created is not None  # noqa: S101 - just inserted in this transaction
            return created, True

        return self._write(work)

    @staticmethod
    def _action_by_key(conn: sqlite3.Connection, key: str) -> ActionRecord | None:
        row = conn.execute(
            "SELECT id, run_id, idempotency_key, tool, payload_hash, args_json, attempts, "
            "status, result_hash FROM actions WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        return None if row is None else ActionRecord(*row)

    def get_action(self, idempotency_key: str) -> ActionRecord | None:
        try:
            return self._action_by_key(self._conn, idempotency_key)
        except sqlite3.Error as exc:
            raise RuntimeFault(FaultCode.STATE_UNAVAILABLE, f"cannot read state: {exc}") from exc

    def list_open_actions(self) -> list[ActionRecord]:
        """Actions whose intent was recorded but whose result was not (after a crash)."""
        try:
            rows = self._conn.execute(
                "SELECT id, run_id, idempotency_key, tool, payload_hash, args_json, attempts, "
                "status, result_hash FROM actions WHERE status='intent' ORDER BY id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeFault(FaultCode.STATE_UNAVAILABLE, f"cannot read state: {exc}") from exc
        return [ActionRecord(*row) for row in rows]

    def record_result(
        self, idempotency_key: str, status: ActionStatus, result_hash: str | None
    ) -> ActionRecord:
        def work(conn: sqlite3.Connection) -> ActionRecord:
            cur = conn.execute(
                "UPDATE actions SET status=?, result_hash=? WHERE idempotency_key=?",
                (status, result_hash, idempotency_key),
            )
            if cur.rowcount != 1:
                raise RuntimeFault(FaultCode.IDEMPOTENCY_CONFLICT, "no intent recorded for key")
            record = self._action_by_key(conn, idempotency_key)
            assert record is not None  # noqa: S101 - row was just updated
            return record

        return self._write(work)
