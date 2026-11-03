"""Ollama provider: loopback only, non-streaming /api/chat."""

import json
import time
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
from jangus.config import check_loopback_url


class OllamaProvider:
    name = "ollama"

    def __init__(self, url: str, transport: Transport | None = None) -> None:
        # Same check as config: http://127.0.0.1:<port> or http://[::1]:<port> only.
        self._url = check_loopback_url(url).rstrip("/") + "/api/chat"
        self._transport = transport or urllib_transport(use_proxy_env=False)

    def complete(self, request: ModelRequest) -> ModelResponse:
        body: dict[str, Any] = {
            "model": request.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "options": {"num_predict": request.max_tokens, "temperature": 0},
        }
        if request.json_schema is not None:
            body["format"] = request.json_schema
        if request.keep_alive is not None:
            body["keep_alive"] = request.keep_alive
        started = time.monotonic()
        try:
            response = self._transport(
                HttpRequest(
                    self._url,
                    {"Content-Type": "application/json"},
                    json.dumps(body).encode("utf-8"),
                    request.timeout_seconds,
                )
            )
        except (OSError, TimeoutError) as exc:
            raise failed(f"ollama request failed ({type(exc).__name__})") from None
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status != 200:
            raise failed(f"ollama returned HTTP {response.status}")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise bad_response("ollama response is too large")
        return self._parse(response.body, request.model, latency_ms)

    def _parse(self, raw: bytes, model: str, latency_ms: int) -> ModelResponse:
        try:
            data = json.loads(raw)
        except ValueError:
            raise bad_response("ollama response is not JSON") from None
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or data.get("done") is not True:
            raise bad_response("ollama response is incomplete or has no text")
        # Ollama may omit prompt_eval_count when the prompt was served from its own cache;
        # a missing count becomes usage_unknown rather than a guess.
        usage = Usage(
            input_tokens=token_count(data.get("prompt_eval_count")),
            output_tokens=token_count(data.get("eval_count")),
        )
        reason = data.get("done_reason")
        stop: StopReason = (
            "end" if reason in (None, "stop") else "length" if reason == "length" else "other"
        )
        return ModelResponse(content, usage, self.name, model, latency_ms, stop)
