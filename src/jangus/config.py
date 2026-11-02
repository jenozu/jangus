"""Strict configuration models.

Config is owner-written TOML kept outside the checkout. It holds no secrets
(secrets come from Windows Credential Manager or the environment, injected by
the kernel). Unknown fields, wrong types and unsafe values fail closed with a
RuntimeFault.
"""

import tomllib
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


class ModelEndpoint(StrictModel):
    url: str
    name: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
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


class JangusConfig(StrictModel):
    data_dir: Path = Field(strict=False)
    workspace_dir: Path = Field(strict=False)
    model: ModelEndpoint

    @field_validator("data_dir", "workspace_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError(f"{FaultCode.PATH_INVALID.value}: path must be absolute")
        return value.resolve()

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
