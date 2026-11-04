"""Strict configuration models.

Config is owner-written TOML kept outside the checkout. It holds no secrets
(secrets come from Windows Credential Manager or the environment, injected by
the kernel). Unknown fields, wrong types and unsafe values fail closed with a
RuntimeFault.
"""

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from jangus.errors import FaultCode, RuntimeFault

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


class StrictModel(BaseModel):
    """Base for all config models: no extras, no coercion, immutable."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def checkout_roots() -> tuple[Path, ...]:
    """Directories that must not hold config, data or workspaces."""
    return (Path(__file__).resolve().parents[2],)


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def check_loopback_url(value: str) -> str:
    """Return value if it is a loopback http url with a port, else raise ValueError."""
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"{FaultCode.ENDPOINT_NOT_LOOPBACK.value}: bad port") from exc
    if (
        parts.scheme != "http"
        or parts.hostname not in LOOPBACK_HOSTS
        or port is None
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            f"{FaultCode.ENDPOINT_NOT_LOOPBACK.value}: endpoint must be "
            "http://127.0.0.1:<port> or http://[::1]:<port> (localhost is not accepted)"
        )
    return value


class ModelEndpoint(StrictModel):
    url: str
    name: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        return check_loopback_url(value)


class Caps(StrictModel):
    """Spend caps as percentages of what the owner's plan allots (fail closed without them).

    `plan_limit_usd` is the monthly amount the plan or the provider-side spend limit allows.
    The monthly cap is `monthly_percent` of it and the daily cap is `daily_percent` of it, so
    Jangus stops short of the limit instead of at it. Percentages cannot exceed 100, and the
    daily cap cannot exceed the monthly one. The provider-side limit stays the real backstop.
    """

    plan_limit_usd: float = Field(gt=0)
    monthly_percent: float = Field(default=80, gt=0, le=100)
    daily_percent: float = Field(default=5, gt=0, le=100)
    big_call_tokens: int = Field(default=20_000, gt=0)
    big_call_percent_of_daily: float = Field(default=25, gt=0, le=100)

    @model_validator(mode="after")
    def _daily_within_monthly(self) -> "Caps":
        if self.daily_percent > self.monthly_percent:
            raise ValueError("daily_percent cannot be more than monthly_percent")
        return self

    def _share(self, percent: float, of: float) -> float:
        return float(Decimal(str(of)) * Decimal(str(percent)) / Decimal(100))

    @property
    def monthly_usd(self) -> float:
        return self._share(self.monthly_percent, self.plan_limit_usd)

    @property
    def daily_usd(self) -> float:
        return self._share(self.daily_percent, self.plan_limit_usd)

    @property
    def big_call_usd(self) -> float:
        return self._share(self.big_call_percent_of_daily, self.daily_usd)


class JangusConfig(StrictModel):
    data_dir: Path = Field(strict=False)
    workspace_dir: Path = Field(strict=False)
    # Optional local (Ollama) endpoint. The default cheap model is Claude Haiku over the direct
    # API (ADR-002), so a local model is an opt-in extra: both this table and `ollama_enabled`
    # must be present for the local provider to exist at all.
    model: ModelEndpoint | None = None
    # Each provider is off unless the owner turns it on here. Nothing is enabled by default and
    # nothing switches between them on its own (no silent provider fallback).
    claude_enabled: bool = False
    ollama_enabled: bool = False
    caps: Caps | None = None
    # Where approved agent specs and their standing directives live (default: <data_dir>/specs).
    specs_dir: Path | None = Field(default=None, strict=False)

    @field_validator("specs_dir")
    @classmethod
    def _specs_absolute(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError(f"{FaultCode.PATH_INVALID.value}: path must be absolute")
        return None if value is None else value.resolve()

    @field_validator("data_dir", "workspace_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError(f"{FaultCode.PATH_INVALID.value}: path must be absolute")
        return value.resolve()

    @model_validator(mode="after")
    def _local_provider_needs_an_endpoint(self) -> "JangusConfig":
        if self.ollama_enabled and self.model is None:
            raise ValueError(
                f"{FaultCode.CONFIG_INVALID.value}: ollama_enabled needs a [model] endpoint"
            )
        return self

    @model_validator(mode="after")
    def _separate_and_outside_checkout(self) -> "JangusConfig":
        for label, path in (("data_dir", self.data_dir), ("workspace_dir", self.workspace_dir)):
            for root in checkout_roots():
                if _is_within(path, root.resolve()):
                    raise ValueError(
                        f"{FaultCode.PATH_INVALID.value}: {label} is inside the checkout"
                    )
        if _is_within(self.data_dir, self.workspace_dir) or _is_within(
            self.workspace_dir, self.data_dir
        ):
            raise ValueError(
                f"{FaultCode.PATH_INVALID.value}: data_dir and workspace_dir must not overlap"
            )
        return self


def parse_config(data: dict[str, Any]) -> JangusConfig:
    try:
        return JangusConfig.model_validate(data)
    except ValidationError as exc:
        text = str(exc)
        code = (
            FaultCode.ENDPOINT_NOT_LOOPBACK
            if FaultCode.ENDPOINT_NOT_LOOPBACK.value in text
            else FaultCode.PATH_INVALID
            if FaultCode.PATH_INVALID.value in text
            else FaultCode.CONFIG_INVALID
        )
        # Report locations and reasons only; never echo input values.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()
        )
        raise RuntimeFault(code, problems) from None


def load_config(path: Path) -> JangusConfig:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise RuntimeFault(FaultCode.CONFIG_UNREADABLE, f"cannot read {path.name}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeFault(FaultCode.CONFIG_INVALID, f"{path.name} is not valid TOML") from exc
    return parse_config(data)
