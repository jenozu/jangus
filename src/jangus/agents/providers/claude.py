"""Claude provider (Anthropic Messages API).

The API key arrives through an injected getter backed by the kernel's secret
backend. It is placed in one request header and nowhere else: not in the body,
not in logs, not in error text. Error messages carry only the HTTP status or the
exception class name.

Notes:
- `json_schema` is not sent: structure is enforced by the kernel's strict parse
  of proposals, not trusted from the provider.
- `effort` is recorded in specs but not sent to the API yet.
"""

import json
import time
from collections.abc import Callable
from typing import Any

from jangus.agents.providers.base import (
    ModelRequest,
    ModelResponse,
    StopReason,
    Usage,
    bad_response,
    failed,
    token_count,
)
from jangus.agents.providers.http import (
    MAX_RESPONSE_BYTES,
    HttpRequest,
    Transport,
    urllib_transport,
)
from jangus.errors import FaultCode, RuntimeFault

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class ClaudeProvider:
    name = "claude"

    def __init__(
        self,
        get_api_key: Callable[[], str | None],
        transport: Transport | None = None,
        url: str = API_URL,
    ) -> None:
        if not url.startswith("https://"):
            raise ValueError("Claude API url must be https")
        self._get_key = get_api_key
        self._url = url
        self._transport = transport or urllib_transport(use_proxy_env=True)

    def complete(self, request: ModelRequest) -> ModelResponse:
        key = self._get_key()
        if not key:
            raise RuntimeFault(FaultCode.SECRET_MISSING, "Anthropic API key is not available")
        system: Any = request.system
        if request.cache_prefix:
            system = [
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ]
        body = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": request.user}],
        }
        started = time.monotonic()
        try:
            response = self._transport(
                HttpRequest(
                    self._url,
                    {
                        "content-type": "application/json",
                        "x-api-key": key,
                        "anthropic-version": API_VERSION,
                    },
                    json.dumps(body).encode("utf-8"),
                    request.timeout_seconds,
                )
            )
        except (OSError, TimeoutError) as exc:
            raise failed(f"claude request failed ({type(exc).__name__})") from None
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status != 200:
            raise failed(f"claude returned HTTP {response.status}")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise bad_response("claude response is too large")
        return self._parse(response.body, request.model, latency_ms)

    def _parse(self, raw: bytes, model: str, latency_ms: int) -> ModelResponse:
        try:
            data = json.loads(raw)
        except ValueError:
            raise bad_response("claude response is not JSON") from None
        blocks = data.get("content") if isinstance(data, dict) else None
        if not isinstance(blocks, list):
            raise bad_response("claude response has no content")
        texts: list[str] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    raise bad_response("claude text block is malformed")
                texts.append(text)
        raw_usage = data.get("usage")
        usage_dict = raw_usage if isinstance(raw_usage, dict) else {}
        usage = Usage(
            input_tokens=token_count(usage_dict.get("input_tokens")),
            output_tokens=token_count(usage_dict.get("output_tokens")),
            cached_tokens=token_count(usage_dict.get("cache_read_input_tokens")),
            cache_write_tokens=token_count(usage_dict.get("cache_creation_input_tokens")),
        )
        reason = data.get("stop_reason")
        stop: StopReason = (
            "end"
            if reason in ("end_turn", "stop_sequence")
            else "length"
            if reason == "max_tokens"
            else "other"
        )
        return ModelResponse("".join(texts), usage, self.name, model, latency_ms, stop)
