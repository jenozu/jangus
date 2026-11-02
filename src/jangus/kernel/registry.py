"""Tool registry.

Every tool declares a strict argument schema, a tier, reversibility, and how
the kernel verifies and reconciles it. A tool without both verify and
reconcile may not be T2 or T3.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from jangus.config import StrictModel
from jangus.errors import FaultCode, RuntimeFault


class Tier(StrEnum):
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"

    @property
    def rank(self) -> int:
        return int(self.value[1:])


def max_tier(*tiers: Tier) -> Tier:
    return max(tiers, key=lambda t: t.rank)


@dataclass(frozen=True)
class Reconciliation:
    """What a tool's reconcile step found after a crash."""

    state: Literal["applied", "absent", "unknown"]
    result: dict[str, Any] | None = None


Execute = Callable[[Any, Mapping[str, str]], dict[str, Any]]
Verify = Callable[[Any, dict[str, Any]], bool]
Reconcile = Callable[[Any], Reconciliation]
Elevation = Callable[[Any], Tier | None]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: int
    args_model: type[StrictModel]
    tier: Tier
    reversible: bool
    execute: Execute
    verify: Verify | None = None
    reconcile: Reconcile | None = None
    secrets: tuple[str, ...] = ()
    # Optional rule that raises the effective tier for specific arguments,
    # for example a target outside the workspace.
    elevation: Elevation | None = None


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        def refuse(reason: str) -> RuntimeFault:
            return RuntimeFault(
                FaultCode.TOOL_REGISTRATION_INVALID, f"tool {tool.name!r}: {reason}"
            )

        if not tool.name or tool.version < 1:
            raise refuse("needs a name and a version of at least 1")
        if tool.name in self._tools:
            raise refuse("already registered")
        if not (isinstance(tool.args_model, type) and issubclass(tool.args_model, StrictModel)):
            raise refuse("args_model must be a StrictModel subclass")
        if tool.tier is not Tier.T1 and (tool.verify is None or tool.reconcile is None):
            raise refuse("T2 and T3 tools need both verify and reconcile")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)
