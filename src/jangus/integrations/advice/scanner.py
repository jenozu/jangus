"""Deterministic disclosure scan for evidence that may leave the machine.

A caller-asserted ``redacted`` flag is not proof. This scanner is a second,
mechanical gate: any hit blocks external sharing. It is a deny-list of common
credential shapes and private path forms, not a guarantee that text is safe, so
the owner still reviews the exact packet before approval.
"""

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("api_key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    (
        "credential_assignment",
        re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S{4,}"),
    ),
    ("url_credentials", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("home_path", re.compile(r"(?i)(?:/home/|/Users/|[A-Z]:\\Users\\|~/)[^\s\"']+")),
    ("long_hex_secret", re.compile(r"\b[0-9a-fA-F]{40,}\b")),
)


def scan_text(text: str) -> list[str]:
    """Return sorted rule names that matched; never return matched content."""
    return sorted({name for name, pattern in _PATTERNS if pattern.search(text)})
