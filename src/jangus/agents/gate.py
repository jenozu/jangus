"""Pre-call budget gate and spend alerts (B39, B40, V2-FR34, V2-FR35).

Before any model call is sent the gate prices the worst case (estimated input plus the full
`max_tokens` of output) and refuses the call if that would push any scope over a limit: the
agent's, the directive's, the day's or the month's. A refusal sends nothing, raises an alert,
and the caller counts it as a failed attempt; the call is never downgraded to another model.

After a call the gate raises alerts at 50%, 80% and 100% of the daily and monthly spend caps
(each once per period), flags unusually large calls, runaway tasks and estimator drift, and at
100% blocks the provider until the owner unblocks it. The block is enforced by the provider
hub too, so no other code path can bypass it.
"""

import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from jangus.agents.metering import CallCharge, estimate_tokens
from jangus.agents.pricing import ModelPrice, cost_micros
from jangus.agents.providers.base import ModelRequest
from jangus.config import Caps
from jangus.kernel.alerts import AlertLevel, AlertQueue
from jangus.kernel.budgets import BudgetBook, Dimension, Limits, limit_micros
from jangus.kernel.ledger import Ledger
from jangus.kernel.state import StateStore

THRESHOLDS = (50, 80, 100)
RUNAWAY_FACTOR = 3
RUNAWAY_MIN_SAMPLES = 3
DRIFT_FRACTION = 0.30


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""


def _utc_today() -> date:
    return datetime.now(UTC).date()


