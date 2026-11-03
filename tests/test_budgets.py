from pathlib import Path

import pytest

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import AlertQueue, read_alerts
from jangus.kernel.budgets import (
    BudgetBook,
    Dimension,
    Limits,
    cost_micros,
    limit_micros,
)
from jangus.kernel.ledger import Ledger
from jangus.kernel.state import StateStore

LIMITS = Limits(calls=3, tokens=1000, wall_ms=60_000, usd_micros=500_000)


def open_all(tmp_path: Path) -> tuple[StateStore, Ledger, BudgetBook]:
    state = StateStore.open(tmp_path / "state.db")
    ledger = Ledger.open(tmp_path / "ledger.jsonl")
    return state, ledger, BudgetBook(state, ledger, AlertQueue(ledger, state))


@pytest.fixture
def book(tmp_path: Path) -> BudgetBook:
    _, _, b = open_all(tmp_path)
    b.define("agent:files", LIMITS)
    return b


def test_charge_and_remaining(book: BudgetBook) -> None:
    book.charge("agent:files", {Dimension.CALLS: 1, Dimension.TOKENS: 400}, reason="t")
    assert book.remaining("agent:files", Dimension.CALLS) == 2
    assert book.remaining("agent:files", Dimension.TOKENS) == 600
    assert book.exhausted("agent:files") == []


@pytest.mark.parametrize(
    ("dimension", "amount"),
    [
        (Dimension.CALLS, 3),
        (Dimension.TOKENS, 1000),
        (Dimension.WALL_MS, 60_000),
        (Dimension.USD_MICROS, 500_000),
    ],
)
def test_exhausted_budget_survives_restart_and_new_session(
    tmp_path: Path, dimension: Dimension, amount: int
) -> None:
    """B08: call, token, time and spend budgets stay exhausted after a restart."""
    state, ledger, book = open_all(tmp_path)
    book.define("agent:files", LIMITS)
    book.charge("agent:files", {dimension: amount}, reason="spend it all")
    assert book.exhausted("agent:files") == [dimension]
    state.close()

    state2, ledger2, book2 = open_all(tmp_path)  # new process, new objects, same files
    assert book2.exhausted("agent:files") == [dimension]
    with pytest.raises(RuntimeFault) as info:
        book2.charge("agent:files", {dimension: 1}, reason="after restart")
    assert info.value.code is FaultCode.BUDGET_EXCEEDED
    # defining the same limits again (a new session starting up) must not reset spent
    book2.define("agent:files", LIMITS)
    assert book2.exhausted("agent:files") == [dimension]
    state2.close()
    assert ledger2.verify_chain() > 0


def test_charge_is_all_or_nothing(book: BudgetBook) -> None:
    with pytest.raises(RuntimeFault):
        book.charge(
            "agent:files", {Dimension.CALLS: 1, Dimension.TOKENS: 5000}, reason="too many tokens"
        )
    assert book.remaining("agent:files", Dimension.CALLS) == 3  # the call was not charged


def test_would_exceed_charges_nothing(book: BudgetBook) -> None:
    assert book.would_exceed("agent:files", {Dimension.TOKENS: 1001}) == [Dimension.TOKENS]
    assert book.would_exceed("agent:files", {Dimension.TOKENS: 1000}) == []
    assert book.remaining("agent:files", Dimension.TOKENS) == 1000


def test_zero_limit_is_exhausted_at_once(tmp_path: Path) -> None:
    _, _, book = open_all(tmp_path)
    book.define("directive:x", Limits(0, 0, 0, 0))
    assert set(book.exhausted("directive:x")) == {
        Dimension.CALLS,
        Dimension.TOKENS,
        Dimension.WALL_MS,
        Dimension.USD_MICROS,
    }
    with pytest.raises(RuntimeFault):
        book.charge("directive:x", {Dimension.CALLS: 1}, reason="t")


def test_negative_and_unknown_scope_refused(book: BudgetBook) -> None:
    with pytest.raises(RuntimeFault):
        book.charge("agent:files", {Dimension.CALLS: -1}, reason="t")
    with pytest.raises(RuntimeFault) as info:
        book.charge("agent:nobody", {Dimension.CALLS: 1}, reason="t")
    assert info.value.code is FaultCode.BUDGET_EXCEEDED


