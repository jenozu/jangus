"""Durable desktop outbox, SSH-tunnel-only client, and lost-ack reconciliation."""

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from jangus.execution.files import no_links
from jangus.paths import checkout_roots
from jangus.remote.protocol import (
    JobSpec,
    RemoteFault,
    make_spec,
    unique_json,
    valid_id,
    verify_receipt,
)

MAX_RESPONSE_BYTES = 8192


class ClientJournal:
    """Private immutable submissions survive process death and network disconnection."""

    def __init__(self, directory: Path):
        no_links(directory)
        self.directory = directory.absolute()
        if any(
            self.directory.resolve().is_relative_to(checkout)
            or checkout.is_relative_to(self.directory.resolve())
            for checkout in checkout_roots()
        ):
            raise RemoteFault("state_inside_checkout")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "remote-client.sqlite3"
        no_links(self.path)
        try:
            self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise RemoteFault("unsupported_state_version")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            if version == 0:
                self.db.execute(
                    "CREATE TABLE pending (job_id TEXT PRIMARY KEY, spec TEXT NOT NULL, "
                    "created REAL NOT NULL, last_state TEXT, receipt_sha256 TEXT)"
                )
                self.db.execute("PRAGMA user_version=1")
            if os.name != "nt":
                self.path.chmod(0o600)
        except (sqlite3.Error, OSError):
            if hasattr(self, "db"):
                self.db.close()
            raise RemoteFault("client_state_unavailable") from None

    def close(self):
        self.db.close()

    def prepare(self, text: str) -> JobSpec:
        """Persist identity and exact payload before the first network request."""
        spec = make_spec(text, str(uuid.uuid4()), str(uuid.uuid4()))
        try:
            self.db.execute(
                "INSERT INTO pending (job_id,spec,created) VALUES (?,?,?)",
                (spec.job_id, spec.model_dump_json(), time.time()),
            )
        except sqlite3.Error:
            raise RemoteFault("client_state_unavailable") from None
        return spec

    def get(self, job_id: str) -> JobSpec:
        try:
            valid_id(job_id)
        except ValueError:
            raise RemoteFault("job_missing") from None
        row = self.db.execute("SELECT spec FROM pending WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise RemoteFault("job_missing")
        try:
            return JobSpec.model_validate_json(row["spec"])
        except ValueError:
            raise RemoteFault("client_state_unavailable") from None

    def has_terminal_receipt(self, job_id: str) -> bool:
        row = self.db.execute("SELECT last_state FROM pending WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise RemoteFault("job_missing")
        return row["last_state"] in {"succeeded", "failed", "cancelled"}

    def record(self, status: dict):
        """Accept verified receipts monotonically, even across client processes."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT last_state,receipt_sha256 FROM pending WHERE job_id=?",
                (status["job_id"],),
            ).fetchone()
            if row is None:
                raise RemoteFault("job_missing")
            if (
                row["last_state"] in {"succeeded", "failed", "cancelled"}
                and (
                    status["state"] != row["last_state"]
                    or status["result_sha256"] != row["receipt_sha256"]
                )
            ) or (
                row["receipt_sha256"] is not None
                and row["receipt_sha256"] != status["result_sha256"]
            ):
                raise RemoteFault("receipt_regressed")
            self.db.execute(
                "UPDATE pending SET last_state=?,receipt_sha256=? WHERE job_id=?",
                (status["state"], status["result_sha256"], status["job_id"]),
            )
            self.db.execute("COMMIT")
        except BaseException as exc:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            if isinstance(exc, sqlite3.Error):
                raise RemoteFault("client_state_unavailable") from None
            raise


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


class RemoteClient:
    """Refuse off-loopback transport; SSH tunnel provides host auth/encryption."""

    def __init__(self, base_url: str, token: str, journal: ClientJournal, *, opener=None):
        try:
            url = urlsplit(base_url)
            port = url.port
        except ValueError:
            raise RemoteFault("tunnel_required") from None
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "::1"}
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or port is None
            or not 1024 <= port <= 65535
        ):
            raise RemoteFault("tunnel_required")
        if len(token) < 32:
            raise RemoteFault("authentication_unconfigured")
        self.url = base_url.rstrip("/")
        self.token = token
        self.journal = journal
        self.opener = opener if opener is not None else build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = (
            None
            if body is None
            else json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        )
        headers = {"Authorization": "Bearer " + self.token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            self.url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=5) as reply:
                if reply.status != 200:
                    raise RemoteFault("server_rejected")
                raw = reply.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RemoteFault("response_too_large")
            return unique_json(raw)
        except HTTPError as exc:
            if exc.code == 404:
                raise RemoteFault("job_missing") from None
            raise RemoteFault("server_rejected") from None
        except (URLError, TimeoutError, OSError):
            raise RemoteFault("transport_unavailable") from None

    def reconcile(self, job_id: str) -> dict:
        """GET before POST: lost acknowledgment never generates a new job identity."""
        spec = self.journal.get(job_id)
        try:
            status = self.request("GET", "/v1/jobs/" + spec.job_id)
        except RemoteFault as exc:
            if exc.code != "job_missing":
                raise
            # A completed job that vanished is a rollback/identity incident,
            # not permission to duplicate the accepted effect on another host.
            if self.journal.has_terminal_receipt(spec.job_id):
                raise RemoteFault("receipt_regressed") from None
            status = self.request("POST", "/v1/jobs", spec.model_dump())
        verify_receipt(status, spec)
        self.journal.record(status)
        return status

    def cancel(self, job_id: str) -> dict:
        spec = self.journal.get(job_id)
        status = self.request("POST", "/v1/jobs/" + spec.job_id + "/cancel")
        verify_receipt(status, spec)
        self.journal.record(status)
        return status
