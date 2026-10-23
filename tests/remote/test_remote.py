"""Phase 7 synthetic remote jobs: durable intent, SSH-tunnel contract, and recovery."""

import threading
import uuid
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from pydantic import ValidationError

from jangus.remote.client import ClientJournal, RemoteClient
from jangus.remote.ledger import MAX_ATTEMPTS, JobLedger
from jangus.remote.protocol import (
    JobSpec,
    RemoteFault,
    canonical,
    make_spec,
    sha256,
    unique_json,
    verify_receipt,
)
from jangus.remote.service import create_server, handler_for

TOKEN = "synthetic-test-token-length-more-than-thirty-two"


@pytest.fixture
def ledger(tmp_path):
    store = JobLedger(tmp_path / "server")
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def transport(ledger):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(ledger, TOKEN))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def desktop(tmp_path):
    outbox = ClientJournal(tmp_path / "desktop")
    try:
        yield outbox
    finally:
        outbox.close()


def job(text="a bounded synthetic report"):
    return make_spec(text, str(uuid.uuid4()), str(uuid.uuid4()))


def test_protocol_identity_digest_bounds_and_unknown_kind():
    spec = job()
    assert spec.payload_sha256 == sha256(spec.payload_bytes())
    with pytest.raises(ValidationError):
        JobSpec.model_validate(spec.model_dump() | {"kind": "shell.exec"})
    with pytest.raises(ValidationError):
        JobSpec.model_validate(spec.model_dump() | {"text": "changed"})
    with pytest.raises(ValidationError):
        JobSpec.model_validate(spec.model_dump() | {"unknown": "unexpected"})
    with pytest.raises(ValidationError):
        job("X" * 2049)
    with pytest.raises(RemoteFault, match="invalid_json"):
        unique_json(b'{"version":1,"version":2}')
    with pytest.raises(RemoteFault, match="request_too_large"):
        unique_json(b"x" * 4097)


def test_server_idempotency_and_conflicting_payloads(ledger):
    spec = job()
    first = ledger.submit(spec)
    assert first["state"] == "queued"
    assert ledger.submit(spec) == first
    with pytest.raises(RemoteFault, match="idempotency_conflict"):
        ledger.submit(make_spec("different", spec.job_id, spec.idempotency_key))
    with pytest.raises(RemoteFault, match="idempotency_conflict"):
        ledger.submit(make_spec("same", str(uuid.uuid4()), spec.idempotency_key))
    with pytest.raises(RemoteFault, match="idempotency_conflict"):
        ledger.submit(make_spec("same", spec.job_id, str(uuid.uuid4())))
    assert ledger.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_worker_proves_result_integrity_and_one_effect(ledger):
    spec = job()
    ledger.submit(spec)
    assert ledger.process_once() == spec.job_id
    completed = ledger.status(spec.job_id)
    assert completed["state"] == "succeeded"
    assert completed["attempts"] == 1
    verify_receipt(completed, spec)
    assert ledger.process_once() is None
    assert len(list(ledger.outputs.glob("*.json"))) == 1
    artifact = ledger.outputs / (spec.job_id + ".json")
    assert sha256(artifact.read_bytes()) == completed["result_sha256"]


class SimulatedPowerLoss(BaseException):
    pass


@pytest.mark.parametrize("crash_stage,attempts", [("after_intent", 2), ("after_effect", 1)])
def test_restart_reconciles_without_overwrite(tmp_path, crash_stage, attempts):
    root = tmp_path / "server"
    ledger = JobLedger(root)
    spec = job()
    ledger.submit(spec)

    def crash(stage, _job_id):
        if stage == crash_stage:
            raise SimulatedPowerLoss

    with pytest.raises(SimulatedPowerLoss):
        ledger.process_once(hook=crash)
    prior = ledger.status(spec.job_id)
    assert prior["state"] == "intent"
    assert prior["attempts"] == 1
    artifact = ledger.outputs / (spec.job_id + ".json")
    before = artifact.read_bytes() if artifact.exists() else None
    ledger.close()
    recovered = JobLedger(root)
    try:
        assert recovered.process_once() == spec.job_id
        current = recovered.status(spec.job_id)
        assert current["state"] == "succeeded"
        assert current["attempts"] == attempts
        if before is not None:
            assert artifact.read_bytes() == before
        verify_receipt(current, spec)
        assert recovered.process_once() is None
    finally:
        recovered.close()


def test_uncertain_partial_output_blocks_retry_and_exposes_no_success(ledger):
    spec = job()
    ledger.submit(spec)

    def crash(stage, _job_id):
        if stage == "after_intent":
            raise SimulatedPowerLoss

    with pytest.raises(SimulatedPowerLoss):
        ledger.process_once(hook=crash)
    (ledger.outputs / (spec.job_id + ".json")).write_bytes(b"partial")
    ledger.process_once()
    status = ledger.status(spec.job_id)
    assert status["state"] == "waiting"
    assert status["result"] is None
    assert status["attempts"] == 1
    assert ledger.process_once() is None
    assert (ledger.outputs / (spec.job_id + ".json")).read_bytes() == b"partial"


