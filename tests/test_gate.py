from datetime import date
from pathlib import Path

import pytest

from jangus.agents.attempt_tracker import SpecAttemptTracker
from jangus.agents.pricing import ModelPrice
from jangus.agents.providers.base import ModelRequest, Usage
from jangus.agents.runner import Job, RunOutcome
from jangus.config import Caps
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import read_alerts
from jangus.kernel.attempts import Ladder, TaskAttempts, TaskLimits
from jangus.kernel.budgets import Dimension, Limits
from tests.agent_harness import Harness, make_spec, reply

# One call to opus-tag costs 15 per 1000 input tokens and 75 per 1000 output tokens.
BIG_USAGE = Usage(input_tokens=1000, output_tokens=1000)  # 15_000 + 75_000 = 90_000 micros


def usd_caps(daily: float, monthly: float, **kw: float) -> Caps:
    """Caps for a $100 plan, so a percentage equals a dollar amount."""
    return Caps(plan_limit_usd=100, daily_percent=daily, monthly_percent=monthly, **kw)


CAPS = usd_caps(0.20, 5.0)  # $0.20 = 200_000 micros


def paid_spec(**kw: object) -> object:
    return make_spec(provider="claude", model="opus-tag", escalation=False, **kw)  # type: ignore[arg-type]


def paid(tmp_path: Path, caps: Caps | None = CAPS, **kw: object) -> Harness:
    h = Harness(tmp_path, spec=paid_spec(**kw), caps=caps)  # type: ignore[arg-type]
    h.escalation.usage = BIG_USAGE
    return h


def codes(h: Harness) -> list[str]:
    return [a.code for a in read_alerts(h.ledger)]


# ---- B39 pre-call budget check -------------------------------------------------


def test_call_whose_worst_case_exceeds_the_agent_budget_is_refused_before_sending(
    tmp_path: Path,
) -> None:
    h = paid(tmp_path, tokens=600)  # worst case is ~500 prompt + 512 output tokens
    h.escalation.script.append(reply(done=True))
    report = h.runner.run(Job("j1", "x"))
    assert report.outcome is RunOutcome.REFUSED
    assert h.escalation.requests == []  # refused before anything was sent
    assert any(f.startswith("over_budget:agent:file-agent/tokens") for f in report.failures)
    assert "call_refused" in codes(h) and h.events("call_refused")


def test_refusal_counts_as_a_failed_attempt_and_is_never_downgraded(tmp_path: Path) -> None:
    h = paid(tmp_path, tokens=600)
    attempts = TaskAttempts(h.state, h.ledger, h.alerts, "t/1", TaskLimits(6, 12), Ladder())
    tracker = SpecAttemptTracker(h.spec, attempts)
    h.runner.run(Job("j1", "x"), attempts=tracker)
    assert attempts.failed() == 1
    assert h.provider.requests == []  # the cheaper local model was not silently used instead


@pytest.mark.parametrize("scope_name", ["directive", "day", "month"])
def test_each_scope_limit_refuses_an_over_budget_call(tmp_path: Path, scope_name: str) -> None:
    caps = {
        "directive": CAPS,
        "day": usd_caps(0.01, 5.0),  # 10_000 micros < worst case
        "month": usd_caps(5.0, 5.0),
    }[scope_name]
    h = paid(tmp_path, caps=caps)
    extra: tuple[str, ...] = ()
    if scope_name == "month":  # the month is nearly used up, the day is fresh
        assert h.gate is not None
        h.gate.period_scopes()
        h.budgets.charge("month:2026-11", {Dimension.USD_MICROS: 4_990_000}, reason="earlier spend")
    if scope_name == "directive":
        h.budgets.define("directive:d1", Limits(5, 100, 60_000, 5_000_000))  # tokens too small
        extra = ("directive:d1",)
    h.escalation.script.append(reply(done=True))
    report = h.runner.run(Job("j1", "x", extra_scopes=extra))
    assert report.outcome is RunOutcome.REFUSED and h.escalation.requests == []
    expected = {"directive": "directive:d1/tokens", "day": "day:2026-11-03/usd_micros"}.get(
        scope_name, "month:2026-11/usd_micros"
    )
    assert any(expected in f for f in report.failures), report.failures


