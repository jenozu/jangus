"""Remote worker deployment and crash-safety gates; synthetic data only."""

import json
import os
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from jangus.remote.client import ClientJournal, RemoteClient
from jangus.remote.ledger import JobLedger
from jangus.remote.preflight import EXPECTED, verify_private_path, verify_unit
from jangus.remote.protocol import RemoteFault, make_spec
from jangus.remote.service import handler_for, worker_loop

TOKEN = "phase-seven-synthetic-only-credential-not-a-real-token"


def spec():
    return make_spec("synthetic-only", str(uuid.uuid4()), str(uuid.uuid4()))


def test_ledger_identity_persists_and_audit_survives_reboot(tmp_path):
    root = tmp_path / "server"
    first = JobLedger(root)
    original = spec()
    first.submit(original)
    first.process_once()
    status = first.status(original.job_id)
    prior = first.audit(original.job_id)
    assert [x["event"] for x in prior] == ["submitted", "intent", "transition"]
    assert [x["state"] for x in prior] == ["queued", "intent", "succeeded"]
    assert all(set(x) == {"sequence", "event", "state", "attempts", "recorded"} for x in prior)
    assert status["state"] == "succeeded"
    assert (root / "ledger-identity.json").is_file()
    first.close()

    second = JobLedger(root)
    try:
        assert second.status(original.job_id) == status
        assert second.audit(original.job_id) == prior
        assert second.submit(original)["state"] == "succeeded"
        assert second.audit(original.job_id) == prior
    finally:
        second.close()


@pytest.mark.parametrize("which", ["anchor", "database", "mismatch"])
def test_lost_or_changed_identity_fails_closed(tmp_path, which):
    root = tmp_path / "server"
    store = JobLedger(root)
    store.submit(spec())
    store.close()
    if which == "anchor":
        (root / "ledger-identity.json").unlink()
    elif which == "database":
        (root / "jobs.sqlite3").unlink()
    else:
        data = json.loads((root / "ledger-identity.json").read_bytes())
        data["instance"] = "0" * 64
        (root / "ledger-identity.json").write_text(json.dumps(data))
    with pytest.raises(RemoteFault, match="ledger_identity_"):
        JobLedger(root)


def test_orphaned_artifact_does_not_allow_new_database(tmp_path):
    root = tmp_path / "server"
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / (str(uuid.uuid4()) + ".json")).write_bytes(b"orphan")
    with pytest.raises(RemoteFault, match="ledger_identity_missing"):
        JobLedger(root)
    assert not (root / "jobs.sqlite3").exists()


def test_waiting_cancellation_is_terminal_without_overwriting_partial_output(tmp_path):
    store = JobLedger(tmp_path / "server")
    try:
        item = spec()
        store.submit(item)
        partial = store.outputs / (item.job_id + ".json")
        partial.write_bytes(b"possibly-partial")
        store.process_once()
        assert store.status(item.job_id)["state"] == "waiting"
        outcome = store.cancel(item.job_id)
        assert outcome["state"] == "cancelled"
        assert outcome["cancel_requested"] is True
        assert outcome["error"] == "output_uncertain"
        assert outcome["result"] is None
        assert partial.read_bytes() == b"possibly-partial"
        assert store.cancel(item.job_id) == outcome
        assert store.process_once() is None
        assert [x["event"] for x in store.audit(item.job_id)] == [
            "submitted", "transition", "cancel_requested"
        ]
    finally:
        store.close()


def test_unit_template_static_hardening():
    unit = (Path(__file__).resolve().parents[2] / "deploy/systemd/jangus-worker.service")
    body = unit.read_bytes()
    verify_unit(body)
    text = body.decode()
    for key, value in EXPECTED.items():
        assert f"{key}={value}" in text
    with pytest.raises(RemoteFault, match="unit_unsafe"):
        verify_unit(body.replace(b"IPAddressDeny=any", b"IPAddressDeny=none"))
    with pytest.raises(RemoteFault, match="unit_unsafe"):
        verify_unit(body + b"\n[Service]\nExecStart=/bin/bash\n")
    with pytest.raises(RemoteFault, match="unit_unsafe"):
        verify_unit(body + b"\n[Service]\nExecStart=/usr/bin/sudo\n")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owner and mode semantics")
