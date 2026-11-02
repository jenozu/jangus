"""Secret resolution.

Secrets come only from Windows Credential Manager or the environment, and are
injected by the kernel into a tool call. They are never placed in prompts,
ledger entries, state, errors or results. Each agent may use only the secrets
its approved spec names.
"""

import os
from collections.abc import Collection, Mapping
from typing import Any, Protocol

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.registry import ToolSpec

REDACTED = "[REDACTED]"


class SecretBackend(Protocol):
    def get(self, name: str) -> str | None: ...


class EnvBackend:
    """Reads JANGUS_SECRET_<NAME> from the environment."""

    def __init__(self, environ: Mapping[str, str] | None = None, prefix: str = "JANGUS_SECRET_"):
        self._environ = os.environ if environ is None else environ
        self._prefix = prefix

    def get(self, name: str) -> str | None:
        return self._environ.get(self._prefix + name.upper().replace(".", "_"))


class MemoryBackend:
    """In-memory backend for tests."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def get(self, name: str) -> str | None:
        return self._values.get(name)


class WindowsCredentialBackend:
    """Windows Credential Manager through the optional `keyring` package.

    Not exercised in CI (no Windows credential store there). Importing is lazy
    so the kernel stays dependency-light; if keyring is missing it fails closed.
    """

    def __init__(self, service: str = "jangus") -> None:
        self._service = service

    def get(self, name: str) -> str | None:
        try:
            import keyring  # type: ignore[import-not-found,unused-ignore]
        except ImportError as exc:
            raise RuntimeFault(
                FaultCode.SECRET_BACKEND_UNAVAILABLE, "keyring is not installed"
            ) from exc
        value: Any = keyring.get_password(self._service, name)
        return None if value is None else str(value)


def resolve_secrets(
    backend: SecretBackend | None, tool: ToolSpec, allowed: Collection[str]
) -> dict[str, str]:
    """Return the secrets this tool declares, if and only if the agent may use them."""
    resolved: dict[str, str] = {}
    for name in tool.secrets:
        if name not in allowed:
            raise RuntimeFault(FaultCode.SECRET_DENIED, f"agent spec does not name secret {name!r}")
        if backend is None:
            raise RuntimeFault(FaultCode.SECRET_BACKEND_UNAVAILABLE, "no secret backend")
        value = backend.get(name)
        if not value:
            raise RuntimeFault(FaultCode.SECRET_MISSING, f"secret {name!r} is not available")
        resolved[name] = value
    return resolved


def redact(value: Any, secrets: Mapping[str, str]) -> tuple[Any, bool]:
    """Replace any secret value found in a JSON-able result. Returns (clean, changed)."""
    import json

    text = json.dumps(value, sort_keys=True)
    changed = False
    for secret in secrets.values():
        if secret and secret in text:
            text = text.replace(secret, REDACTED)
            changed = True
    return (json.loads(text) if changed else value), changed
