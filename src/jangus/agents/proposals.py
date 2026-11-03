"""Parse a model reply into proposed tool calls.

A reply is untrusted data. It is parsed, never executed: the kernel validates each
proposal against the registry, the approved spec and the tier policy.

Only `proposals`, `done` and `note` are read. Any other key (for example a request for a
stronger model, a higher limit or the orchestrator) is ignored and only its name is
recorded (B34): nothing a reply says can change a model, a limit or a permission.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from jangus.kernel.registry import Registry

MAX_PROPOSALS_PER_REPLY = 20
MAX_NOTE_CHARS = 500

REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tool": {"type": "string"}, "args": {"type": "object"}},
                "required": ["tool", "args"],
            },
        },
        "done": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "required": ["proposals", "done"],
}


class ReplyError(Exception):
    """The reply could not be parsed into valid proposals. The reason is a short code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Proposal:
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Reply:
    proposals: tuple[Proposal, ...]
    done: bool
    note: str
    ignored_keys: tuple[str, ...]


def _reject_constant(_: str) -> object:
    raise ReplyError("not_plain_json")


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```") and text.endswith("```") and text.count("```") == 2:
        inner = text[3:-3]
        first, _, rest = inner.partition("\n")
        return (rest if first.strip().lower() in ("", "json") else inner).strip()
    return text


def parse_reply(text: str) -> Reply:
    try:
        data = json.loads(_strip_fence(text), parse_constant=_reject_constant)
    except ReplyError:
        raise
    except (ValueError, RecursionError):
        raise ReplyError("not_json") from None
    if not isinstance(data, dict):
        raise ReplyError("not_an_object")
    raw_items = data.get("proposals", [])
    if not isinstance(raw_items, list) or len(raw_items) > MAX_PROPOSALS_PER_REPLY:
        raise ReplyError("bad_proposals")
    proposals: list[Proposal] = []
    ignored: set[str] = {k for k in data if k not in ("proposals", "done", "note")}
    for item in raw_items:
        if not isinstance(item, dict):
            raise ReplyError("bad_proposal")
        tool, args = item.get("tool"), item.get("args")
        if not isinstance(tool, str) or not tool or not isinstance(args, dict):
            raise ReplyError("bad_proposal")
        ignored |= {f"proposal.{k}" for k in item if k not in ("tool", "args")}
        proposals.append(Proposal(tool, args))
    done = data.get("done", False)
    if not isinstance(done, bool):
        raise ReplyError("bad_done")
    note = data.get("note", "")
    if not isinstance(note, str):
        raise ReplyError("bad_note")
    return Reply(tuple(proposals), done, note[:MAX_NOTE_CHARS], tuple(sorted(ignored)))


def proposal_hash(registry: Registry, proposal: Proposal) -> str:
    """Hash of (tool, normalized arguments). Normalized means validated by the tool's schema
    when it validates, so equivalent spellings of the same call hash the same."""
    args: Any = proposal.args
    tool = registry.get(proposal.tool)
    if tool is not None:
        try:
            args = tool.args_model.model_validate(proposal.args).model_dump(mode="json")
        except ValueError:
            args = proposal.args
    try:
        text = json.dumps(
            {"tool": proposal.tool, "args": args},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        text = repr((proposal.tool, sorted(map(str, proposal.args))))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
