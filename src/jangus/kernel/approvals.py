"""Approval queue for T3 actions.

An approval is valid only for the exact run, tool and normalized payload hash it
was requested for, only until it expires, and only once. The ledger entry that
records the owner's decision is written before the state change, so an approval
can never exist without a log entry.
"""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.ledger import Ledger
from jangus.kernel.state import StateStore

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "used"]
# SELECT * relies on the approvals table column order matching ApprovalRecord.


@dataclass(frozen=True)
class ApprovalRecord:
    id: int
    run_id: int
    tool: str
    payload_hash: str
    status: str
    created_at: int
    expires_at: int
    decided_by: str | None
    decided_at: int | None


def _invalid(reason: str) -> RuntimeFault:
    return RuntimeFault(FaultCode.APPROVAL_INVALID, reason)


class ApprovalQueue:
    def __init__(
        self,
        state: StateStore,
        ledger: Ledger,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._state = state
        self._ledger = ledger
        self._clock = clock

    def _get(self, approval_id: int) -> ApprovalRecord:
        rows = self._state.query("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if not rows:
            raise _invalid(f"no approval {approval_id}")
        return ApprovalRecord(*rows[0])

    def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRecord]:
        if status is None:
            rows = self._state.query("SELECT * FROM approvals ORDER BY id")
        else:
            rows = self._state.query(
                "SELECT * FROM approvals WHERE status=? ORDER BY id", (status,)
            )
        return [ApprovalRecord(*r) for r in rows]

    def enqueue(
        self, run_id: int, tool: str, payload_hash: str, ttl_seconds: int = 24 * 3600
    ) -> ApprovalRecord:
        """Queue a T3 action. Re-queueing the same pending payload returns the same record."""
        if ttl_seconds < 1:
            raise _invalid("ttl must be at least one second")
        now = self._clock()
        existing = self._state.query(
            "SELECT * FROM approvals WHERE run_id=? AND tool=? AND payload_hash=? "
            "AND status='pending' AND expires_at>?",
            (run_id, tool, payload_hash, now),
        )
        if existing:
            return ApprovalRecord(*existing[0])
        self._ledger.append(
            {
                "kind": "approval_requested",
                "run": run_id,
                "tool": tool,
                "payload_hash": payload_hash,
            }
        )
        new_id = self._state.transaction(
            lambda c: (
                c.execute(
                    "INSERT INTO approvals(run_id, tool, payload_hash, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (run_id, tool, payload_hash, now, now + ttl_seconds),
                ).lastrowid
            )
        )
        assert new_id is not None  # noqa: S101 - sqlite always returns a rowid after INSERT
        return self._get(new_id)

    def _decide(
        self, approval_id: int, owner_id: str, decision: Literal["approved", "rejected"]
    ) -> ApprovalRecord:
        if not owner_id.strip():
            raise _invalid("an owner id is required")
        record = self._get(approval_id)
        now = self._clock()
        if record.status != "pending":
            raise _invalid(f"approval {approval_id} is {record.status}, not pending")
        if record.expires_at <= now:
            raise _invalid(f"approval {approval_id} has expired")
        self._ledger.append(
            {
                "kind": "approval_decided",
                "id": approval_id,
                "decision": decision,
                "by": owner_id,
                "run": record.run_id,
                "tool": record.tool,
                "payload_hash": record.payload_hash,
            }
        )
        self._state.transaction(
            lambda c: c.execute(
                "UPDATE approvals SET status=?, decided_by=?, decided_at=? "
                "WHERE id=? AND status='pending'",
                (decision, owner_id, now, approval_id),
            )
        )
        return self._get(approval_id)

    def approve(self, approval_id: int, owner_id: str) -> ApprovalRecord:
        return self._decide(approval_id, owner_id, "approved")

    def reject(self, approval_id: int, owner_id: str) -> ApprovalRecord:
        return self._decide(approval_id, owner_id, "rejected")

    def expire_due(self) -> int:
        """Mark pending approvals past their expiry as expired. Returns how many."""
        now = self._clock()
        due = [r for r in self.list("pending") + self.list("approved") if r.expires_at <= now]
        for record in due:
            self._ledger.append({"kind": "approval_expired", "id": record.id})
            self._expire_row(record.id)
        return len(due)

    def _expire_row(self, approval_id: int) -> None:
        def work(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE approvals SET status='expired' WHERE id=? "
                "AND status IN ('pending','approved')",
                (approval_id,),
            )

        self._state.transaction(work)

    def consume(self, run_id: int, tool: str, payload_hash: str) -> ApprovalRecord | None:
        """Use up one valid approval for exactly this run, tool and payload, or return None."""
        now = self._clock()
        rows = self._state.query(
            "SELECT * FROM approvals WHERE run_id=? AND tool=? AND payload_hash=? "
            "AND status='approved' AND expires_at>? ORDER BY id LIMIT 1",
            (run_id, tool, payload_hash, now),
        )
        if not rows:
            return None
        record = ApprovalRecord(*rows[0])
        self._ledger.append({"kind": "approval_used", "id": record.id, "run": run_id})
        changed = self._state.transaction(
            lambda c: (
                c.execute(
                    "UPDATE approvals SET status='used' WHERE id=? AND status='approved'",
                    (record.id,),
                ).rowcount
            )
        )
        return record if changed == 1 else None