def test_read_only_privilege_preflight(tmp_path):
    target = tmp_path / "worker"
    target.mkdir(mode=0o700)
    target.chmod(0o700)
    verify_private_path(target, os.getuid(), directory=True)
    target.chmod(0o755)
    with pytest.raises(RemoteFault, match="permissions_unsafe"):
        verify_private_path(target, os.getuid(), directory=True)
    target.chmod(0o700)
    with pytest.raises(RemoteFault, match="permissions_unsafe"):
        verify_private_path(target, os.getuid() + 1, directory=True)
    secret = tmp_path / "worker.env"
    secret.write_text("JANGUS_REMOTE_TOKEN=SYNTHETIC_DUMMY_NEVER_LIVE")
    secret.chmod(0o600)
    verify_private_path(secret, os.getuid(), directory=False)
    secret.chmod(0o644)
    with pytest.raises(RemoteFault, match="permissions_unsafe"):
        verify_private_path(secret, os.getuid(), directory=False)


def test_tunnel_helper_requires_verified_host_and_local_bind():
    source = (Path(__file__).resolve().parents[2] / "scripts/windows/open-remote-tunnel.ps1")
    command = source.read_text()
    assert "StrictHostKeyChecking=yes" in command
    assert "ExitOnForwardFailure=yes" in command
    assert "BatchMode=yes" in command
    assert "ClearAllForwardings=yes" in command
    assert '127.0.0.1:${LocalPort}:127.0.0.1:8765' in command


def test_actual_http_listener_and_worker_restart_reconcile_same_job(tmp_path):
    root = tmp_path / "server"
    journal = ClientJournal(tmp_path / "desktop")
    submitted = journal.prepare("durable fixture from desktop")

    def start():
        ledger = JobLedger(root)
        http = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(ledger, TOKEN))
        http.daemon_threads = True
        stop = threading.Event()
        worker = threading.Thread(target=worker_loop, args=(ledger, stop), daemon=True)
        listener = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        listener.start()
        client = RemoteClient(f"http://127.0.0.1:{http.server_port}", TOKEN, journal)
        return ledger, http, stop, worker, listener, client

    def shutdown(ledger, http, stop, worker, listener, client):
        http.shutdown()
        http.server_close()
        listener.join(timeout=3)
        stop.set()
        worker.join(timeout=3)
        ledger.close()
        assert not listener.is_alive()
        assert not worker.is_alive()

    try:
        args = start()
        try:
            initial = args[-1].reconcile(submitted.job_id)
            assert initial["job_id"] == submitted.job_id
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                receipt = args[-1].reconcile(submitted.job_id)
                if receipt["state"] == "succeeded":
                    break
                time.sleep(0.05)
            assert receipt["state"] == "succeeded"
            assert receipt["attempts"] == 1
        finally:
            shutdown(*args)
        # New process-like instances, fresh port, same durable server and client state.
        journal.close()
        journal = ClientJournal(tmp_path / "desktop")
        args = start()
        try:
            restored = args[-1].reconcile(submitted.job_id)
            assert restored == receipt
            assert args[0].process_once() is None
            assert len(list(args[0].outputs.glob("*.json"))) == 1
            assert len(args[0].audit(submitted.job_id)) == 3
        finally:
            shutdown(*args)
    finally:
        journal.close()


def test_client_terminal_receipt_cannot_regress_or_be_recreated(tmp_path):
    ledger = JobLedger(tmp_path / "server")
    journal = ClientJournal(tmp_path / "desktop")
    try:
        item = journal.prepare("synthetic immutable")
        ledger.submit(item)
        ledger.process_once()
        complete = ledger.status(item.job_id)
        journal.record(complete)
        with pytest.raises(RemoteFault, match="receipt_regressed"):
            journal.record(ledger.status(item.job_id) | {"state": "queued",
                                                        "result_sha256": None})
        client = RemoteClient("http://127.0.0.1:8765", TOKEN, journal)
        attempts = []

        def disconnected(method, path, body=None):
            attempts.append(method)
            raise RemoteFault("job_missing")

        client.request = disconnected
        with pytest.raises(RemoteFault, match="receipt_regressed"):
            client.reconcile(item.job_id)
        assert attempts == ["GET"]
        assert ledger.status(item.job_id)["state"] == "succeeded"
    finally:
        journal.close()
        ledger.close()


def test_dead_worker_health_is_not_falsely_reported(tmp_path):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    ledger = JobLedger(tmp_path / "server")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_for(ledger, TOKEN, worker_alive=lambda: False),
    )
    listener = threading.Thread(target=server.serve_forever, daemon=True)
    listener.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/health",
            headers={"Authorization": "Bearer " + TOKEN},
        )
        with pytest.raises(HTTPError) as exc:
            urlopen(request, timeout=2)
        assert exc.value.code == 503
    finally:
        server.shutdown()
        server.server_close()
        listener.join(timeout=2)
        ledger.close()


def test_unrecoverable_worker_fault_is_not_swallowed():
    class BrokenLedger:
        def process_once(self):
            raise RemoteFault("state_unavailable")

    with pytest.raises(RemoteFault, match="state_unavailable"):
        worker_loop(BrokenLedger(), threading.Event())
