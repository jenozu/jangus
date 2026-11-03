from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from jangus.agents.metering import usage_by_role
from jangus.agents.pricing import ModelPrice, PriceTable, cost_micros, load_prices
from jangus.agents.providers.base import ModelRequest
from jangus.agents.report import build_usage_report
from jangus.agents.runner import Job, RunOutcome
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import read_alerts
from jangus.kernel.budgets import Dimension
from jangus.kernel.escalation import EscalationCap
from tests.agent_harness import Harness, ScriptedProvider, default_prices, make_spec, reply

TODAY = date(2026, 11, 3)


def table(models: dict[str, Any], as_of: date = TODAY) -> PriceTable:
    return PriceTable.parse({"as_of": as_of, "models": models})


# ---- price table ----------------------------------------------------------


def test_parse_valid_table_and_free_entries() -> None:
    t = table(
        {"claude/haiku-tag": {"input_per_mtok": 1, "output_per_mtok": 5}, "ollama/x:1": "free"}
    )
    assert t.price_for("claude", "haiku-tag") == ModelPrice(Decimal(1), Decimal(5))
    assert t.price_for("ollama", "anything").input_per_mtok == 0  # local models are free
    assert t.price_for("claude", "unlisted") is None  # a paid model with no entry
    with pytest.raises(RuntimeFault) as info:
        t.require("claude", "unlisted")
    assert info.value.code is FaultCode.PRICE_MISSING


@pytest.mark.parametrize(
    "bad",
    [
        {"as_of": "2026-11-01", "models": {}},  # as_of must be a date
        {"as_of": TODAY},  # models missing
        {"as_of": TODAY, "models": {"claude": "free"}},  # key is not provider/model
        {"as_of": TODAY, "models": {"claude/x": {"input_per_mtok": 1}}},  # no output price
        {"as_of": TODAY, "models": {"claude/x": {"input_per_mtok": -1, "output_per_mtok": 1}}},
        {
            "as_of": TODAY,
            "models": {"claude/x": {"input_per_mtok": 1, "output_per_mtok": 1, "x": 1}},
        },
        {"as_of": TODAY, "models": {"claude/x": {"input_per_mtok": "1", "output_per_mtok": 1}}},
        {"as_of": TODAY, "models": {}, "extra": 1},
        [],
    ],
)
def test_malformed_tables_are_refused(bad: object) -> None:
    with pytest.raises(RuntimeFault) as info:
        PriceTable.parse(bad)
    assert info.value.code is FaultCode.PRICES_INVALID


def test_load_prices_from_a_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "prices.yaml"
    path.write_text(
        "as_of: 2026-11-01\nmodels:\n"
        "  claude/haiku-tag: {input_per_mtok: 1.0, output_per_mtok: 5.0}\n"
    )
    assert load_prices(path).price_for("claude", "haiku-tag") is not None
    with pytest.raises(RuntimeFault):
        load_prices(tmp_path / "missing.yaml")
    path.write_text("as_of: [")
    with pytest.raises(RuntimeFault):
        load_prices(path)


def test_cost_is_in_micro_dollars_rounded_up() -> None:
    price = ModelPrice(Decimal(3), Decimal(15), Decimal("0.3"))
    assert cost_micros(price, input_tokens=1000, output_tokens=0) == 3000
    assert cost_micros(price, input_tokens=0, output_tokens=100) == 1500
    assert cost_micros(price, input_tokens=0, output_tokens=0, cached_tokens=1) == 1  # 0.3 -> 1
    assert cost_micros(price, input_tokens=0, output_tokens=0) == 0


def test_staleness_after_sixty_days() -> None:
    t = table({}, as_of=TODAY)
    assert not t.is_stale(TODAY + timedelta(days=60)) and t.is_stale(TODAY + timedelta(days=61))


# ---- B38 in the run loop --------------------------------------------------


def paid_spec() -> Any:
    return make_spec(provider="claude", model="opus-tag", escalation=False)


def test_paid_model_without_a_price_is_refused_before_sending(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=paid_spec(), prices=table({}))
    h.escalation.script.append(reply(done=True))
    report = h.runner.run(Job("j1", "x"))
    assert report.outcome is RunOutcome.REFUSED
    assert h.escalation.requests == [] and h.provider.requests == []  # nothing was sent anywhere
    assert "price_missing:claude/opus-tag" in report.failures
    assert any(a.code == "price_missing" and a.level == "critical" for a in read_alerts(h.ledger))
    assert h.events("model_call") == []


def test_no_price_table_means_no_paid_model_at_all(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=paid_spec())
    h.runner._prices = PriceTable.local_only()  # noqa: SLF001 - what a runner without prices has
    h.escalation.script.append(reply(done=True))
    assert h.runner.run(Job("j1", "x")).outcome is RunOutcome.REFUSED


def test_priced_paid_model_runs_and_cost_is_recorded_and_charged(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=paid_spec(), prices=default_prices())
    h.escalation.script.append(reply(done=True))
    h.escalation.usage = ScriptedProvider().usage  # 100 in, 20 out
    h.runner.run(Job("j1", "x"))
    call = h.events("model_call")[0]
    assert call["cost_usd_micros"] == 100 * 15 + 20 * 75  # opus-tag: $15 in, $75 out per Mtok
    assert h.budgets.status("agent:file-agent")[Dimension.USD_MICROS].spent == 3000