def test_cancellation_before_effect_and_after_effect(ledger):
    queued = job()
    ledger.submit(queued)
    assert ledger.cancel(queued.job_id)["state"] == "cancelled"
    assert ledger.process_once() is None
    assert not (ledger.outputs / (queued.job_id + ".json")).exists()

    active = job("cancel after file output")
    ledger.submit(active)

    def cancel(stage, job_id):
        if stage == "after_effect":
            ledger.cancel(job_id)

    ledger.process_once(hook=cancel)
    status = ledger.status(active.job_id)
    assert status["state"] == "cancelled"
    assert status["cancel_requested"] is True
    assert status["result_sha256"] is not None
    assert status["attempts"] == 1
    verify_receipt(status, active)
    assert ledger.cancel(active.job_id)["result_sha256"] == status["result_sha256"]


def test_exhausted_attempt_budget_and_expired_deadline(ledger):
    spec = job()
    ledger.submit(spec)
    ledger.db.execute(
        "UPDATE jobs SET attempts=?,state='intent' WHERE job_id=?",
        (MAX_ATTEMPTS, spec.job_id),
    )
    ledger.process_once()
    assert ledger.status(spec.job_id)["state"] == "failed"
    assert ledger.status(spec.job_id)["attempts"] == MAX_ATTEMPTS
    next_spec = job()
    ledger.submit(next_spec)
    ledger.db.execute("UPDATE jobs SET deadline=0 WHERE job_id=?", (next_spec.job_id,))
    ledger.process_once()
    assert ledger.status(next_spec.job_id)["state"] == "failed"


def test_corrupted_result_detected_after_success(ledger):
    spec = job()
    ledger.submit(spec)
    ledger.process_once()
    (ledger.outputs / (spec.job_id + ".json")).write_bytes(b"tampered")
    with pytest.raises(RemoteFault, match="result_integrity"):
        ledger.status(spec.job_id)


def test_authenticated_loopback_http_and_no_generic_tools(ledger, transport, desktop):
    spec = desktop.prepare("synthetic-only")
    client = RemoteClient(transport, TOKEN, desktop)
    with pytest.raises(HTTPError) as exc:
        urlopen(transport + "/v1/health", timeout=2)
    assert exc.value.code == 401
    assert client.request("GET", "/v1/health")["healthy"] is True
    wrong = RemoteClient(transport, "wrong-token-" + "z" * 32, desktop)
    with pytest.raises(RemoteFault, match="server_rejected"):
        wrong.request("GET", "/v1/health")
    submitted = client.request("POST", "/v1/jobs", spec.model_dump())
    assert submitted["state"] == "queued"
    assert client.request("GET", "/v1/jobs/" + spec.job_id)["job_id"] == spec.job_id
    invalid = spec.model_dump() | {"kind": "shell.exec"}
    with pytest.raises(RemoteFault, match="server_rejected"):
        client.request("POST", "/v1/jobs", invalid)
    assert ledger.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    with pytest.raises(RemoteFault, match="tunnel_required"):
        RemoteClient("http://example.com:8765", TOKEN, desktop)
    with pytest.raises(RemoteFault, match="invalid_port"):
        create_server(ledger, TOKEN, 80)


def test_lost_ack_disconnect_reconnect_and_result_verification(ledger, transport, desktop):
    spec = desktop.prepare("synthetic stable job")
    client = RemoteClient(transport, TOKEN, desktop)
    # Server has accepted the submit, but the client never records the response.
    client.request("POST", "/v1/jobs", spec.model_dump())
    result = client.reconcile(spec.job_id)
    assert result["state"] == "queued"
    ledger.process_once()
    result = client.reconcile(spec.job_id)
    assert result["state"] == "succeeded"
    assert result["attempts"] == 1
    assert client.reconcile(spec.job_id) == result
    assert ledger.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    saved_id = desktop.get(spec.job_id)
    assert saved_id == spec


def test_lost_ack_then_submit_from_durable_outbox(ledger, transport, desktop):
    spec = desktop.prepare("another synthetic job")
    client = RemoteClient(transport, TOKEN, desktop)
    created = client.reconcile(spec.job_id)
    assert created["state"] == "queued"
    assert ledger.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_mismatched_or_fabricated_result_rejected():
    spec = job()
    result = {
        "version": 1,
        "job_id": spec.job_id,
        "payload_sha256": spec.payload_sha256,
        "content_sha256": sha256(spec.text.encode()),
        "bytes": len(spec.text.encode()),
    }
    good = {
        "version": 1,
        "job_id": spec.job_id,
        "idempotency_key": spec.idempotency_key,
        "payload_sha256": spec.payload_sha256,
        "kind": spec.kind,
        "state": "succeeded",
        "attempts": 1,
        "cancel_requested": False,
        "result": result,
        "result_sha256": sha256(canonical(result)),
    }
    verify_receipt(good, spec)
    with pytest.raises(RemoteFault, match="receipt_mismatch"):
        verify_receipt(good | {"job_id": str(uuid.uuid4())}, spec)
    with pytest.raises(RemoteFault, match="receipt_mismatch"):
        verify_receipt(good | {"result_sha256": "0" * 64}, spec)
    with pytest.raises(RemoteFault, match="receipt_mismatch"):
        verify_receipt(good | {"result": result | {"bytes": 999}}, spec)


def test_client_does_not_expose_token_in_journal(tmp_path):
    journal = ClientJournal(tmp_path / "desktop")
    try:
        spec = journal.prepare("synthetic-canary")
        saved = journal.get(spec.job_id)
        assert saved == spec
        dump = "\n".join(journal.db.iterdump())
        assert TOKEN not in dump
        assert "synthetic-canary" in dump
    finally:
        journal.close()