def test_hitting_the_cap_alerts_once(tmp_path: Path) -> None:
    state, ledger, book = open_all(tmp_path)
    book.define("agent:files", LIMITS)
    book.charge("agent:files", {Dimension.CALLS: 3}, reason="t")  # reaches the cap
    for _ in range(3):  # repeated refused attempts must not flood the queue
        with pytest.raises(RuntimeFault):
            book.charge("agent:files", {Dimension.CALLS: 1}, reason="t")
    codes = [a.code for a in read_alerts(ledger)]
    assert codes.count("budget_reached") == 1
    assert codes.count("budget_exhausted") == 1
    state.close()


def test_raising_a_limit_rearms_the_alert(tmp_path: Path) -> None:
    state, ledger, book = open_all(tmp_path)
    book.define("agent:files", Limits(1, 10, 10, 10))
    book.charge("agent:files", {Dimension.CALLS: 1}, reason="t")
    with pytest.raises(RuntimeFault):
        book.charge("agent:files", {Dimension.CALLS: 1}, reason="t")
    book.define("agent:files", Limits(2, 10, 10, 10))  # owner raises the limit
    book.charge("agent:files", {Dimension.CALLS: 1}, reason="t")
    with pytest.raises(RuntimeFault):
        book.charge("agent:files", {Dimension.CALLS: 1}, reason="t")
    assert [a.code for a in read_alerts(ledger)].count("budget_exhausted") == 2
    state.close()


def test_alert_dedupe_survives_restart(tmp_path: Path) -> None:
    state, _, book = open_all(tmp_path)
    book.define("agent:files", Limits(1, 10, 10, 10))
    book.charge("agent:files", {Dimension.CALLS: 1}, reason="t")
    state.close()
    _, ledger2, book2 = open_all(tmp_path)
    with pytest.raises(RuntimeFault):
        book2.charge("agent:files", {Dimension.CALLS: 1}, reason="t")
    assert [a.code for a in read_alerts(ledger2)].count("budget_exhausted") == 1


def test_spend_is_written_to_the_ledger(tmp_path: Path) -> None:
    state, ledger, book = open_all(tmp_path)
    book.define("agent:files", LIMITS)
    book.charge("agent:files", {Dimension.CALLS: 1, Dimension.USD_MICROS: 1234}, reason="call 1")
    charges = [e.event for e in ledger.entries() if e.event["kind"] == "budget_charge"]
    assert charges == [
        {
            "kind": "budget_charge",
            "scope": "agent:files",
            "reason": "call 1",
            "amounts": {"calls": 1, "usd_micros": 1234},
        }
    ]
    state.close()


def test_period_scopes_roll_over_by_name(tmp_path: Path) -> None:
    state, _, book = open_all(tmp_path)
    book.define("day:2026-11-03", Limits(1, 1, 1, 1))
    book.charge("day:2026-11-03", {Dimension.CALLS: 1}, reason="t")
    book.define("day:2026-11-04", Limits(1, 1, 1, 1))
    book.charge("day:2026-11-04", {Dimension.CALLS: 1}, reason="t")  # a new day is a new scope
    assert book.exhausted("day:2026-11-03") == [Dimension.CALLS]
    state.close()


def test_scope_lookup_does_not_leak_into_similar_names(tmp_path: Path) -> None:
    state, _, book = open_all(tmp_path)
    book.define("agent:a", LIMITS)
    book.define("agent:ab", Limits(0, 0, 0, 0))
    assert book.exhausted("agent:a") == []
    state.close()


def test_dollar_conversion_never_grants_or_undercharges() -> None:
    assert limit_micros(0.0000019) == 1  # rounds down
    assert cost_micros(0.0000011) == 2  # rounds up
    assert limit_micros(1.5) == 1_500_000
    assert Limits.from_spec(5, 100, 30, 2.5).as_dict()[Dimension.WALL_MS] == 30_000


def test_alert_adapter_for_the_provider_hub(tmp_path: Path) -> None:
    state, ledger, _ = open_all(tmp_path)
    AlertQueue(ledger, state).as_fn()("provider_failed", "claude: provider_failed")
    alert = read_alerts(ledger)[0]
    assert (alert.code, alert.level) == ("provider_failed", "critical")
    state.close()
