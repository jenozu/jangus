"""Durable budgets for calls, tokens, wall time and spend (B08).

Every budget is a row in the state database (`budgets`: name, limit, spent), so an
exhausted budget stays exhausted after a restart or a new session. Four dimensions
are tracked per *scope*, a plain string such as `agent:files`, `directive:abc123`,
`day:2026-11-03` or `month:2026-11`. Period scopes carry the period in their name,
so a new day or month simply starts a new scope: nothing is ever reset in place.

All amounts are integers: calls, tokens, milliseconds and micro-dollars.
Charges are all-or-nothing across dimensions. Hitting a limit raises
`budget_exceeded` and queues an alert once. Defining limits again never lowers
`spent`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import AlertLevel, AlertQueue
from jangus.kernel.ledger import Ledger
from jangus.kernel.state import BudgetRecord, StateStore

MICROS_PER_USD = 1_000_000


class Dimension(StrEnum):
    CALLS = "calls"
    TOKENS = "tokens"
    WALL_MS = "wall_ms"
    USD_MICROS = "usd_micros"


def limit_micros(usd: float) -> int:
    """A dollar limit as micro-dollars, rounded down (never grants more than was set)."""
    return int((Decimal(str(usd)) * MICROS_PER_USD).to_integral_value(rounding=ROUND_FLOOR))


def cost_micros(usd: float) -> int:
    """A dollar cost as micro-dollars, rounded up (never under-charges)."""
    return int((Decimal(str(usd)) * MICROS_PER_USD).to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class Limits:
    calls: int
    tokens: int
    wall_ms: int
    usd_micros: int

    @classmethod
    def from_spec(cls, calls: int, tokens: int, wall_seconds: int, spend_usd: float) -> "Limits":
        return cls(calls, tokens, wall_seconds * 1000, limit_micros(spend_usd))

    def as_dict(self) -> dict[Dimension, int]:
        return {
            Dimension.CALLS: self.calls,
            Dimension.TOKENS: self.tokens,
            Dimension.WALL_MS: self.wall_ms,
            Dimension.USD_MICROS: self.usd_micros,
        }


def budget_name(scope: str, dimension: Dimension) -> str:
    return f"{scope}/{dimension.value}"


class BudgetBook:
    def __init__(self, state: StateStore, ledger: Ledger, alerts: AlertQueue) -> None:
        self._state = state
        self._ledger = ledger
        self._alerts = alerts

    def define(self, scope: str, limits: Limits) -> None:
        """Create or change the limits of a scope. Amounts already spent are kept."""
        self._state.set_budgets(
            {budget_name(scope, dim): value for dim, value in limits.as_dict().items()}
        )

    def status(self, scope: str) -> dict[Dimension, BudgetRecord]:
        found = {rec.name: rec for rec in self._state.list_budgets(f"{scope}/")}
        out: dict[Dimension, BudgetRecord] = {}
        for dim in Dimension:
            rec = found.get(budget_name(scope, dim))
            if rec is not None:
                out[dim] = rec
        return out

    def remaining(self, scope: str, dimension: Dimension) -> int:
        rec = self._state.get_budget(budget_name(scope, dimension))
        return rec.limit_amount - rec.spent

    def exhausted(self, scope: str) -> list[Dimension]:
        """Dimensions with nothing left (spent has reached the limit)."""
        return [d for d, rec in self.status(scope).items() if rec.spent >= rec.limit_amount]

    def would_exceed(self, scope: str, amounts: Mapping[Dimension, int]) -> list[Dimension]:
        """Dimensions that `amounts` would push over their limit. Charges nothing."""
        over: list[Dimension] = []
        for dim, amount in amounts.items():
            rec = self._state.get_budget(budget_name(scope, dim))
            if rec.spent + amount > rec.limit_amount:
                over.append(dim)
        return over

    def charge(self, scope: str, amounts: Mapping[Dimension, int], *, reason: str) -> None:
        """Charge all dimensions or none. Raises budget_exceeded (and alerts once) at a cap."""
        wanted = {dim: amount for dim, amount in amounts.items() if amount != 0}
        if not wanted:
            return
        names = {budget_name(scope, dim): amount for dim, amount in wanted.items()}
        try:
            records = self._state.charge_budgets(names)
        except RuntimeFault as fault:
            if fault.code is FaultCode.BUDGET_EXCEEDED:
                self._alert_exhausted(scope, wanted)
            raise
        self._ledger.append(
            {
                "kind": "budget_charge",
                "scope": scope,
                "reason": reason,
                "amounts": {dim.value: amount for dim, amount in wanted.items()},
            }
        )
        for rec in records:
            if rec.spent >= rec.limit_amount:
                self._alerts.raise_alert(
                    "budget_reached",
                    f"budget {rec.name} reached its limit",
                    level=AlertLevel.WARNING,
                    detail={"budget": rec.name, "limit": rec.limit_amount},
                    once=f"reached:{rec.name}:{rec.limit_amount}",
                )

    def _alert_exhausted(self, scope: str, wanted: Mapping[Dimension, int]) -> None:
        for dim, amount in wanted.items():
            name = budget_name(scope, dim)
            try:
                rec = self._state.get_budget(name)
            except RuntimeFault:
                continue  # no such budget: the original fault already says so
            if rec.spent + amount <= rec.limit_amount:
                continue
            # The limit is in the key, so raising a limit re-arms the alert.
            self._alerts.raise_alert(
                "budget_exhausted",
                f"budget {name} is exhausted: work stops until the owner acts",
                level=AlertLevel.CRITICAL,
                detail={"budget": name, "limit": rec.limit_amount, "spent": rec.spent},
                once=f"exhausted:{name}:{rec.limit_amount}",
            )