class CallGate:
    def __init__(
        self,
        *,
        state: StateStore,
        ledger: Ledger,
        budgets: BudgetBook,
        alerts: AlertQueue,
        caps: Caps | None,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        self._state = state
        self._ledger = ledger
        self._budgets = budgets
        self._alerts = alerts
        self._caps = caps
        self._today = today

    # -- scopes ------------------------------------------------------------

    def period_scopes(self) -> list[str]:
        """The day and month scopes, defined from the owner's caps. Empty without caps."""
        if self._caps is None:
            return []
        today = self._today()
        day, month = f"day:{today.isoformat()}", f"month:{today.strftime('%Y-%m')}"
        self._budgets.define(day, Limits.usd_only(limit_micros(self._caps.daily_usd)))
        self._budgets.define(month, Limits.usd_only(limit_micros(self._caps.monthly_usd)))
        return [day, month]

    # -- provider block ----------------------------------------------------

    def blocked(self, provider: str) -> bool:
        return self._state.has_flag(f"provider_blocked:{provider}")

    def unblock(self, provider: str, owner_id: str) -> None:
        """Owner action: lift the block. Raising a cap is a separate owner action."""
        self._state.clear_flag(f"provider_blocked:{provider}")
        self._ledger.append({"kind": "provider_unblocked", "provider": provider, "by": owner_id})

    # -- before the call ---------------------------------------------------

    def check(
        self,
        *,
        scopes: Sequence[str],
        provider: str,
        price: ModelPrice,
        request: ModelRequest,
    ) -> GateDecision:
        if self.blocked(provider):
            return self._refuse("provider_blocked", provider, f"{provider} is blocked")
        paid = price.input_per_mtok > 0 or price.output_per_mtok > 0
        if paid and self._caps is None:
            return self._refuse("no_spend_caps", provider, "a paid call needs owner-set spend caps")
        est_in = estimate_tokens(request.system + request.user)
        worst_tokens = est_in + request.max_tokens
        worst_cost = cost_micros(price, input_tokens=est_in, output_tokens=request.max_tokens)
        amounts = {
            Dimension.CALLS: 1,
            Dimension.TOKENS: worst_tokens,
            Dimension.USD_MICROS: worst_cost,
        }
        over: list[str] = []
        for scope in [*scopes, *self.period_scopes()]:
            over += [f"{scope}/{dim.value}" for dim in self._budgets.would_exceed(scope, amounts)]
        if over:
            return self._refuse(
                "over_budget:" + ",".join(over), provider, f"worst case exceeds {', '.join(over)}"
            )
        return GateDecision(True)

    def _refuse(self, reason: str, provider: str, message: str) -> GateDecision:
        self._alerts.raise_alert(
            "call_refused",
            f"model call refused before sending: {message}",
            level=AlertLevel.CRITICAL,
            detail={"provider": provider, "reason": reason},
        )
        self._ledger.append({"kind": "call_refused", "provider": provider, "reason": reason})
        return GateDecision(False, reason)

    # -- after the call ----------------------------------------------------

    def after_call(
        self,
        *,
        provider: str,
        model: str,
        charge: CallCharge,
        cost_usd_micros: int,
        task_label: str,
        task_type: str | None,
        paid: bool,
    ) -> None:
        self._flag_big_call(model, charge, cost_usd_micros)
        self._flag_drift(model, charge)
        self._flag_runaway(task_label, task_type)
        self._alert_thresholds(provider, paid)

    def _flag_big_call(self, model: str, charge: CallCharge, cost: int) -> None:
        if self._caps is None:
            return
        if charge.total_tokens > self._caps.big_call_tokens or cost > limit_micros(
            self._caps.big_call_usd
        ):
            self._alerts.raise_alert(
                "big_call",
                f"one call to {model} used {charge.total_tokens} tokens",
                detail={"model": model, "tokens": charge.total_tokens, "cost_usd_micros": cost},
            )

    def _flag_drift(self, model: str, charge: CallCharge) -> None:
        if not charge.usage_known or charge.input_tokens <= 0:
            return
        gap = abs(charge.est_input_tokens - charge.input_tokens) / charge.input_tokens
        if gap > DRIFT_FRACTION:
            self._alerts.raise_alert(
                "estimator_drift",
                f"input estimate for {model} is off by more than 30% of the measured value",
                level=AlertLevel.INFO,
                detail={"estimate": charge.est_input_tokens, "measured": charge.input_tokens},
                once=f"drift:{model}",
            )

    def _flag_runaway(self, task_label: str, task_type: str | None) -> None:
        if task_type is None:
            return
        totals: dict[str, int] = defaultdict(int)
        for entry in self._ledger.entries():
            event = entry.event
            if event.get("kind") == "model_call" and event.get("task_type") == task_type:
                totals[str(event["task"])] += int(event["input_tokens"]) + int(
                    event["output_tokens"]
                )
        others = [v for label, v in totals.items() if label != task_label]
        if len(others) < RUNAWAY_MIN_SAMPLES:
            return
        median = statistics.median(others)
        if median > 0 and totals[task_label] > RUNAWAY_FACTOR * median:
            self._alerts.raise_alert(
                "runaway_task",
                f"task {task_label} used over {RUNAWAY_FACTOR}x the median tokens of its type",
                detail={"task": task_label, "tokens": totals[task_label], "median": int(median)},
                once=f"runaway:{task_label}",
            )

    def _alert_thresholds(self, provider: str, paid: bool) -> None:
        for scope in self.period_scopes():
            rec = self._budgets.status(scope).get(Dimension.USD_MICROS)
            if rec is None or rec.limit_amount <= 0:
                continue
            pct = rec.spent * 100 // rec.limit_amount
            for threshold in THRESHOLDS:
                if pct < threshold:
                    continue
                self._alerts.raise_alert(
                    f"spend_{threshold}pct",
                    f"{scope} spend reached {threshold}% of its cap",
                    level=AlertLevel.CRITICAL if threshold == 100 else AlertLevel.WARNING,
                    detail={"scope": scope, "spent": rec.spent, "limit": rec.limit_amount},
                    once=f"threshold:{scope}:{rec.limit_amount}:{threshold}",
                )
            if paid and pct >= 100 and not self.blocked(provider):
                self._state.set_flag(f"provider_blocked:{provider}")
                self._ledger.append(
                    {"kind": "provider_blocked", "provider": provider, "scope": scope}
                )
