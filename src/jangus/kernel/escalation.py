"""Daily cap on calls to the orchestrator (V2-FR25, B28).

The orchestrator is escalation-only. Each allowed escalation is charged to a per-day budget
that is stored durably, so a restart does not reset it. At the cap the escalation is not
made: it is written to the ledger as queued for the owner and an alert is raised. The cap
never raises itself.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import AlertLevel, AlertQueue
from jangus.kernel.budgets import BudgetBook, Dimension
from jangus.kernel.ledger import Ledger
from jangus.kernel.state import StateStore


def _utc_today() -> date:
    return datetime.now(UTC).date()


class EscalationCap:
    def __init__(
        self,
        *,
        state: StateStore,
        ledger: Ledger,
        budgets: BudgetBook,
        alerts: AlertQueue,
        daily_limit: int,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        self._state = state
        self._ledger = ledger
        self._budgets = budgets
        self._alerts = alerts
        self._limit = daily_limit
        self._today = today

    def _scope(self) -> str:
        return f"day:{self._today().isoformat()}:escalations"

    def allow(self, directive_id: str, reason: str) -> bool:
        scope = self._scope()
        self._budgets.define_one(scope, Dimension.ESCALATIONS, self._limit)
        try:
            self._budgets.charge(scope, {Dimension.ESCALATIONS: 1}, reason="escalation")
        except RuntimeFault as fault:
            if fault.code is not FaultCode.BUDGET_EXCEEDED:
                raise
            self._ledger.append(
                {
                    "kind": "escalation_queued",
                    "directive": directive_id,
                    "reason": reason,
                    "note": "daily cap reached: waiting for the owner",
                }
            )
            self._alerts.raise_alert(
                "escalation_cap_reached",
                f"daily orchestrator cap reached; directive {directive_id} waits for the owner",
                level=AlertLevel.WARNING,
                detail={"directive": directive_id, "limit": self._limit},
                once=f"escalation_cap:{scope}:{directive_id}",
            )
            return False
        self._ledger.append(
            {
                "kind": "escalation",
                "directive": directive_id,
                "reason": reason,
                "role": "orchestrator",
            }
        )
        return True
