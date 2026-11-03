"""Keep model input small and deterministic (V2-FR39, TOKEN_ECONOMY.md section 7.6).

The kernel, not a model, shrinks large tool results (counts and the first few items) and
trims external inputs so a prompt fits the task's `max_input_tokens`. The same input always
produces the same output. If it cannot be made to fit, the call is refused (a counted failed
attempt) rather than sent over the limit.

Order of reduction: collapse older results to one line, shrink the rest, trim inputs.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jangus.agents.metering import estimate_tokens
from jangus.agents.prompt import MAX_RESULT_CHARS, result_block, user_prompt

_LEVELS = ((10, 400), (5, 200), (3, 80), (1, 40))  # (items kept, characters per string)
_MAX_KEYS = 20
_MAX_DEPTH = 4
_KEEP_FULL_RESULTS = 2
_MIN_INPUT_SHARE_CHARS = 200
CHARS_PER_TOKEN = 3


class ContextTooLarge(Exception):  # noqa: N818 - reads better as a state than an error
    """The prompt cannot be made to fit max_input_tokens."""


def shrink(value: Any, *, items: int, chars: int, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value if len(value) <= chars else f"{value[:chars]}...(+{len(value) - chars} chars)"
    if isinstance(value, list):
        kept = [shrink(v, items=items, chars=chars, depth=depth + 1) for v in value[:items]]
        if len(value) <= items:
            return kept
        return {"_list_of": len(value), "_first": kept}
    if isinstance(value, dict):
        if depth >= _MAX_DEPTH:
            return {"_keys": sorted(str(k) for k in value)[:items], "_count": len(value)}
        out = {
            str(k): shrink(v, items=items, chars=chars, depth=depth + 1)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))[:_MAX_KEYS]
        }
        if len(value) > _MAX_KEYS:
            out["_more_keys"] = len(value) - _MAX_KEYS
        return out
    return value


def summarize_result(value: Any, max_chars: int = MAX_RESULT_CHARS) -> str:
    """Deterministic summary of a tool result no longer than max_chars."""
    text = ""
    for items, chars in _LEVELS:
        text = json.dumps(shrink(value, items=items, chars=chars), sort_keys=True, default=str)
        if len(text) <= max_chars:
            return text
    return text[:max_chars] + " ...(truncated)"


_MARKER_ROOM = 48


def trim_text(text: str, max_chars: int) -> str:
    """Keep the start and the end of a long text and say how much was cut. The result is never
    longer than max_chars."""
    if len(text) <= max_chars:
        return text
    if max_chars <= _MARKER_ROOM * 2:
        return text[:max_chars]
    keep = max_chars - _MARKER_ROOM
    head = int(keep * 0.7)
    tail = keep - head
    cut = len(text) - head - tail
    return f"{text[:head]}\n...(kernel trimmed {cut} chars)...\n{text[len(text) - tail :]}"


@dataclass(frozen=True)
class ResultItem:
    index: int
    tool: str
    outcome: dict[str, Any]


def render_results(
    items: Sequence[ResultItem], *, keep_full: int | None, per_result_chars: int
) -> list[str]:
    blocks: list[str] = []
    for position, item in enumerate(items):
        older = keep_full is not None and position < len(items) - keep_full
        if older:
            status = item.outcome.get("status")
            blocks.append(f"[result {item.index} {item.tool}: {status}; details dropped by kernel]")
        else:
            body = {k: v for k, v in item.outcome.items() if k != "result"}
            body["result"] = json.loads(
                summarize_result(item.outcome.get("result"), per_result_chars)
            )
            blocks.append(result_block(item.index, item.tool, body))
    return blocks


def fit_user_prompt(
    *,
    system: str,
    instruction: str,
    inputs: Mapping[str, str],
    results: Sequence[ResultItem],
    failures: Sequence[str],
    max_input_tokens: int | None,
) -> tuple[str, bool]:
    """The user prompt, trimmed if needed. Returns (text, was_trimmed).

    Raises ContextTooLarge if even the smallest form exceeds max_input_tokens."""

    def build(keep_full: int | None, per_result: int, ins: Mapping[str, str]) -> str:
        blocks = render_results(results, keep_full=keep_full, per_result_chars=per_result)
        return user_prompt(instruction, ins, blocks, failures)

    def fits(text: str) -> bool:
        return max_input_tokens is None or estimate_tokens(system + text) <= max_input_tokens

    plain = build(None, MAX_RESULT_CHARS, inputs)
    if fits(plain):
        return plain, False
    for keep, per in ((_KEEP_FULL_RESULTS, MAX_RESULT_CHARS), (_KEEP_FULL_RESULTS, 400), (0, 200)):
        text = build(keep, per, inputs)
        if fits(text):
            return text, True
    assert max_input_tokens is not None  # noqa: S101 - fits() is always true without a limit
    skeleton = build(0, 200, dict.fromkeys(inputs, ""))
    available = max_input_tokens * CHARS_PER_TOKEN - len(system) - len(skeleton)
    if inputs and available // len(inputs) >= _MIN_INPUT_SHARE_CHARS:
        share = available // len(inputs)
        trimmed = {label: trim_text(text, share) for label, text in inputs.items()}
        text = build(0, 200, trimmed)
        if fits(text):
            return text, True
    raise ContextTooLarge(f"prompt does not fit {max_input_tokens} tokens")
