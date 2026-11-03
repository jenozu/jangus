import json
import threading
import urllib.error
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from jangus.agents.providers.base import ModelRequest, ProviderHub, Usage
from jangus.agents.providers.claude import ClaudeProvider
from jangus.agents.providers.factory import build_hub
from jangus.agents.providers.http import HttpRequest, HttpResponse, urllib_transport
from jangus.agents.providers.ollama import OllamaProvider
from jangus.config import parse_config
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.secrets import MemoryBackend

KEY = "sk-ant-CANARY-0123456789"


class FakeTransport:
    def __init__(self, *replies: HttpResponse | BaseException) -> None:
        self.replies = list(replies)
        self.requests: list[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def ok(payload: dict[str, Any]) -> HttpResponse:
    return HttpResponse(200, json.dumps(payload).encode())


def req(**kw: Any) -> ModelRequest:
    base: dict[str, Any] = {"model": "m1", "system": "SYS", "user": "USER", "max_tokens": 256}
    base.update(kw)
    return ModelRequest(**base)


def fault_of(call: Callable[[], object]) -> RuntimeFault:
    with pytest.raises(RuntimeFault) as info:
        call()
    return info.value


OLLAMA_OK = {
    "message": {"role": "assistant", "content": '{"proposals": []}'},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 120,
    "eval_count": 15,
}


# ---- Ollama -----------------------------------------------------------------


def test_ollama_request_shape_and_measured_usage() -> None:
    transport = FakeTransport(ok(OLLAMA_OK))
    provider = OllamaProvider("http://127.0.0.1:11434", transport)
    schema = {"type": "object"}
    response = provider.complete(req(json_schema=schema, keep_alive="5m"))

    [sent] = transport.requests
    assert sent.url == "http://127.0.0.1:11434/api/chat"
    body = json.loads(sent.body)
    assert body["model"] == "m1" and body["stream"] is False
    assert body["format"] == schema and body["keep_alive"] == "5m"
    assert body["options"] == {"num_predict": 256, "temperature": 0}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]  # stable prefix first
    assert response.text == '{"proposals": []}'
    assert response.usage == Usage(input_tokens=120, output_tokens=15)
    assert response.usage.known and response.stop_reason == "end"
    assert (response.provider, response.model) == ("ollama", "m1")


def test_ollama_missing_prompt_count_is_usage_unknown() -> None:
    payload = {k: v for k, v in OLLAMA_OK.items() if k != "prompt_eval_count"}
    response = OllamaProvider("http://127.0.0.1:11434", FakeTransport(ok(payload))).complete(req())
    assert response.usage.known is False
    assert response.usage.output_tokens == 15


def test_ollama_length_stop_is_reported() -> None:
    payload = {**OLLAMA_OK, "done_reason": "length"}
    response = OllamaProvider("http://127.0.0.1:11434", FakeTransport(ok(payload))).complete(req())
    assert response.stop_reason == "length"


@pytest.mark.parametrize(
    "bad",
    [
        {"message": {"content": "x"}, "done": False},
        {"message": {"content": 5}, "done": True},
        {"done": True},
        [],
        {**OLLAMA_OK, "eval_count": -1},
        {**OLLAMA_OK, "eval_count": "15"},
        {**OLLAMA_OK, "prompt_eval_count": True},
    ],
)
def test_ollama_malformed_response_is_bad_response(bad: Any) -> None:
    provider = OllamaProvider("http://127.0.0.1:11434", FakeTransport(ok(bad)))
    assert fault_of(lambda: provider.complete(req())).code is FaultCode.PROVIDER_BAD_RESPONSE


def test_ollama_non_json_and_oversize_are_bad_responses() -> None:
    for reply in (HttpResponse(200, b"<html>"), HttpResponse(200, b"x" * (4 * 1024 * 1024 + 2))):
        provider = OllamaProvider("http://127.0.0.1:11434", FakeTransport(reply))
        assert (
            fault_of(lambda p=provider: p.complete(req())).code is FaultCode.PROVIDER_BAD_RESPONSE
        )  # type: ignore[misc]


@pytest.mark.parametrize(
    "reply",
    [
        HttpResponse(500, b""),
        HttpResponse(302, b""),
        urllib.error.URLError("refused"),
        TimeoutError(),
    ],
)
def test_ollama_transport_failures_are_provider_failed(reply: Any) -> None:
    provider = OllamaProvider("http://127.0.0.1:11434", FakeTransport(reply))
    assert fault_of(lambda: provider.complete(req())).code is FaultCode.PROVIDER_FAILED


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://192.168.1.5:11434",
        "http://example.com:11434",
        "https://127.0.0.1:11434",
        "http://user:pw@127.0.0.1:11434",
        "http://127.0.0.1",
        "http://127.0.0.1:11434/v1",
    ],
)
def test_ollama_refuses_non_loopback_endpoints(url: str) -> None:
    with pytest.raises(ValueError, match="endpoint_not_loopback"):
        OllamaProvider(url)


