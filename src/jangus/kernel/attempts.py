"""Kernel-counted attempts, hard task limits and the escalation ladder (B31-B34, B36).

Everything here is counted by the kernel from what it observed; nothing a model says can
change a count, a limit or the model choice. All counters live in the state database, keyed
by a *task key*, so a restart, a retry or a re-issued (superseding) directive never resets
them: a task never gets a fresh budget.

One failed attempt is counted per round (one model reply and what came of it). Failure kinds
that count: a rejected or failed call, a failed verification, an unparseable reply, a repeated
proposal, a no-progress cycle (three results with the same hash and no new evidence), or a
hard limit hit (`max_model_calls`, `max_proposals`, wall time).

Ladder: `default_model` failed attempts on the default model, then `step_up` on the stronger
one, then STOP: the task is deferred and an alert is queued. Only this module moves a task
up the ladder.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from jangus.kernel.alerts import AlertLevel, AlertQueue
from jangus.kernel.ledger import Ledger
from jangus.kernel.state import StateStore

NO_PROGRESS_STREAK = 3
MAX_REASONS_KEPT = 8
_MAX_LINEAGE_HOPS = 1000

REPEAT = "repeat_proposal"
NO_PROGRESS = "no_progress"


@dataclass(frozen=True)
class RoundResult:
    failed_total: int
    reasons: tuple[str, ...]  # every failure recorded for the round, including kernel-added ones


class Step(StrEnum):
    DEFAULT = "default"
    STEP_UP = "step_up"
    STOP = "stop"


@dataclass(frozen=True)
class TaskLimits:
    max_model_calls: int
    max_proposals: int
    max_wall_seconds: int | None = None

    @classmethod
    def narrowed(
        cls,
        *,
        spec_model_calls: int,
        spec_proposals: int,
        spec_wall_seconds: int | None,
        task_model_calls: int | None = None,
        task_proposals: int | None = None,
        task_wall_seconds: int | None = None,
    ) -> "TaskLimits":
        """A task may only tighten the spec's limits, never loosen them."""

        def tighter(spec: int | None, task: int | None) -> int | None:
            if spec is None:
                return task
            return spec if task is None else min(spec, task)

        calls = tighter(spec_model_calls, task_model_calls)
        props = tighter(spec_proposals, task_proposals)
        assert calls is not None and props is not None  # noqa: S101 - spec values are required
        return cls(calls, props, tighter(spec_wall_seconds, task_wall_seconds))


@dataclass(frozen=True)
class Ladder:
    default_model: int = 2
    step_up: int = 1  # 0 when the spec names no escalation model


def lineage_root(ledger: Ledger, directive_id: str) -> str:
    """The first directive in a chain of `supersedes` links, read from the ledger."""
    parent: dict[str, str] = {}
    for entry in ledger.entries():
        event = entry.event
        if event.get("kind") == "directive_issued" and event.get("supersedes"):
            parent[str(event["id"])] = str(event["supersedes"])
    current = directive_id
    for _ in range(_MAX_LINEAGE_HOPS):
        if current not in parent:
            return current
        current = parent[current]
    return current


def task_key(root: str, task_id: int | str) -> str:
    return f"{root}/{task_id}"


def _add(conn: sqlite3.Connection, key: str, name: str, delta: int) -> int:
    conn.execute(
        "INSERT INTO task_counters(task_key, name, value) VALUES (?, ?, ?) "
        "ON CONFLICT(task_key, name) DO UPDATE SET value = value + excluded.value",
        (key, name, delta),
    )
    return _get(conn, key, name)


def _get(conn: sqlite3.Connection, key: str, name: str) -> int:
    row = conn.execute(
        "SELECT value FROM task_counters WHERE task_key=? AND name=?", (key, name)
    ).fetchone()
    return 0 if row is None else int(row[0])


def _text(conn: sqlite3.Connection, key: str, name: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM task_text WHERE task_key=? AND name=?", (key, name)
    ).fetchone()
    return None if row is None else str(row[0])


