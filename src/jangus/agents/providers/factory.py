"""Build the provider hub from config. Which providers are enabled is the owner's choice.

The default cheap model is Claude Haiku over the direct Anthropic API (ADR-002, 2026-11-04).
The local Ollama provider is optional: it exists only when the owner configured a loopback
endpoint and set `ollama_enabled`. Neither provider is enabled by default, and the hub never
substitutes one for the other (no silent provider fallback).
"""

from collections.abc import Callable

from jangus.agents.providers.base import AlertFn, ModelProvider, ProviderHub
from jangus.agents.providers.claude import ClaudeProvider
from jangus.agents.providers.http import Transport
from jangus.agents.providers.ollama import OllamaProvider
from jangus.config import JangusConfig
from jangus.kernel.secrets import SecretBackend

CLAUDE_KEY_NAME = "anthropic_api_key"


def build_hub(
    cfg: JangusConfig,
    secrets: SecretBackend,
    alert: AlertFn,
    ollama_transport: Transport | None = None,
    claude_transport: Transport | None = None,
    blocked: Callable[[str], bool] | None = None,
) -> ProviderHub:
    providers: dict[str, ModelProvider] = {
        "claude": ClaudeProvider(lambda: secrets.get(CLAUDE_KEY_NAME), claude_transport),
    }
    enabled: set[str] = {"claude"} if cfg.claude_enabled else set()
    if cfg.model is not None:
        providers["ollama"] = OllamaProvider(cfg.model.url, ollama_transport)
        if cfg.ollama_enabled:
            enabled.add("ollama")
    return ProviderHub(providers, enabled, alert, blocked)