def test_local_model_costs_zero_without_an_entry(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.provider.script.append(reply(done=True))
    h.runner.run(Job("j1", "x"))
    assert h.events("model_call")[0]["cost_usd_micros"] == 0


def test_spend_cap_stops_a_paid_run(tmp_path: Path) -> None:
    h = Harness(tmp_path, spec=paid_spec(), prices=default_prices())
    h.budgets.define_one("agent:file-agent", Dimension.USD_MICROS, 2000)  # below one call's cost
    h.escalation.script += [reply(("file.read", {"path": "/work/x"})), reply(done=True)]
    report = h.runner.run(Job("j1", "x"))
    assert report.outcome is RunOutcome.BUDGET and len(h.escalation.requests) == 1


def test_stale_price_table_alerts_once(tmp_path: Path) -> None:
    old = table({}, as_of=date.today() - timedelta(days=90))
    h = Harness(tmp_path, prices=old)
    h.provider.script += [reply(done=True), reply(done=True)]
    h.runner.run(Job("j1", "x"))
    h.runner.run(Job("j2", "x"))
    assert [a.code for a in read_alerts(h.ledger)].count("price_table_stale") == 1


# ---- B37 / B30 ledger and report -------------------------------------------


def test_every_call_record_has_the_required_fields_and_kernel_run_records_zero(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.provider.script.append(reply(done=True))
    h.runner.run(Job("j1", "x", task_label="t9"))
    call = h.events("model_call")[0]
    for key in (
        "role", "reason", "model", "input_tokens", "output_tokens", "cached_tokens",
        "cost_usd_micros", "est_input_tokens", "latency_ms", "outcome", "run", "task", "attempt",
    ):  # fmt: skip
        assert key in call, key
    assert usage_by_role(h.ledger)["kernel-run"]["calls"] == 0  # kernel-run is always zero


def test_report_totals_match_the_ledger_and_the_budgets(tmp_path: Path) -> None:
    h = Harness(tmp_path, prices=default_prices())
    h.provider.script += [
        reply(("file.read", {"path": "/work/a"})),
        reply(done=True),
        reply(done=True),
    ]
    h.runner.run(Job("j1", "first"))
    h.runner.run(Job("j2", "second"))
    report = build_usage_report(h.ledger)
    ledger_calls = h.events("model_call")
    assert report.totals["calls"] == len(ledger_calls) == 3
    assert report.totals["tokens"] == sum(
        c["input_tokens"] + c["output_tokens"] for c in ledger_calls
    )
    assert sum(r["calls"] for r in report.by_role.values()) == report.totals["calls"]
    assert sum(r["tokens"] for r in report.by_run.values()) == report.totals["tokens"]
    spent = h.budgets.status("agent:file-agent")
    assert spent[Dimension.TOKENS].spent == report.totals["tokens"]
    assert spent[Dimension.CALLS].spent == report.totals["calls"]
    assert report.by_agent["file-agent"]["calls"] == 3
    assert "Total: 3 calls" in report.render()


def test_report_can_be_limited_to_one_day(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.provider.script.append(reply(done=True))
    h.runner.run(Job("j1", "x"))
    assert build_usage_report(h.ledger, "1999-01-01").totals["calls"] == 0
    today = h.ledger.entries()[0].ts[:10]
    assert build_usage_report(h.ledger, today).totals["calls"] == 1


# ---- escalation cap ----------------------------------------------------------


def make_cap(h: Harness, limit: int, day: list[date]) -> EscalationCap:
    return EscalationCap(
        state=h.state,
        ledger=h.ledger,
        budgets=h.budgets,
        alerts=h.alerts,
        daily_limit=limit,
        today=lambda: day[0],
    )


def test_daily_cap_allows_then_queues_for_the_owner(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    day = [TODAY]
    cap = make_cap(h, 2, day)
    assert cap.allow("d1", "halted") and cap.allow("d2", "halted")
    assert cap.allow("d3", "halted") is False  # never raised silently
    assert [e["directive"] for e in h.events("escalation")] == ["d1", "d2"]
    assert h.events("escalation_queued")[0]["directive"] == "d3"
    assert any(a.code == "escalation_cap_reached" for a in read_alerts(h.ledger))


def test_cap_survives_restart_and_resets_by_day(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    day = [TODAY]
    make_cap(h, 1, day).allow("d1", "halted")
    h.state.close()
    h2 = Harness(tmp_path)
    cap2 = make_cap(h2, 1, day)
    assert cap2.allow("d2", "halted") is False  # the same day's count was kept
    day[0] = TODAY + timedelta(days=1)
    assert cap2.allow("d3", "halted") is True  # a new day is a new allowance


def test_cap_gates_the_directive_runner_escalation(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    cap = make_cap(h, 0, [TODAY])  # a cap of zero: nobody may be called
    h.provider.script.append(reply(("file.read", {"path": "/work/a"}), done=True))
    task = {
        "id": 1,
        "title": "x",
        "testStrategy": "y",
        "tools": ["file.read"],
    }
    d = h.issue_directive([task], escalate=True)
    called: list[object] = []
    report = h.directives.run(d, gate=cap, on_escalate=called.append)
    assert report.halted is not None and not report.escalated and called == []


def test_request_dataclass_is_unchanged_by_pricing() -> None:
    assert ModelRequest(model="m", system="s", user="u", max_tokens=1).cache_prefix is False
