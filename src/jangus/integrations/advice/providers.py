"""Opt-in provider transports; no providers are invoked by default."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from jangus.integrations.advice.contracts import AdviceFault


class ManualCodexReview:
    """Operator pastes a reviewed Codex suggestion; no SDK, tools or network calls."""

    def __init__(self, review: str):
        self.review = review

    def __call__(self, packet: str, timeout_seconds: int, max_bytes: int) -> str:
        if not packet or not 1 <= timeout_seconds <= 120:
            raise AdviceFault("review_invalid")
        if len(self.review.encode()) > max_bytes:
            raise AdviceFault("review_too_large")
        return self.review


class AstraTextOnly:
    """Explicit opt-in Responses API request: no tools, no conversation storage."""

    def __init__(self, api_key: str, *, opener=None):
        if not api_key or len(api_key) < 20:
            raise AdviceFault("credential_unavailable")
        self.key = api_key
        self.opener = opener or build_opener(ProxyHandler({}))

    def __call__(self, packet: str, timeout_seconds: int, max_bytes: int) -> str:
        body = json.dumps(
            {
                "model": "gpt-6-astra",
                "input": packet,
                "instructions": (
                    "Offer read-only advice. Return a JSON object with exactly "
                    "summary (string) and action_ids (array of existing identifiers). "
                    "Do not request tools or invent permissions."
                ),
                "tools": [],
                "tool_choice": "none",
                "store": False,
                "max_output_tokens": min(max_bytes, 512),
            },
            separators=(",", ":"),
        ).encode()
        request = Request(
            "https://api.openai.com/v1/responses",
            data=body,
            headers={
                "Authorization": "Bearer " + self.key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=timeout_seconds) as reply:
                if reply.status != 200:
                    raise AdviceFault("provider_unavailable")
                raw = reply.read(16385)
            if len(raw) > 16384:
                raise AdviceFault("provider_response_too_large")
            data = json.loads(raw)
            outputs = [
                c["text"]
                for item in data["output"]
                if item["type"] == "message"
                for c in item["content"]
                if c["type"] == "output_text"
            ]
            if data.get("status") != "completed" or len(outputs) != 1:
                raise AdviceFault("provider_response_invalid")
            return outputs[0]
        except (HTTPError, URLError, TimeoutError, OSError):
            raise AdviceFault("provider_unavailable") from None
        except (TypeError, ValueError, KeyError, RecursionError):
            raise AdviceFault("provider_response_invalid") from None
