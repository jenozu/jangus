"""Cost report and monthly reconciliation (B47, V2-FR40).

`jangus cost reconcile <export>` compares what the ledger says was spent on a month with the
provider's own usage export, which the owner downloads and passes in. A gap above 5% raises an
alert: either the ledger missed a call or the provider billed something Jangus did not make.

Export format (CSV with a header row, or JSON): one row per usage line with a cost column named
`cost_usd`, `cost`, `amount_usd` or `amount`, and optionally a `date` (or `day`, `timestamp`)
column starting with an ISO date. Anything else in the file is ignored. Money is read as exact
decimals.
"""

import csv
import io
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jangus.agents.report import build_usage_report
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import AlertLevel, AlertQueue
from jangus.kernel.ledger import Ledger

GAP_ALERT_FRACTION = Decimal("0.05")
_COST_COLUMNS = ("cost_usd", "cost", "amount_usd", "amount")
_DATE_COLUMNS = ("date", "day", "timestamp", "time")
MAX_EXPORT_BYTES = 20_000_000


def _bad(reason: str) -> RuntimeFault:
    return RuntimeFault(FaultCode.COST_EXPORT_INVALID, reason)


def _rows(raw: bytes, suffix: str) -> list[dict[str, Any]]:
    if len(raw) > MAX_EXPORT_BYTES:
        raise _bad("export file is too large")
    text = raw.decode("utf-8-sig", "replace")
    if suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            raise _bad("export is not valid JSON") from None
        if isinstance(data, dict):
            data = data.get("data", data.get("rows", data.get("usage")))
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise _bad("JSON export must be a list of rows")
        return data
    return list(csv.DictReader(io.StringIO(text)))


def _pick(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        if name in lowered:
            return lowered[name]
    return None


@dataclass(frozen=True)
class ExportTotal:
    micros: int
    rows: int
    skipped_other_months: int
    had_dates: bool


def read_export(path: Path, month: str) -> ExportTotal:
    """Sum the export's cost for `month` (YYYY-MM) in micro-dollars."""
    try:
        raw = path.read_bytes()
    except OSError:
        raise _bad(f"cannot read {path.name}") from None
    total = Decimal(0)
    rows = skipped = 0
    had_dates = False
    for row in _rows(raw, path.suffix):
        cost = _pick(row, _COST_COLUMNS)
        if cost is None or str(cost).strip() == "":
            continue
        try:
            amount = Decimal(str(cost).strip().lstrip("$"))
        except InvalidOperation:
            raise _bad("a cost value is not a number") from None
        if not amount.is_finite() or amount < 0:
            raise _bad("a cost value is negative or not finite")
        stamp = _pick(row, _DATE_COLUMNS)
        if stamp is not None and str(stamp).strip():
            had_dates = True
            if not str(stamp).strip().startswith(month):
                skipped += 1
                continue
        total += amount
        rows += 1
    if rows == 0 and skipped == 0:
        raise _bad("no cost column found (expected cost_usd, cost, amount_usd or amount)")
    return ExportTotal(int(total * 1_000_000), rows, skipped, had_dates)


def ledger_month_micros(ledger: Ledger, month: str) -> int:
    return build_usage_report(ledger, month).totals["cost_usd_micros"]


@dataclass(frozen=True)
class Reconciliation:
    month: str
    ledger_micros: int
    export_micros: int
    gap_fraction: Decimal
    alerted: bool

    def render(self) -> str:
        def usd(m: int) -> str:
            return f"${Decimal(m) / 1_000_000:.4f}"

        verdict = "ALERT: gap above 5%" if self.alerted else "within 5%"
        return (
            f"Reconciliation for {self.month}\n"
            f"  ledger total:   {usd(self.ledger_micros)}\n"
            f"  provider export:{usd(self.export_micros)}\n"
            f"  gap:            {self.gap_fraction * 100:.2f}% ({verdict})"
        )


def reconcile(ledger: Ledger, alerts: AlertQueue, export_path: Path, month: str) -> Reconciliation:
    export = read_export(export_path, month)
    ours = ledger_month_micros(ledger, month)
    theirs = export.micros
    if theirs == 0:
        gap = Decimal(0) if ours == 0 else Decimal(1)
    else:
        gap = abs(Decimal(ours - theirs)) / Decimal(theirs)
    alerted = gap > GAP_ALERT_FRACTION
    if alerted:
        alerts.raise_alert(
            "reconcile_gap",
            f"ledger and provider export differ by more than 5% for {month}",
            level=AlertLevel.CRITICAL,
            detail={"month": month, "ledger_micros": ours, "export_micros": theirs},
            once=f"reconcile:{month}:{ours}:{theirs}",
        )
    return Reconciliation(month, ours, theirs, gap, alerted)