def _set_text(conn: sqlite3.Connection, key: str, name: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM task_text WHERE task_key=? AND name=?", (key, name))
    else:
        conn.execute(
            "INSERT INTO task_text(task_key, name, value) VALUES (?, ?, ?) "
            "ON CONFLICT(task_key, name) DO UPDATE SET value=excluded.value",
            (key, name, value),
        )


class TaskAttempts:
    def __init__(
        self,
        state: StateStore,
        ledger: Ledger,
        alerts: AlertQueue,
        key: str,
        limits: TaskLimits,
        ladder: Ladder,
    ) -> None:
        self._state = state
        self._ledger = ledger
        self._alerts = alerts
        self.key = key
        self._limits = limits
        self._ladder = ladder

    # -- reading -----------------------------------------------------------

    def failed(self) -> int:
        rows = self._state.query(
            "SELECT value FROM task_counters WHERE task_key=? AND name='failed'", (self.key,)
        )
        return int(rows[0][0]) if rows else 0

    def step(self) -> Step:
        failed = self.failed()
        if failed < self._ladder.default_model:
            return Step.DEFAULT
        if failed < self._ladder.default_model + self._ladder.step_up:
            return Step.STEP_UP
        return Step.STOP

    def exhausted_reason(self) -> str | None:
        if self.step() is not Step.STOP:
            return None
        reasons = self.failure_reasons(3)
        return "attempts exhausted: " + (", ".join(reasons) if reasons else "no reason recorded")

    def failure_reasons(self, limit: int = 5) -> Sequence[str]:
        rows = self._state.query(
            "SELECT reason FROM task_failures WHERE task_key=? ORDER BY id DESC LIMIT ?",
            (self.key, limit),
        )
        return [str(r[0]) for r in reversed(rows)]

    def counters(self) -> dict[str, int]:
        rows = self._state.query(
            "SELECT name, value FROM task_counters WHERE task_key=?", (self.key,)
        )
        return {str(n): int(v) for n, v in rows}

    # -- counting ----------------------------------------------------------

    def note_model_call(self) -> str | None:
        count = self._state.transaction(lambda c: _add(c, self.key, "model_calls", 1))
        return "limit:max_model_calls" if count > self._limits.max_model_calls else None

    def note_proposal(self, proposal_hash: str) -> str | None:
        def work(conn: sqlite3.Connection) -> str | None:
            count = _add(conn, self.key, "proposals", 1)
            cur = conn.execute(
                "INSERT OR IGNORE INTO task_proposals(task_key, proposal_hash) VALUES (?, ?)",
                (self.key, proposal_hash),
            )
            if count > self._limits.max_proposals:
                return "limit:max_proposals"
            return REPEAT if cur.rowcount == 0 else None

        return self._state.transaction(work)

    def note_result(self, proposal_hash: str, result_hash: str | None) -> None:
        if result_hash is None:
            return

        def work(conn: sqlite3.Connection) -> None:
            fresh = (
                conn.execute(
                    "INSERT OR IGNORE INTO task_results(task_key, result_hash) VALUES (?, ?)",
                    (self.key, result_hash),
                ).rowcount
                == 1
            )
            same_as_last = _text(conn, self.key, "last_result") == result_hash
            _set_text(conn, self.key, "last_result", result_hash)
            streak = _get(conn, self.key, "streak")
            if same_as_last and not fresh:
                _add(conn, self.key, "streak", 1)
                streak += 1
            else:
                conn.execute(
                    "INSERT INTO task_counters(task_key, name, value) VALUES (?, 'streak', 1) "
                    "ON CONFLICT(task_key, name) DO UPDATE SET value=1",
                    (self.key,),
                )
                streak = 1
            if streak >= NO_PROGRESS_STREAK:
                _set_text(conn, self.key, "pending_failure", NO_PROGRESS)
                conn.execute(
                    "UPDATE task_counters SET value=0 WHERE task_key=? AND name='streak'",
                    (self.key,),
                )

        self._state.transaction(work)

    def note_elapsed_ms(self, elapsed_ms: int) -> str | None:
        total = self._state.transaction(lambda c: _add(c, self.key, "wall_ms", max(0, elapsed_ms)))
        limit = self._limits.max_wall_seconds
        return "limit:max_wall_seconds" if limit is not None and total > limit * 1000 else None

    def end_round(self, failures: Sequence[str]) -> RoundResult:
        """Close a round. All failures in it (and a pending no-progress) are ONE failed attempt."""

        def work(conn: sqlite3.Connection) -> tuple[int, list[str]]:
            reasons = list(dict.fromkeys(failures))
            pending = _text(conn, self.key, "pending_failure")
            if pending is not None:
                _set_text(conn, self.key, "pending_failure", None)
                if pending not in reasons:
                    reasons.append(pending)
            if not reasons:
                return _get(conn, self.key, "failed"), []
            total = _add(conn, self.key, "failed", 1)
            for reason in reasons[:MAX_REASONS_KEPT]:
                conn.execute(
                    "INSERT INTO task_failures(task_key, reason) VALUES (?, ?)", (self.key, reason)
                )
            return total, reasons

        total, reasons = self._state.transaction(work)
        if reasons:
            self._after_failure(total, reasons)
        return RoundResult(total, tuple(reasons))

    def record_failure(self, reason: str) -> RoundResult:
        """A failure the kernel saw outside a model round, such as a failed verification."""
        return self.end_round([reason])

    # -- consequences ------------------------------------------------------

    def _after_failure(self, total: int, reasons: Sequence[str]) -> None:
        self._ledger.append(
            {
                "kind": "attempt_failed",
                "task": self.key,
                "failed_attempts": total,
                "reasons": list(reasons)[:MAX_REASONS_KEPT],
            }
        )
        if total == self._ladder.default_model and self._ladder.step_up > 0:
            self._ledger.append({"kind": "step_up", "task": self.key, "after_failed": total})
        if self.step() is Step.STOP:
            self._alerts.raise_alert(
                "attempts_exhausted",
                f"task {self.key} used all attempts: it is deferred and needs the owner",
                level=AlertLevel.CRITICAL,
                detail={"task": self.key, "failed_attempts": total, "reasons": list(reasons)[:3]},
                once=f"attempts_exhausted:{self.key}",
            )
