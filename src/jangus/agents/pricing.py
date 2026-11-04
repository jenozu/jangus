"""Price table and cost computation (B38, V2-FR33).

`prices.yaml` is owner-maintained and kept in the private config directory (never in the
checkout). Example:

    as_of: 2026-11-01
    models:
      claude/haiku-tag: {input_per_mtok: 1.00, output_per_mtok: 5.00, cached_per_mtok: 0.10}
      ollama/qwen3:8b: free

Keys are `provider/model`. The default cheap model is Claude Haiku over the direct API
(ADR-002), so the common case is a paid entry. Local models (provider `ollama`, optional and
off by default) cost nothing and need no entry; every other provider is paid, and a paid model
with no entry is refused (fail closed).
Costs are integer micro-dollars, rounded up so the ledger never under-reports.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from jangus.errors import FaultCode, RuntimeFault

STALE_AFTER_DAYS = 60
FREE_PROVIDERS = frozenset({"ollama"})
_PRICE_KEYS = ("input_per_mtok", "output_per_mtok", "cached_per_mtok", "cache_write_per_mtok")


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_per_mtok: Decimal = Decimal(0)
    cache_write_per_mtok: Decimal = Decimal(0)


FREE = ModelPrice(Decimal(0), Decimal(0))


def _invalid(reason: str) -> RuntimeFault:
    return RuntimeFault(FaultCode.PRICES_INVALID, reason)


def _money(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _invalid(f"{where}: price must be a number")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise _invalid(f"{where}: price is not a number") from None
    if not amount.is_finite() or amount < 0:
        raise _invalid(f"{where}: price must be zero or more")
    return amount


class PriceTable:
    def __init__(self, as_of: date, prices: dict[str, ModelPrice]) -> None:
        self.as_of = as_of
        self._prices = dict(prices)

    @classmethod
    def parse(cls, data: object) -> "PriceTable":
        if not isinstance(data, dict) or set(data) != {"as_of", "models"}:
            raise _invalid("prices must have exactly as_of and models")
        as_of = data["as_of"]
        if not isinstance(as_of, date):
            raise _invalid("as_of must be a date such as 2026-11-01")
        models = data["models"]
        if not isinstance(models, dict):
            raise _invalid("models must be a mapping")
        out: dict[str, ModelPrice] = {}
        for key, entry in models.items():
            if not isinstance(key, str) or key.count("/") < 1 or key.startswith("/"):
                raise _invalid("model keys look like provider/model")
            if entry == "free":
                out[key] = FREE
                continue
            if not isinstance(entry, dict) or not {"input_per_mtok", "output_per_mtok"} <= set(
                entry
            ):
                raise _invalid(f"{key}: needs input_per_mtok and output_per_mtok, or 'free'")
            extra = set(entry) - set(_PRICE_KEYS)
            if extra:
                raise _invalid(f"{key}: unknown price fields {sorted(extra)}")
            out[key] = ModelPrice(**{k: _money(v, key) for k, v in entry.items()})
        return cls(as_of, out)

    @classmethod
    def local_only(cls) -> "PriceTable":
        """No paid model has a price, so none may be called."""
        return cls(date.today(), {})

    def price_for(self, provider: str, model: str) -> ModelPrice | None:
        entry = self._prices.get(f"{provider}/{model}")
        if entry is not None:
            return entry
        return FREE if provider in FREE_PROVIDERS else None

    def require(self, provider: str, model: str) -> ModelPrice:
        price = self.price_for(provider, model)
        if price is None:
            raise RuntimeFault(
                FaultCode.PRICE_MISSING, f"no price for paid model {provider}/{model}: call refused"
            )
        return price

    def is_stale(self, today: date) -> bool:
        return (today - self.as_of).days > STALE_AFTER_DAYS


def load_prices(path: Path) -> PriceTable:
    try:
        raw = path.read_bytes()
    except OSError:
        raise _invalid(f"cannot read {path.name}") from None
    try:
        return PriceTable.parse(yaml.safe_load(raw))
    except yaml.YAMLError:
        raise _invalid("prices file is not valid YAML") from None


def cost_micros(
    price: ModelPrice,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> int:
    """Micro-dollars: tokens times dollars per million tokens is exactly micro-dollars."""
    total = (
        Decimal(input_tokens) * price.input_per_mtok
        + Decimal(output_tokens) * price.output_per_mtok
        + Decimal(cached_tokens) * price.cached_per_mtok
        + Decimal(cache_write_tokens) * price.cache_write_per_mtok
    )
    return int(total.to_integral_value(rounding=ROUND_CEILING))
