"""Bounded, loopback-only Ollama JSON inference with no cloud fallback."""

import json
import threading
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import Field, field_validator

from jangus.config import ModelConfig, StrictModel

MAX_RESPONSE = 64 * 1024
MAX_PROMPT = 24 * 1024


class ModelFailure(Exception):
    """Safe error code, never raw response or endpoint details."""


class Message(StrictModel):
    role: str
    content: str = Field(min_length=1, max_length=MAX_PROMPT)

    @field_validator("role")
    @classmethod
    def known_role(cls, value: str) -> str:
        if value not in {"system", "user"}:
            raise ValueError("unsupported message role")
        return value


class ModelReply(StrictModel):
    value: dict
    reported_prompt_tokens: int | None = None
    reported_output_tokens: int | None = None
    usage_source: str = "provider_reported"


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


class OllamaProvider:
    """One request at a time; cancellation is cooperative around a bounded HTTP call."""

    def __init__(self, config: ModelConfig, *, opener=None):
        if not config.name or not config.name.strip():
            raise ModelFailure("model_unconfigured")
        url = urlsplit(config.endpoint)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "::1"}
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or not url.port
            or config.max_concurrent_inference != 1
        ):
            raise ModelFailure("non_loopback_endpoint")
        self.model = config.name
        self.endpoint = config.endpoint.rstrip("/") + "/api/chat"
        # Explicitly ignore HTTP_PROXY and HTTPS_PROXY for local inference.
        self.opener = opener if opener is not None else build_opener(ProxyHandler({}))
        self._semaphore = threading.BoundedSemaphore(1)

    def generate(
        self,
        messages: list[Message],
        *,
        timeout: float = 60,
        cancelled: Callable[[], bool] | None = None,
    ) -> ModelReply:
        if not messages or not 1 <= timeout <= 300:
            raise ModelFailure("invalid_request")
        if cancelled is not None and cancelled():
            raise ModelFailure("cancelled")
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [m.model_dump() for m in messages],
                "format": "json",
                "stream": False,
                "keep_alive": "0",
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 512},
            },
            separators=(",", ":"),
        ).encode()
        if len(payload) > MAX_PROMPT:
            raise ModelFailure("prompt_too_large")
        if not self._semaphore.acquire(blocking=False):
            raise ModelFailure("inference_busy")
        try:
            if cancelled is not None and cancelled():
                raise ModelFailure("cancelled")
            request = Request(
                self.endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    if response.status != 200:
                        raise ModelFailure("provider_unavailable")
                    raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ModelFailure("response_too_large")
                data = json.loads(raw, object_pairs_hook=_unique_pairs)
                if not isinstance(data, dict):
                    raise ValueError("unexpected envelope")
                msg = data["message"]
                if not isinstance(msg, dict) or not isinstance(msg["content"], str):
                    raise ValueError("invalid message")
                value = json.loads(msg["content"], object_pairs_hook=_unique_pairs)
                if not isinstance(value, dict):
                    raise ValueError("expected object")
            except (HTTPError, URLError, TimeoutError, OSError):
                raise ModelFailure("provider_unavailable") from None
            except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
                raise ModelFailure("invalid_provider_response") from None
            if cancelled is not None and cancelled():
                raise ModelFailure("cancelled")
            counts = []
            for name in ("prompt_eval_count", "eval_count"):
                count = data.get(name)
                counts.append(count if type(count) is int and count >= 0 else None)
            return ModelReply(
                value=value,
                reported_prompt_tokens=counts[0],
                reported_output_tokens=counts[1],
            )
        finally:
            self._semaphore.release()
