"""Loopback-only authenticated HTTP service for durable, registered remote jobs."""

import argparse
import json
import os
import re
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import ValidationError

from jangus.execution.contracts import RuntimeFault
from jangus.remote.ledger import JobLedger
from jangus.remote.protocol import (
    MAX_REQUEST_BYTES,
    PROTOCOL_VERSION,
    JobSpec,
    RemoteFault,
    unique_json,
)

JOB_PATTERN = re.compile(r"/v1/jobs/([0-9a-f-]{36})(/cancel)?\Z")


def handler_for(
    ledger: JobLedger, secret: str, worker_alive: Callable[[], bool] | None = None
):
    if len(secret) < 32:
        raise RemoteFault("authentication_unconfigured")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            # Never log private input, job identifiers, or bearer credentials.
            return

        def reply(self, code: int, payload: dict):
            data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def authenticated(self) -> bool:
            expected = "Bearer " + secret
            received = self.headers.get("Authorization", "")
            if not secrets.compare_digest(received.encode(), expected.encode()):
                self.reply(401, {"error": "unauthorized"})
                return False
            return True

        def receive(self) -> dict:
            try:
                size = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                raise RemoteFault("invalid_request") from None
            if not 0 <= size <= MAX_REQUEST_BYTES:
                raise RemoteFault("request_too_large")
            if (
                self.headers.get("Transfer-Encoding")
                or self.headers.get("Content-Type") != "application/json"
            ):
                raise RemoteFault("invalid_request")
            return unique_json(self.rfile.read(size))

        def dispatch(self):
            if not self.authenticated():
                return
            try:
                path = self.path
                if self.command == "GET" and path == "/v1/health":
                    if worker_alive is not None and not worker_alive():
                        self.reply(503, {"error": "worker_unavailable"})
                        return
                    ledger.db.execute("SELECT 1").fetchone()
                    self.reply(200, {"version": PROTOCOL_VERSION, "healthy": True})
                    return
                if self.command == "POST" and path == "/v1/jobs":
                    spec = JobSpec.model_validate(self.receive())
                    self.reply(200, ledger.submit(spec))
                    return
                match = JOB_PATTERN.fullmatch(path)
                if match:
                    job_id, suffix = match.groups()
                    if self.command == "GET" and suffix is None:
                        self.reply(200, ledger.status(job_id))
                        return
                    if self.command == "POST" and suffix == "/cancel":
                        if self.headers.get("Content-Length", "0") != "0":
                            raise RemoteFault("invalid_request")
                        self.reply(200, ledger.cancel(job_id))
                        return
                self.reply(404, {"error": "route_missing"})
            except ValidationError:
                self.reply(400, {"error": "invalid_job"})
            except RemoteFault as exc:
                status = (
                    404
                    if exc.code == "job_missing"
                    else 409
                    if exc.code == "idempotency_conflict"
                    else 400
                    if exc.code in {"invalid_json", "invalid_request", "request_too_large"}
                    else 503
                )
                self.reply(status, {"error": exc.code})
            except (OSError, ValueError):
                self.reply(503, {"error": "operation_unavailable"})

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

    return Handler


def create_server(
    ledger: JobLedger,
    token: str,
    port: int = 8765,
    worker_alive: Callable[[], bool] | None = None,
) -> ThreadingHTTPServer:
    """Hard-coded loopback binding, regardless of machine or service settings."""
    if not 1024 <= port <= 65535:
        raise RemoteFault("invalid_port")
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(ledger, token, worker_alive))
    server.daemon_threads = True
    return server


def worker_loop(ledger: JobLedger, stop: threading.Event):
    while not stop.is_set():
        try:
            job = ledger.process_once()
        except RuntimeFault as exc:
            if str(exc) != "executor_busy":
                raise
            job = None
        stop.wait(0.1 if job is not None else 0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = os.environ.get("JANGUS_REMOTE_TOKEN", "")
    if len(token) < 32:
        parser.error("JANGUS_REMOTE_TOKEN must be securely configured (32+ characters)")
    ledger = JobLedger(args.state_dir)
    stop = threading.Event()
    worker = threading.Thread(target=worker_loop, args=(ledger, stop), daemon=True)
    worker_failed = threading.Event()
    watchdog = None
    try:
        server = create_server(ledger, token, args.port, worker_alive=worker.is_alive)
        worker.start()

        def supervise():
            while not stop.wait(0.5):
                if not worker.is_alive():
                    worker_failed.set()
                    server.shutdown()
                    return

        watchdog = threading.Thread(target=supervise, daemon=True)
        watchdog.start()
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            server.server_close()
    finally:
        stop.set()
        if watchdog is not None:
            watchdog.join(timeout=3)
        if worker.is_alive():
            worker.join(timeout=3)
        ledger.close()
    return 1 if worker_failed.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
