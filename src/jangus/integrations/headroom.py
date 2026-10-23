"""Experimental context compaction; preserve original evidence by exact reference."""

import hashlib
import re
from dataclasses import dataclass

from jangus.integrations.advice.contracts import token_estimate


@dataclass(frozen=True)
class CompactContext:
    excerpt: str
    original_sha256: str
    reference: str
    original_estimated_tokens: int
    excerpt_estimated_tokens: int
    truncated: bool


def compact(
    text: str,
    *,
    reference: str,
    max_bytes: int = 1024,
    required_exact: tuple[str, ...] = (),
) -> CompactContext:
    """Never shorten critical constraints, IDs or evidence. Refuse insufficient room."""
    if not 1 <= max_bytes <= 8192 or not 2 <= len(reference) <= 128:
        raise ValueError("context_invalid")
    if len(required_exact) > 16 or any(not term or term not in text for term in required_exact):
        raise ValueError("required_evidence_missing")
    lines = text.splitlines(keepends=True)
    protected = set()
    for i, line in enumerate(lines):
        if any(term in line for term in required_exact):
            protected.add(i)
        elif re.search(r"\b(MUST|NEVER|ERROR|SHA-256|INVALID|FAILED)\b", line):
            protected.add(i)
    # Start with exact required and high-impact lines, always in original order.
    chosen = set(protected)
    used = sum(len(lines[i].encode()) for i in chosen)
    if used > max_bytes:
        raise ValueError("required_evidence_exceeds_budget")
    for i, line in enumerate(lines):
        if i not in chosen and used + len(line.encode()) <= max_bytes:
            chosen.add(i)
            used += len(line.encode())
    excerpt = "".join(lines[i] for i in sorted(chosen))
    return CompactContext(
        excerpt=excerpt,
        original_sha256=hashlib.sha256(text.encode()).hexdigest(),
        reference=reference,
        original_estimated_tokens=token_estimate(text),
        excerpt_estimated_tokens=token_estimate(excerpt),
        truncated=len(chosen) != len(lines),
    )


def verify_reference(original: str, data: CompactContext):
    if hashlib.sha256(original.encode()).hexdigest() != data.original_sha256:
        raise ValueError("original_evidence_changed")
    if data.excerpt not in original and data.excerpt != original:
        # Joined non-adjacent lines are allowed: validate each full excerpt line.
        source = original.splitlines(keepends=True)
        cursor = 0
        for line in data.excerpt.splitlines(keepends=True):
            try:
                index = source.index(line, cursor)
            except ValueError:
                raise ValueError("context_excerpt_changed") from None
            cursor = index + 1
