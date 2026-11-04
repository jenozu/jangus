import json
from pathlib import Path

import pytest

from jangus.cli import main
from jangus.cli_cost import read_export, reconcile
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import AlertQueue, read_alerts
from jangus.kernel.ledger import Ledger
from jangus.kernel.state import StateStore

MONTH = "2026-11"


class Rig:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.state = StateStore.open(tmp / "state.db")
        self.ledger = Ledger.open(tmp / "ledger.jsonl", clock=lambda: "2026-11-15T10:00:00+00:00")
        self.alerts = AlertQueue(self.ledger, self.state)

    def call(self, micros: int, tokens: int = 100) -> None:
        self.ledger.append(
            {
                "kind": "model_call",
                "role": "specialist",
                "reason": "task",
                "run": 1,
                "agent": "a",
                "task": "t",
                "input_tokens": tokens,
                "output_tokens": 0,
                "cached_tokens": 0,
                "cost_usd_micros": micros,
            }  # fmt: skip
        )

    def csv(
        self, rows: list[str], name: str = "usage.csv", header: str = "date,model,cost_usd"
    ) -> Path:
        path = self.tmp / name
        path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
        return path


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def codes(rig: Rig) -> list[str]:
    return [a.code for a in read_alerts(rig.ledger)]


# ---- B47 --------------------------------------------------------------------------------


def test_gap_above_five_percent_raises_an_alert(rig: Rig) -> None:
    rig.call(1_000_000)  # the ledger says $1.00
    export = rig.csv(["2026-11-03,haiku,1.20"])  # the provider says $1.20: a 16.7% gap
    result = reconcile(rig.ledger, rig.alerts, export, MONTH)
    assert (
        result.alerted and result.ledger_micros == 1_000_000 and result.export_micros == 1_200_000
    )
    alerts = [a for a in read_alerts(rig.ledger) if a.code == "reconcile_gap"]
    assert len(alerts) == 1 and alerts[0].level == "critical"
    assert "ALERT" in result.render()


def test_gap_within_five_percent_is_quiet(rig: Rig) -> None:
    rig.call(1_000_000)
    result = reconcile(rig.ledger, rig.alerts, rig.csv(["2026-11-03,haiku,1.04"]), MONTH)
    assert not result.alerted and "reconcile_gap" not in codes(rig)
    assert "within 5%" in result.render()


def test_exactly_five_percent_does_not_alert(rig: Rig) -> None:
    rig.call(1_000_000)
    assert not reconcile(rig.ledger, rig.alerts, rig.csv(["2026-11-03,m,1.05"]), MONTH).alerted


def test_ledger_total_matches_the_usage_report(rig: Rig) -> None:
    for micros in (250_000, 250_000, 500_000):
        rig.call(micros)
    result = reconcile(rig.ledger, rig.alerts, rig.csv(["2026-11-01,m,1.00"]), MONTH)
    assert result.ledger_micros == 1_000_000 and not result.alerted


def test_the_provider_billing_something_the_ledger_never_saw_alerts(rig: Rig) -> None:
    result = reconcile(rig.ledger, rig.alerts, rig.csv(["2026-11-03,m,0.50"]), MONTH)
    assert result.alerted and result.ledger_micros == 0  # ledger empty, provider charged


def test_both_zero_is_fine(rig: Rig) -> None:
    assert not reconcile(rig.ledger, rig.alerts, rig.csv(["2026-11-03,m,0"]), MONTH).alerted


def test_ledger_spend_the_provider_never_billed_alerts(rig: Rig) -> None:
    rig.call(100_000)
    assert reconcile(rig.ledger, rig.alerts, rig.csv(["2026-11-03,m,0"]), MONTH).alerted


def test_repeating_the_same_reconciliation_does_not_flood_alerts(rig: Rig) -> None:
    rig.call(1_000_000)
    export = rig.csv(["2026-11-03,m,2.00"])
    reconcile(rig.ledger, rig.alerts, export, MONTH)
    reconcile(rig.ledger, rig.alerts, export, MONTH)
    assert codes(rig).count("reconcile_gap") == 1


# ---- reading exports ----------------------------------------------------------------------


def test_only_the_requested_month_counts(rig: Rig) -> None:
    path = rig.csv(["2026-10-30,m,9.99", "2026-11-02,m,1.00", "2026-11-20T08:00:00Z,m,2.00"])
    export = read_export(path, MONTH)
    assert export.micros == 3_000_000 and export.rows == 2 and export.skipped_other_months == 1


def test_json_export_and_alternative_column_names(rig: Rig) -> None:
    path = rig.tmp / "u.json"
    path.write_text(
        json.dumps(
            {
                "data": [
                    {"Date": "2026-11-01", "Amount": "$0.25"},
                    {"Date": "2026-11-02", "Amount": 0.75},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert read_export(path, MONTH).micros == 1_000_000


def test_export_without_dates_uses_every_row(rig: Rig) -> None:
    export = read_export(rig.csv(["1.50", "0.50"], header="cost"), MONTH)
    assert export.micros == 2_000_000 and not export.had_dates


def test_money_is_exact(rig: Rig) -> None:
    assert read_export(rig.csv(["2026-11-01,m,0.1", "2026-11-01,m,0.2"]), MONTH).micros == 300_000


@pytest.mark.parametrize(
    ("content", "name"),
    [
        ("date,model\n2026-11-01,m\n", "a.csv"),  # no cost column
        ("date,cost\n2026-11-01,abc\n", "b.csv"),
        ("date,cost\n2026-11-01,-3\n", "c.csv"),
        ("not json", "d.json"),
        ('{"x": 1}', "e.json"),
        ("", "f.csv"),
    ],
)
def test_bad_exports_are_refused(rig: Rig, content: str, name: str) -> None:
    path = rig.tmp / name
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RuntimeFault) as info:
        read_export(path, MONTH)
    assert info.value.code is FaultCode.COST_EXPORT_INVALID


def test_missing_file_is_refused(rig: Rig) -> None:
    with pytest.raises(RuntimeFault):
        read_export(rig.tmp / "nope.csv", MONTH)


# ---- the commands ---------------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    path = tmp_path / "jangus.toml"
    path.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'workspace_dir = "{(tmp_path / "work").as_posix()}"\n'
        '[model]\nurl = "http://127.0.0.1:11434"\nname = "m"\n',
        encoding="utf-8",
    )
    return path


def test_cost_report_and_reconcile_commands(cfg: Path, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--config", str(cfg), "cost", "report", "--month", MONTH]) == 0
    assert "Usage report for 2026-11" in capsys.readouterr().out
    export = tmp_path / "usage.csv"
    export.write_text("date,cost_usd\n2026-11-03,5.00\n", encoding="utf-8")
    code = main(["--config", str(cfg), "cost", "reconcile", str(export), "--month", MONTH])
    assert code == 1 and "ALERT" in capsys.readouterr().out  # ledger is empty, provider billed $5
    ok = tmp_path / "zero.csv"
    ok.write_text("date,cost_usd\n2026-11-03,0\n", encoding="utf-8")
    assert main(["--config", str(cfg), "cost", "reconcile", str(ok), "--month", MONTH]) == 0
    bad = tmp_path / "bad.csv"
    bad.write_text("x\n1\n", encoding="utf-8")
    assert main(["--config", str(cfg), "cost", "reconcile", str(bad), "--month", MONTH]) == 1
