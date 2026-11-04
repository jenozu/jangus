"""Build the provider hub from config. Which providers are enabled is the owner's choice."""

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
        "ollama": OllamaProvider(cfg.model.url, ollama_transport),
        "claude": ClaudeProvider(lambda: secrets.get(CLAUDE_KEY_NAME), claude_transport),
    }
    enabled = {"ollama"} | ({"claude"} if cfg.claude_enabled else set())
    return ProviderHub(providers, enabled, alert, blocked)
