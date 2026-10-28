"""Strict external advice contracts; model output is never execution authority."""

import hashlib
import json
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from jangus.config import StrictModel
from jangus.integrations.advice.scanner import scan_text


class AdviceFault(ValueError):
    """Stable error code without private evidence."""


class Evidence(StrictModel):
    reference: str = Field(pattern=r"^[A-Za-z0-9._:/#-]{2,120}$")
    text: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sharing: Literal["local", "external-ok"]
    redacted: bool = False

    @model_validator(mode="after")
    def unchanged(self):
        if hashlib.sha256(self.text.encode()).hexdigest() != self.sha256:
            raise ValueError("evidence revision changed")
        return self


class AdvicePolicy(StrictModel):
    codex_enabled: bool = False
    astra_enabled: bool = False
    max_calls: int = Field(default=0, ge=0, le=5)
    max_estimated_tokens: int = Field(default=0, ge=0, le=24000)
    max_reserved_microdollars: int = Field(default=0, ge=0, le=2500000)
    timeout_seconds: int = Field(default=15, ge=1, le=120)
    max_answer_bytes: int = Field(default=2048, ge=128, le=4096)


class Suggestion(StrictModel):
    summary: str = Field(min_length=1, max_length=1000)
    action_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("action_ids")
    @classmethod
    def valid_actions(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(
            re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", item) is None for item in value
        ):
            raise ValueError("invalid action references")
        return value


def token_estimate(text: str) -> int:
    """Byte heuristic, never an observed provider token count."""
    return (len(text.encode()) + 3) // 4


def pack_evidence(items: list[Evidence]) -> str:
    if not 1 <= len(items) <= 6:
        raise AdviceFault("evidence_unavailable")
    items = [Evidence.model_validate(item.model_dump()) for item in items]
    if any(item.sharing != "external-ok" or not item.redacted for item in items):
        raise AdviceFault("disclosure_denied")
    # The redacted flag is caller-asserted; the scanner is the mechanical check.
    # The sha256 field is intentionally not scanned: it is a digest, not content.
    if any(scan_text(item.text) or scan_text(item.reference) for item in items):
        raise AdviceFault("evidence_unsafe")
    raw = json.dumps(
        [{"reference": e.reference, "sha256": e.sha256, "text": e.text} for e in items],
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(raw.encode()) > 4096:
        raise AdviceFault("evidence_too_large")
    return raw