def test_call_within_every_limit_is_sent(tmp_path: Path) -> None:
    h = paid(tmp_path)
    h.escalation.script.append(reply(done=True))
    assert h.runner.run(Job("j1", "x")).outcome is RunOutcome.MODEL_DONE
    assert len(h.escalation.requests) == 1


def test_paid_call_without_owner_spend_caps_is_refused(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=paid_spec(), caps=usd_caps(1, 1))
    h.gate._caps = None  # type: ignore[union-attr]  # noqa: SLF001 - config with no caps
    h.escalation.script.append(reply(done=True))
    report = h.runner.run(Job("j1", "x"))
    assert report.outcome is RunOutcome.REFUSED and "no_spend_caps" in report.failures
    assert h.escalation.requests == []


def test_local_model_needs_no_caps(tmp_path: Path) -> None:
    h = Harness(tmp_path, caps=CAPS)
    h.provider.script.append(reply(done=True))
    assert h.runner.run(Job("j1", "x")).outcome is RunOutcome.MODEL_DONE


# ---- B40 alerts and the block ---------------------------------------------------


def test_alerts_fire_at_50_and_80_percent_once_each(tmp_path: Path) -> None:
    h = paid(tmp_path)  # daily cap 200_000; each call costs 90_000
    h.escalation.script += [reply(("file.read", {"path": "/work/a"})), reply(done=True)]
    h.runner.run(Job("j1", "x"))  # 2 calls: 180_000 = 90% of the daily cap
    c = codes(h)
    assert c.count("spend_50pct") == 1 and c.count("spend_80pct") == 1
    assert "spend_100pct" not in c and not h.gate.blocked("claude")  # type: ignore[union-attr]


def test_provider_is_blocked_at_100_percent_until_the_owner_acts(tmp_path: Path) -> None:
    h = paid(tmp_path, caps=usd_caps(0.18, 5.0))  # exactly two calls
    h.escalation.script += [reply(("file.read", {"path": "/work/a"})), reply(done=True)]
    h.runner.run(Job("j1", "x"))
    assert "spend_100pct" in codes(h) and h.gate.blocked("claude")  # type: ignore[union-attr]
    assert h.events("provider_blocked")

    h.escalation.script.append(reply(done=True))
    again = h.runner.run(Job("j2", "x"))  # the exhausted daily budget stops the run first
    assert again.outcome is RunOutcome.BUDGET and again.model_calls == 0
    assert len(h.escalation.requests) == 2  # nothing more was sent

    verdict = h.gate.check(  # type: ignore[union-attr]
        scopes=[],
        provider="claude",
        price=ModelPrice(1, 1),  # type: ignore[arg-type]
        request=ModelRequest(model="m", system="s", user="u", max_tokens=1),
    )
    assert not verdict.allowed and verdict.reason == "provider_blocked"

    h.gate.unblock("claude", "owner")  # type: ignore[union-attr]
    assert not h.gate.blocked("claude")  # type: ignore[union-attr]
    assert h.events("provider_unblocked")[0]["by"] == "owner"
    # unblocking does not raise the cap: the exhausted daily budget still stops paid work
    assert h.runner.run(Job("j3", "x")).outcome is RunOutcome.BUDGET


def test_block_survives_a_restart_and_is_enforced_by_the_hub(tmp_path: Path) -> None:
    h = paid(tmp_path, caps=usd_caps(0.18, 5.0))
    h.escalation.script += [reply(("file.read", {"path": "/work/a"})), reply(done=True)]
    h.runner.run(Job("j1", "x"))
    h.state.close()
    h2 = paid(tmp_path, caps=usd_caps(0.18, 5.0))
    assert h2.gate.blocked("claude")  # type: ignore[union-attr]
    with pytest.raises(RuntimeFault) as info:  # even code that skips the gate cannot call it
        h2.hub.complete("claude", ModelRequest(model="m", system="s", user="u", max_tokens=1))
    assert info.value.code is FaultCode.PROVIDER_BLOCKED
    assert h2.escalation.requests == []


