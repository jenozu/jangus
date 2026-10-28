"""Operational logging to stderr. Honors ``logging.level``; never records content.

Messages carry stable fault codes and counters, not file names, manifests,
memory text, or credentials. As defence in depth, the formatter also masks
common credential shapes, and ``logging.redact_secrets`` cannot be disabled.
"""

import logging
import os
import re
import sys

_MASK = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+"
    r"|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"
    r"|\bsk-[A-Za-z0-9_-]{16,}\b)"
)
LOGGER = "jangus"


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _MASK.sub("[redacted]", super().format(record))


def debug_requested(flag: bool = False) -> bool:
    return flag or os.environ.get("JANGUS_DEBUG", "") in {"1", "true", "yes"}


def configure(level: str = "INFO", *, debug: bool = False) -> logging.Logger:
    """Idempotent: replaces only the handler this module installed."""
    logger = logging.getLogger(LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, "_jangus", False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler._jangus = True  # type: ignore[attr-defined]
    handler.setFormatter(
        _RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug_requested(debug) else getattr(logging, level))
    logger.propagate = False
    return logger


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER}.{name}")
