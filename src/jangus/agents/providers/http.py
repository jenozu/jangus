"""Minimal HTTP transport on the standard library (kernel stays dependency-light).

Unit tests inject a fake transport, so no test touches the network. The real
transport refuses redirects and caps the response size. Loopback providers use a
transport that ignores proxy environment variables, so a local model call can
never be routed through a proxy.
"""

import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class HttpRequest:
    url: str
    headers: dict[str, str]
    body: bytes
    timeout: float


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


Transport = Callable[[HttpRequest], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def urllib_transport(use_proxy_env: bool) -> Transport:
    handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
    if not use_proxy_env:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)

    def send(request: HttpRequest) -> HttpResponse:
        req = urllib.request.Request(  # noqa: S310 - scheme is fixed by the provider
            request.url, data=request.body, headers=request.headers, method="POST"
        )
        try:
            with opener.open(req, timeout=request.timeout) as response:
                return HttpResponse(response.status, response.read(MAX_RESPONSE_BYTES + 1))
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, b"")

    return send