def test_local_provider_is_not_blocked_by_paid_spend(tmp_path: Path) -> None:
    h = paid(tmp_path, caps=usd_caps(0.18, 5.0))
    h.escalation.script += [reply(("file.read", {"path": "/work/a"})), reply(done=True)]
    h.runner.run(Job("j1", "x"))
    assert not h.gate.blocked("ollama")  # type: ignore[union-attr]


def test_monthly_cap_alerts_too(tmp_path: Path) -> None:
    h = paid(tmp_path, caps=usd_caps(0.18, 0.18))
    h.escalation.script += [reply(("file.read", {"path": "/work/a"})), reply(done=True)]
    h.runner.run(Job("j1", "x"))
    alerts = [a for a in read_alerts(h.ledger) if a.code == "spend_100pct"]
    assert "month:2026-11" in {a.detail["scope"] for a in alerts}


def test_big_call_flag(tmp_path: Path) -> None:
    h = paid(tmp_path, caps=usd_caps(5.0, 50.0, big_call_tokens=1500))
    h.escalation.script.append(reply(done=True))
    h.runner.run(Job("j1", "x"))  # 2000 tokens in one call
    assert "big_call" in codes(h)


def test_runaway_task_is_flagged_against_the_median_of_its_type(tmp_path: Path) -> None:
    h = Harness(tmp_path, caps=CAPS)
    h.provider.script += [reply(done=True)] * 4
    for i in range(3):  # three ordinary tasks of one type: 120 tokens each
        h.runner.run(Job(f"j{i}", "x", task_label=f"t{i}", task_type="sort"))
    assert "runaway_task" not in codes(h)
    h.provider.usage = Usage(input_tokens=2000, output_tokens=500)
    h.runner.run(Job("j9", "x", task_label="t9", task_type="sort"))
    flagged = [a for a in read_alerts(h.ledger) if a.code == "runaway_task"]
    assert len(flagged) == 1 and flagged[0].detail["task"] == "t9"


def test_estimator_drift_is_flagged_once_per_model(tmp_path: Path) -> None:
    h = Harness(
        tmp_path, caps=CAPS
    )  # scripted usage says 100 input tokens; the estimate is far off
    h.provider.script += [reply(done=True)] * 2
    h.runner.run(Job("j1", "x"))
    h.runner.run(Job("j2", "x"))
    assert codes(h).count("estimator_drift") == 1


def test_daily_scope_rolls_over_with_the_date(tmp_path: Path) -> None:
    h = paid(tmp_path, caps=usd_caps(0.18, 5.0))
    h.escalation.script += [reply(("file.read", {"path": "/work/a"})), reply(done=True)]
    h.runner.run(Job("j1", "x"))
    assert h.budgets.exhausted("day:2026-11-03") == [Dimension.USD_MICROS]
    assert h.gate is not None
    h.gate._today = lambda: date(2026, 11, 4)  # noqa: SLF001 - the next day
    h.gate.unblock("claude", "owner")
    assert h.gate.period_scopes()[0] == "day:2026-11-04"
    assert h.budgets.exhausted("day:2026-11-04") == []


def test_caps_are_percentages_of_the_plan_limit() -> None:
    caps = Caps(plan_limit_usd=200, monthly_percent=80, daily_percent=5)
    assert caps.monthly_usd == 160.0 and caps.daily_usd == 10.0
    assert caps.big_call_usd == 2.5  # 25% of the daily cap by default
    assert caps.big_call_tokens == 20_000


def test_caps_default_to_staying_below_the_limit() -> None:
    caps = Caps(plan_limit_usd=100)
    assert caps.monthly_usd == 80.0 and caps.daily_usd == 5.0


def test_caps_can_never_exceed_the_plan() -> None:
    from pydantic import ValidationError

    for bad in (
        {"plan_limit_usd": 100, "monthly_percent": 101},
        {"plan_limit_usd": 100, "monthly_percent": 0},
        {"plan_limit_usd": 100, "daily_percent": 101},
        {"plan_limit_usd": 100, "monthly_percent": 10, "daily_percent": 20},  # daily > monthly
        {"plan_limit_usd": 0},
        {"plan_limit_usd": -5},
        {"plan_limit_usd": 100, "surprise": 1},
        {"monthly_percent": 50},  # no plan limit given: not guessed
    ):
        with pytest.raises(ValidationError):
            Caps(**bad)  # type: ignore[arg-type]