def test_ollama_ipv6_loopback_accepted() -> None:
    transport = FakeTransport(ok(OLLAMA_OK))
    OllamaProvider("http://[::1]:11434", transport).complete(req())
    assert transport.requests[0].url == "http://[::1]:11434/api/chat"


def test_request_validation() -> None:
    for kw in ({"model": ""}, {"max_tokens": 0}, {"timeout_seconds": 0}):
        with pytest.raises(ValueError):
            req(**kw)


# ---- Claude -----------------------------------------------------------------

CLAUDE_OK = {
    "content": [
        {"type": "text", "text": "hello "},
        {"type": "tool_use", "id": "x", "name": "n", "input": {}},
        {"type": "text", "text": "world"},
    ],
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 50,
        "output_tokens": 7,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 0,
    },
}


def claude(transport: FakeTransport, key: str | None = KEY) -> ClaudeProvider:
    return ClaudeProvider(lambda: key, transport)


def test_claude_request_shape_cache_prefix_and_measured_usage() -> None:
    transport = FakeTransport(ok(CLAUDE_OK), ok(CLAUDE_OK))
    provider = claude(transport)
    response = provider.complete(req(model="haiku-x", cache_prefix=True))
    sent = transport.requests[0]
    assert sent.url == "https://api.anthropic.com/v1/messages"
    assert sent.headers["x-api-key"] == KEY
    assert sent.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(sent.body)
    assert body["model"] == "haiku-x" and body["max_tokens"] == 256
    assert body["system"] == [
        {"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}
    ]
    assert body["messages"] == [{"role": "user", "content": "USER"}]
    assert KEY not in sent.body.decode()  # the key is only ever in the header
    assert response.text == "hello world"
    assert response.usage == Usage(50, 7, 900, 0)
    assert response.usage.known and response.stop_reason == "end"

    provider.complete(req(cache_prefix=False))
    assert json.loads(transport.requests[1].body)["system"] == "SYS"


def test_claude_missing_usage_is_usage_unknown_and_max_tokens_is_length() -> None:
    payload = {"content": [{"type": "text", "text": "t"}], "stop_reason": "max_tokens"}
    response = claude(FakeTransport(ok(payload))).complete(req())
    assert response.usage.known is False and response.stop_reason == "length"


def test_claude_without_key_fails_before_any_request() -> None:
    transport = FakeTransport()
    fault = fault_of(lambda: claude(transport, key=None).complete(req()))
    assert fault.code is FaultCode.SECRET_MISSING
    assert transport.requests == []


@pytest.mark.parametrize("status", [400, 401, 429, 500, 529])
def test_claude_http_errors_do_not_echo_anything(status: int) -> None:
    transport = FakeTransport(HttpResponse(status, KEY.encode()))
    fault = fault_of(lambda: claude(transport).complete(req()))
    assert fault.code is FaultCode.PROVIDER_FAILED
    assert KEY not in str(fault) and str(status) in str(fault)


def test_claude_key_never_appears_in_transport_exception_text() -> None:
    transport = FakeTransport(OSError(f"connection reset while sending {KEY}"))
    fault = fault_of(lambda: claude(transport).complete(req()))
    assert fault.code is FaultCode.PROVIDER_FAILED
    assert KEY not in str(fault)
    assert fault.__cause__ is None and fault.__suppress_context__


@pytest.mark.parametrize(
    "bad",
    [
        {"content": "text"},
        {"content": [{"type": "text", "text": 5}]},
        {"content": [], "usage": {"input_tokens": "5"}},
        [],
    ],
)
def test_claude_malformed_response(bad: Any) -> None:
    fault = fault_of(lambda: claude(FakeTransport(ok(bad))).complete(req()))
    assert fault.code is FaultCode.PROVIDER_BAD_RESPONSE


def test_claude_requires_https() -> None:
    with pytest.raises(ValueError):
        ClaudeProvider(lambda: KEY, url="http://api.anthropic.com/v1/messages")


# ---- Hub: B11 no silent fallback ---------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.alerts: list[tuple[str, str]] = []

    def __call__(self, kind: str, message: str) -> None:
        self.alerts.append((kind, message))


class Counting:
    def __init__(self, name: str, fail: BaseException | None = None) -> None:
        self.name = name
        self.fail = fail
        self.calls = 0

    def complete(self, request: ModelRequest):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.fail:
            raise self.fail
        raise AssertionError("not used in these tests")


def test_b11_failing_provider_stops_and_alerts_and_never_switches() -> None:
    primary = Counting("ollama", RuntimeFault(FaultCode.PROVIDER_FAILED, "boom"))
    other = Counting("claude")
    alerts = Recorder()
    hub = ProviderHub({"ollama": primary, "claude": other}, {"ollama", "claude"}, alerts)
    fault = fault_of(lambda: hub.complete("ollama", req()))
    assert fault.code is FaultCode.PROVIDER_FAILED
    assert (primary.calls, other.calls) == (1, 0)
    assert alerts.alerts == [("provider_failed", "ollama: provider_failed")]


def test_b11_paid_provider_failure_does_not_fall_back_to_local() -> None:
    local = Counting("ollama")
    paid = Counting("claude", RuntimeFault(FaultCode.PROVIDER_FAILED, "429"))
    hub = ProviderHub({"ollama": local, "claude": paid}, {"ollama", "claude"}, Recorder())
    fault_of(lambda: hub.complete("claude", req()))
    assert (local.calls, paid.calls) == (0, 1)


def test_b11_disabled_provider_is_never_called() -> None:
    paid = Counting("claude")
    alerts = Recorder()
    hub = ProviderHub({"claude": paid, "ollama": Counting("ollama")}, {"ollama"}, alerts)
    assert fault_of(lambda: hub.complete("claude", req())).code is FaultCode.PROVIDER_DISABLED
    assert fault_of(lambda: hub.complete("nope", req())).code is FaultCode.PROVIDER_DISABLED
    assert paid.calls == 0
    assert [k for k, _ in alerts.alerts] == ["provider_disabled", "provider_disabled"]


def test_unexpected_error_becomes_generic_fault_without_leaking_text() -> None:
    boom = Counting("ollama", ValueError(f"internal detail {KEY}"))
    hub = ProviderHub({"ollama": boom}, {"ollama"}, Recorder())
    fault = fault_of(lambda: hub.complete("ollama", req()))
    assert fault.code is FaultCode.PROVIDER_FAILED
    assert KEY not in str(fault) and fault.__cause__ is None


def test_broken_alert_sink_does_not_hide_the_stop() -> None:
    def broken(kind: str, message: str) -> None:
        raise OSError("disk full")

    hub = ProviderHub(
        {"ollama": Counting("ollama", RuntimeFault(FaultCode.PROVIDER_FAILED, "x"))},
        {"ollama"},
        broken,
    )
    assert fault_of(lambda: hub.complete("ollama", req())).code is FaultCode.PROVIDER_FAILED
    assert hub.alerts_lost == 1


# ---- Factory / config ----------------------------------------------------------


def cfg(tmp_path: Path, **extra: Any):  # type: ignore[no-untyped-def]
    return parse_config(
        {
            "data_dir": str(tmp_path / "data"),
            "workspace_dir": str(tmp_path / "work"),
            "model": {"url": "http://127.0.0.1:11434", "name": "m"},
            **extra,
        }
    )


def test_claude_is_off_unless_the_owner_enables_it(tmp_path: Path) -> None:
    claude_t = FakeTransport(ok(CLAUDE_OK))
    secrets = MemoryBackend({"anthropic_api_key": KEY})
    off = build_hub(cfg(tmp_path), secrets, Recorder(), FakeTransport(), claude_t)
    assert fault_of(lambda: off.complete("claude", req())).code is FaultCode.PROVIDER_DISABLED
    assert claude_t.requests == []

    on = build_hub(cfg(tmp_path, claude_enabled=True), secrets, Recorder(), None, claude_t)
    assert on.complete("claude", req()).text == "hello world"
    assert claude_t.requests[0].headers["x-api-key"] == KEY


def test_enabled_claude_with_no_stored_key_stops_instead_of_using_ollama(tmp_path: Path) -> None:
    ollama_t = FakeTransport(ok(OLLAMA_OK))
    hub = build_hub(
        cfg(tmp_path, claude_enabled=True), MemoryBackend({}), Recorder(), ollama_t, FakeTransport()
    )
    assert fault_of(lambda: hub.complete("claude", req())).code is FaultCode.SECRET_MISSING
    assert ollama_t.requests == []


# ---- The real urllib transport, against a throwaway loopback server -------------


class _Handler(BaseHTTPRequestHandler):
    seen: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        _Handler.seen.append(self.rfile.read(length))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
            return
        data = json.dumps(OLLAMA_OK).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server():  # type: ignore[no-untyped-def]
    _Handler.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_real_transport_posts_ignores_proxy_env_and_refuses_redirects(
    server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # nothing listens there
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    provider = OllamaProvider(f"http://127.0.0.1:{server}")
    response = provider.complete(req())
    assert response.usage.known and json.loads(_Handler.seen[0])["model"] == "m1"

    send = urllib_transport(use_proxy_env=False)
    redirected = send(HttpRequest(f"http://127.0.0.1:{server}/redirect", {}, b"{}", 5))
    assert redirected.status == 302  # not followed
