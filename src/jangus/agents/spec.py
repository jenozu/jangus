"""Agent spec schema and loader (docs/ARCHITECTURE.md, "Agent spec").

A spec is inert data. It becomes active only when its SHA-256 is in the owner's
approval registry (see spec_registry.py). The hash is taken over the exact bytes
that are parsed, so what was approved is what runs.

Untrusted input rules: YAML anchors and aliases are rejected (no expansion
tricks), duplicate keys are rejected (no ambiguity about which value wins),
and only a single plain document is accepted. Error messages carry locations
and reasons, never the spec's text.
"""

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from jangus.config import StrictModel
from jangus.directives.models import Budget, SpecConstraints
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.registry import Tier

MAX_SPEC_BYTES = 64 * 1024
Provider = Literal["ollama", "claude", "none"]  # "none": kernel-only agent, never calls a model
Host = Literal["pc", "vps", "actions"]
Effort = Literal["low", "medium", "high"]

_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_CRON_FIELD = re.compile(r"^[0-9*/,\-]+$")


class ModelRef(StrictModel):
    provider: Provider
    name: str = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    effort: Effort | None = None


class EscalationModel(StrictModel):
    provider: Provider
    name: str = Field(min_length=1)
    effort: Effort | None = None


class SpecBudget(StrictModel):
    calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    wall_seconds: int = Field(ge=0)
    spend_usd: float = Field(ge=0)
    daily_escalations: int | None = Field(default=None, ge=0)


class Attempts(StrictModel):
    default_model: int = Field(default=2, ge=1)
    step_up: int = Field(default=1, ge=0)
    max_model_calls: int = Field(default=6, ge=1)
    max_proposals: int = Field(default=12, ge=1)


class Trigger(StrictModel):
    """Exactly one of cron, manual, or escalation."""

    cron: str | None = None
    manual: bool | None = None
    escalation: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "Trigger":
        set_fields = [k for k in ("cron", "manual", "escalation") if getattr(self, k) is not None]
        if len(set_fields) != 1:
            raise ValueError("trigger must set exactly one of cron, manual, escalation")
        if self.cron is not None:
            parts = self.cron.split()
            if len(parts) != 5 or not all(_CRON_FIELD.match(p) for p in parts):
                raise ValueError("cron must be five fields of digits and * / , -")
        if self.manual is False:
            raise ValueError("manual trigger must be true or omitted")
        if self.escalation is not None and not self.escalation:
            raise ValueError("escalation trigger needs at least one reason")
        return self


def _is_absolute(value: str) -> bool:
    return PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()


class AgentSpec(StrictModel):
    schema_version: Literal[1]
    name: str
    role: str = Field(min_length=1)
    model: ModelRef
    tools: tuple[str, ...]
    max_tier: Tier
    secrets: tuple[str, ...] = ()
    memory_scope: tuple[str, ...] = ()
    triggers: tuple[Trigger, ...] = ()
    budget: SpecBudget
    escalation_model: EscalationModel | None = None
    attempts: Attempts = Attempts()
    max_input_tokens: int | None = Field(default=None, gt=0)
    recipes: tuple[str, ...] = ()
    watchers: tuple[str, ...] = ()
    host: Host = "pc"
    required_paths: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _name_shape(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError(
                "name must be lowercase letters, digits and hyphens, starting with a letter"
            )
        return value

    @field_validator("tools", "secrets", "memory_scope", "recipes", "watchers")
    @classmethod
    def _unique_nonblank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("entries must not be blank")
        if len(set(value)) != len(value):
            raise ValueError("entries must be unique")
        return value

    @field_validator("required_paths")
    @classmethod
    def _paths_absolute(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _is_absolute(item) for item in value):
            raise ValueError("required_paths must be absolute")
        return value


@dataclass(frozen=True)
class LoadedSpec:
    spec: AgentSpec
    sha256: str


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _invalid(reason: str) -> RuntimeFault:
    return RuntimeFault(FaultCode.SPEC_INVALID, reason)


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            try:
                if key in seen:
                    raise _invalid("duplicate key in spec")
                seen.add(key)
            except TypeError:
                raise _invalid("unhashable key in spec") from None
        return super().construct_mapping(node, deep)


def _reject_aliases(text: str) -> None:
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise _invalid("YAML anchors and aliases are not allowed in specs")
    except yaml.YAMLError:
        raise _invalid("spec is not valid YAML") from None


def parse_spec(raw: bytes) -> LoadedSpec:
    """Parse untrusted spec bytes. The returned hash covers exactly these bytes."""
    if len(raw) > MAX_SPEC_BYTES:
        raise _invalid(f"spec is larger than {MAX_SPEC_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _invalid("spec is not valid UTF-8") from None
    _reject_aliases(text)
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except RuntimeFault:
        raise
    except yaml.YAMLError:
        raise _invalid("spec is not valid YAML") from None
    if not isinstance(data, dict):
        raise _invalid("spec must be a mapping")
    try:
        # JSON mode keeps strict typing (no "5" -> 5) while accepting arrays and enum strings.
        spec = AgentSpec.model_validate_json(json.dumps(data, allow_nan=False))
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()
        )
        raise _invalid(problems) from None
    except (TypeError, ValueError):
        raise _invalid("spec contains values that are not plain data") from None
    return LoadedSpec(spec, sha256_hex(raw))


def load_spec_file(path: Path) -> LoadedSpec:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_SPEC_BYTES + 1)
    except OSError:
        raise _invalid(f"cannot read {path.name}") from None
    return parse_spec(raw)


def check_tools_known(spec: AgentSpec, known: set[str] | frozenset[str]) -> None:
    unknown = sorted(set(spec.tools) - known)
    if unknown:
        raise _invalid(f"spec names tools that are not registered: {unknown}")


def check_host(
    spec: AgentSpec,
    *,
    running_host: Host,
    secret_available: Callable[[str], bool],
    path_exists: Callable[[str], bool] = os.path.exists,
) -> None:
    """B35: refuse to run where the spec's host, secrets or paths are not available."""
    problems: list[str] = []
    if spec.host != running_host:
        problems.append(f"spec is for host {spec.host!r} but this is {running_host!r}")
    problems += [f"secret {n!r} not available" for n in spec.secrets if not secret_available(n)]
    problems += [f"path {p!r} not found" for p in spec.required_paths if not path_exists(p)]
    if problems:
        raise RuntimeFault(FaultCode.HOST_REQUIREMENTS_UNMET, "; ".join(problems))


def to_constraints(loaded: LoadedSpec) -> SpecConstraints:
    """The bounds a directive may narrow but never exceed."""
    spec = loaded.spec
    return SpecConstraints(
        name=spec.name,
        sha256=loaded.sha256,
        tools=frozenset(spec.tools),
        max_tier=spec.max_tier,
        budget=Budget(
            calls=spec.budget.calls,
            tokens=spec.budget.tokens,
            wall_seconds=spec.budget.wall_seconds,
            spend_usd=spec.budget.spend_usd,
        ),
        max_model_calls=spec.attempts.max_model_calls,
        max_proposals=spec.attempts.max_proposals,
        max_wall_seconds=spec.budget.wall_seconds,
        max_input_tokens=spec.max_input_tokens,
    )
