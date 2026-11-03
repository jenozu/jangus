"""Provider interface and the hub that enforces "no silent fallback" (B11).

A provider turns a request into text plus *measured* token usage. Whatever it
returns is untrusted data: the run loop parses it into proposals and the kernel
validates them. Providers hold no tools, run nothing and read no files.

The hub calls exactly the provider it is asked for. If that provider is
disabled or fails, the hub raises and raises an alert. It never tries another
provider, a cheaper model, or a paid model instead.
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from jangus.errors import FaultCode, RuntimeFault

StopReason = Literal["end", "length", "other"]
AlertFn = Callable[[str, str], None]


@dataclass(frozen=True)
class ModelRequest:
    """Stable prefix first (system), then the per-task user text, for prompt caching.

    `model` comes from an owner-approved spec (or the kernel's step-up choice), never from
    model output.
    """

    model: str
    system: str
    user: str
    max_tokens: int
    json_schema: dict[str, Any] | None = None
    cache_prefix: bool = False
    timeout_seconds: float = 120.0
    keep_alive: str | None = None  # Ollama only: how long to keep the model loaded

    def __post_init__(self) -> None:
        if not self.model or self.max_tokens < 1 or self.timeout_seconds <= 0:
            raise ValueError("model must be set; max_tokens and timeout_seconds must be positive")


@dataclass(frozen=True)
class Usage:
    """Measured by the provider. None means the provider did not report it.

    `input_tokens` is the uncached input the provider billed or processed.
    `cached_tokens` is input served from a prompt cache; `cache_write_tokens` is
    input written to a cache (priced differently by some providers).
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None

    @property
    def known(self) -> bool:
        """False means `usage_unknown`: the caller must charge the pre-call estimate."""
        return self.input_tokens is not None and self.output_tokens is not None


@dataclass(frozen=True)
class ModelResponse:
    text: str
    usage: Usage
    provider: str
    model: str
    latency_ms: int
    stop_reason: StopReason


class ModelProvider(Protocol):
    name: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...


def token_count(value: object) -> int | None:
    """A reported count, or None when absent. Wrong types and negatives are a bad response."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise bad_response("usage count is not a non-negative integer")
    return value


def bad_response(reason: str) -> RuntimeFault:
    return RuntimeFault(FaultCode.PROVIDER_BAD_RESPONSE, reason)


def failed(reason: str) -> RuntimeFault:
    return RuntimeFault(FaultCode.PROVIDER_FAILED, reason)


class ProviderHub:
    def __init__(
        self,
        providers: Mapping[str, ModelProvider],
        enabled: Collection[str],
        alert: AlertFn,
        blocked: Callable[[str], bool] | None = None,
    ) -> None:
        self._providers = dict(providers)
        self._enabled = frozenset(enabled)
        self._alert = alert
        self._blocked = blocked
        self.alerts_lost = 0  # alerts the sink failed to take; surfaced by status later

    def _raise_alert(self, kind: str, message: str) -> None:
        try:
            self._alert(kind, message)
        except Exception:  # noqa: BLE001 - a broken alert sink must not hide the original stop
            self.alerts_lost += 1

    def complete(self, provider: str, request: ModelRequest) -> ModelResponse:
        chosen = self._providers.get(provider)
        if chosen is None or provider not in self._enabled:
            message = f"provider {provider!r} is disabled or not configured"
            self._raise_alert("provider_disabled", message)
            raise RuntimeFault(FaultCode.PROVIDER_DISABLED, message)
        if self._blocked is not None and self._blocked(provider):
            message = f"provider {provider!r} is blocked until the owner acts"
            self._raise_alert("provider_blocked", message)
            raise RuntimeFault(FaultCode.PROVIDER_BLOCKED, message)
        try:
            return chosen.complete(request)
        except RuntimeFault as fault:
            self._raise_alert("provider_failed", f"{provider}: {fault.code.value}")
            raise
        except Exception:
            self._raise_alert("provider_failed", f"{provider}: unexpected error")
            raise failed("unexpected provider error") from None
