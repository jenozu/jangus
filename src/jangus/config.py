"""Read-only configuration validation. This module never starts services or creates paths."""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from yaml.tokens import AliasToken, AnchorToken

MAX_CONFIG_BYTES = 64 * 1024


class ConfigurationError(ValueError):
    """A safe-to-display error that does not echo configuration values."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PathsConfig(StrictModel):
    data_dir: str = Field(min_length=1)
    workspace_dir: str = Field(min_length=1)


class ModelConfig(StrictModel):
    provider: str = "ollama"
    endpoint: str = "http://127.0.0.1:11434"
    name: str | None = None
    max_concurrent_inference: int = Field(default=1, ge=1, le=1)

    @field_validator("provider")
    @classmethod
    def supported_provider(cls, value: str) -> str:
        if value != "ollama":
            raise ValueError("only the planned local provider is supported")
        return value

    @field_validator("name")
    @classmethod
    def model_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("model name must not be blank")
        return value

    @field_validator("endpoint")
    @classmethod
    def local_endpoint(cls, value: str) -> str:
        try:
            url = urlsplit(value)
            port = url.port
        except ValueError as exc:
            raise ValueError("invalid local endpoint") from exc
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
            or port is None
            or not 1 <= port <= 65535
        ):
            raise ValueError("endpoint must be a loopback HTTP URL with a port")
        return value


class FeaturesConfig(StrictModel):
    remote_enabled: bool = False
    codex_enabled: bool = False
    astra_enabled: bool = False

    @field_validator("remote_enabled", "codex_enabled", "astra_enabled")
    @classmethod
    def not_implemented(cls, value: bool) -> bool:
        if value:
            raise ValueError("feature is not implemented in this phase")
        return value


class PermissionsConfig(StrictModel):
    allowed_tools: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("allowed_tools")
    @classmethod
    def registered_tools(cls, value: list[str]) -> list[str]:
        known = {"file.list", "file.read", "file.hash", "file.copy", "file.report"}
        if len(value) != len(set(value)) or not set(value) <= known:
            raise ValueError("Only unique registered file tools are supported")
        return value

    allow_network: bool = False

    @field_validator("allow_network")
    @classmethod
    def no_network(cls, value: bool) -> bool:
        if value:
            raise ValueError("network execution is not implemented")
        return value


class LimitsConfig(StrictModel):
    max_actions: int = Field(default=20, ge=1, le=100)
    max_attempts_per_task: int = Field(default=2, ge=1, le=5)
    run_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    context_tokens: int = Field(default=4096, ge=1024, le=32768)
    response_tokens: int = Field(default=1024, ge=128, le=8192)


class LoggingConfig(StrictModel):
    level: str = "INFO"
    retention_days: int = Field(default=14, ge=1, le=90)
    max_total_mb: int = Field(default=256, ge=1, le=4096)
    redact_secrets: bool = True

    @field_validator("level")
    @classmethod
    def known_level(cls, value: str) -> str:
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("unknown log level")
        return value

    @field_validator("redact_secrets")
    @classmethod
    def require_redaction(cls, value: bool) -> bool:
        if not value:
            raise ValueError("secret redaction cannot be disabled")
        return value


class JangusConfig(StrictModel):
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    paths: PathsConfig
    model: ModelConfig = Field(default_factory=ModelConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class _UniqueSafeLoader(yaml.SafeLoader):
    """Reject duplicate/non-string keys instead of silently replacing values."""

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ConfigurationError("YAML keys must be unique strings.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _checkout_roots() -> set[Path]:
    roots = set()
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for candidate in (start, *start.parents):
            if (candidate / ".git").exists() or (
                (candidate / "pyproject.toml").exists() and (candidate / "rules.md").exists()
            ):
                roots.add(candidate.resolve())
    return roots


def _external_path(value: str, repositories: set[Path]) -> Path:
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ConfigurationError("Storage paths must be absolute or start with ~/.")
        path = path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError("Storage path cannot be resolved.") from None
    if any(path.is_relative_to(repo) or repo.is_relative_to(path) for repo in repositories):
        raise ConfigurationError("Storage paths must not overlap a repository checkout.")
    if path.exists() and not path.is_dir():
        raise ConfigurationError("Storage paths must be directories.")
    return path


def load_config(path: Path, *, repository_root: Path | None = None) -> JangusConfig:
    """Validate a bounded YAML file without writing files or contacting providers.

    Runtime authorization is implemented separately by the Phase 4 controller.
    The optional repository_root adds a boundary, never removes detected checkouts.
    """
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
    except OSError:
        raise ConfigurationError("Configuration file cannot be read.") from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigurationError("Configuration file exceeds 64 KiB.")
    try:
        text = raw.decode("utf-8-sig")
        if any(isinstance(token, (AliasToken, AnchorToken)) for token in yaml.scan(text)):
            raise ConfigurationError("YAML anchors and aliases are not supported.")
        # _UniqueSafeLoader subclasses SafeLoader, so this is safe loading.
        data = yaml.load(text, Loader=_UniqueSafeLoader)  # nosec B506
    except (UnicodeError, yaml.YAMLError, RecursionError):
        raise ConfigurationError("Invalid UTF-8 YAML configuration.") from None
    if not isinstance(data, dict):
        raise ConfigurationError("Configuration must be a YAML mapping.")
    try:
        config = JangusConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(
            f"Invalid configuration ({exc.error_count()} schema violation(s)); "
            "check keys and types against 'jangus config schema'."
        ) from None

    if config.limits.response_tokens >= config.limits.context_tokens:
        raise ConfigurationError("Response tokens must be smaller than the context budget.")
    repositories = _checkout_roots()
    if repository_root is not None:
        repositories.add(repository_root.resolve())
    data_dir = _external_path(config.paths.data_dir, repositories)
    workspace_dir = _external_path(config.paths.workspace_dir, repositories)
    if data_dir.is_relative_to(workspace_dir) or workspace_dir.is_relative_to(data_dir):
        raise ConfigurationError("Data and workspace directories must not overlap.")
    return config
