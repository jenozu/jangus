"""Tier policy: decide what the kernel does with a proposed tool call.

Pure and side-effect free. The caller (executor) records the decision in the
ledger. Anything unknown, unlisted, malformed or above the agent's max tier is
rejected, never queued.
"""

import hashlib
import json
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from jangus.config import StrictModel
from jangus.kernel.registry import Registry, Tier, ToolSpec, max_tier


class Outcome(StrEnum):
    RUN = "run"
    RUN_LOG = "run_log"
    QUEUE = "queue"
    REJECT = "reject"


class RejectReason(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    SCHEMA_VIOLATION = "schema_violation"
    ABOVE_MAX_TIER = "above_max_tier"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    tool: str
    tier: Tier | None = None
    reason: RejectReason | None = None
    args: StrictModel | None = None
    payload_hash: str | None = None


def payload_hash(tool: ToolSpec, args: StrictModel) -> str:
    """SHA-256 over tool name, version and the normalized (validated) arguments."""
    normalized: dict[str, Any] = {
        "tool": tool.name,
        "version": tool.version,
        "args": args.model_dump(mode="json"),
    }
    text = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reject(tool: str, reason: RejectReason, tier: Tier | None = None) -> Decision:
    return Decision(Outcome.REJECT, tool, tier=tier, reason=reason)


def decide(
    registry: Registry,
    tool_name: str,
    raw_args: object,
    *,
    allowed_tools: Collection[str],
    agent_max_tier: Tier,
) -> Decision:
    tool = registry.get(tool_name)
    if tool is None:
        return _reject(tool_name, RejectReason.UNKNOWN_TOOL)
    if tool_name not in allowed_tools:
        return _reject(tool_name, RejectReason.TOOL_NOT_ALLOWED)
    if not isinstance(raw_args, dict):
        return _reject(tool_name, RejectReason.SCHEMA_VIOLATION)
    try:
        args = tool.args_model.model_validate(raw_args)
    except ValidationError:
        return _reject(tool_name, RejectReason.SCHEMA_VIOLATION)

    effective = tool.tier
    if tool.elevation is not None:
        elevated = tool.elevation(args)
        if elevated is not None:
            effective = max_tier(effective, elevated)
    if effective.rank > agent_max_tier.rank:
        return _reject(tool_name, RejectReason.ABOVE_MAX_TIER, tier=effective)

    outcome = {Tier.T1: Outcome.RUN, Tier.T2: Outcome.RUN_LOG, Tier.T3: Outcome.QUEUE}[effective]
    return Decision(
        outcome, tool_name, tier=effective, args=args, payload_hash=payload_hash(tool, args)
    )
