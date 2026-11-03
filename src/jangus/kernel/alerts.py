"""Alert queue backed by the ledger.

An alert is a ledger entry of kind "alert". The ledger is durable and tamper-evident,
so the queue survives restarts and cannot be edited quietly. Delivery (push, email)
is a later phase; until then alerts show up in `jangus status` and the daily report,
both of which read them from the ledger.

Messages carry codes and numbers only: never secrets or untrusted external content.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from jangus.kernel.ledger import Ledger
from jangus.kernel.state import StateStore

AlertFn = Callable[[str, str], None]


class AlertLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    seq: int
    ts: str
    code: str
    level: str
    message: str
    detail: dict[str, Any]


class AlertQueue:
    def __init__(self, ledger: Ledger, state: StateStore | None = None) -> None:
        self._ledger = ledger
        self._state = state

    def raise_alert(
        self,
        code: str,
        message: str,
        *,
        level: AlertLevel = AlertLevel.WARNING,
        detail: Mapping[str, Any] | None = None,
        once: str | None = None,
    ) -> bool:
        """Append an alert. With `once`, a key already seen (even before a restart) is skipped.

        Returns True if an alert was written.
        """
        if once is not None:
            if self._state is None:
                raise ValueError("once= needs a state store")
            if not self._state.mark_once(f"alert:{once}"):
                return False
        self._ledger.append(
            {
                "kind": "alert",
                "code": code,
                "level": level.value,
                "message": message,
                "detail": dict(detail or {}),
            }
        )
        return True

    def as_fn(self) -> AlertFn:
        """Adapter for code that takes a plain (kind, message) callback (the provider hub)."""

        def alert(kind: str, message: str) -> None:
            self.raise_alert(kind, message, level=AlertLevel.CRITICAL)

        return alert


def read_alerts(ledger: Ledger) -> list[Alert]:
    return [
        Alert(
            e.seq,
            e.ts,
            str(e.event["code"]),
            str(e.event["level"]),
            str(e.event["message"]),
            dict(e.event.get("detail", {})),
        )
        for e in ledger.entries()
        if e.event.get("kind") == "alert"
    ]
