"""Phase 8 provider gating, privacy and persistent budget tests."""

import hashlib
import uuid

import pytest

from jangus.integrations.advice.broker import AdviceBroker
from jangus.integrations.advice.contracts import AdviceFault, AdvicePolicy, Evidence
from jangus.integrations.advice.ledger import ConsultationLedger
from jangus.integrations.advice.providers import ManualCodexReview


def evidence():
    text = "synthetic redacted failure"
    return Evidence(
        reference="synthetic:1",
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        sharing="external-ok",
        redacted=True,
    )


@pytest.fixture
def suite(tmp_path):
    session = str(uuid.uuid4())
    root = tmp_path / "private"
    policy = AdvicePolicy(
        codex_enabled=True,
        astra_enabled=True,
        max_calls=2,
        max_estimated_tokens=12000,
        max_reserved_microdollars=300000,
    )
    db = ConsultationLedger(root, session, policy)
    try:
        yield db, root, session, policy
    finally:
        db.close()


def consult(db, **kwargs):
    args = dict(
        provider="codex",
        evidence=[evidence()],
        allowed_action_ids={"copy_1"},
        local_recovery_exhausted=True,
        owner_approved_call=True,
        authorize=lambda grant: grant.session_id == db.session_id,
        transport=ManualCodexReview('{"summary":"Review copy","action_ids":["copy_1"]}'),
    )
    args.update(kwargs)
    return AdviceBroker(db).consult(**args)


def test_disabled_by_default_no_transport(tmp_path):
    db = ConsultationLedger(tmp_path / "private", str(uuid.uuid4()), AdvicePolicy())
    try:
        calls = []
        with pytest.raises(AdviceFault, match="provider_disabled"):
            consult(db, transport=lambda *_: calls.append(1))
        assert not calls and db.usage()["calls"] == 0
    finally:
        db.close()


def test_unapproved_local_and_non_redacted_never_leave(suite):
    db, *_ = suite
    with pytest.raises(AdviceFault, match="consultation_not_authorized"):
        consult(db, owner_approved_call=False)
    with pytest.raises(AdviceFault, match="consultation_not_authorized"):
        consult(db, local_recovery_exhausted=False)
    for note in (
        evidence().model_copy(update={"sharing": "local"}),
        evidence().model_copy(update={"redacted": False}),
    ):
        with pytest.raises(AdviceFault, match="disclosure_denied"):
            consult(db, evidence=[note])
    with pytest.raises(AdviceFault, match="consultation_not_authorized"):
        consult(db, authorize=lambda grant: False)
    assert db.usage()["calls"] == 0


def test_invalid_response_charges_calls_and_does_not_expand_tools(suite):
    db, *_ = suite
    with pytest.raises(AdviceFault, match="advice_out_of_scope"):
        consult(db, transport=ManualCodexReview('{"summary":"no","action_ids":["shell_exec"]}'))
    with pytest.raises(AdviceFault, match="invalid_advice"):
        consult(db, transport=ManualCodexReview('{"summary":"x","action_ids":[],"tools":[]}'))
    with pytest.raises(AdviceFault, match="consultation_budget_exhausted"):
        consult(db)
    assert db.usage()["calls"] == 2
    audit = "\n".join(db.db.iterdump())
    assert "synthetic redacted failure" not in audit


def test_restart_keeps_budget_and_policy(suite):
    db, root, session, policy = suite
    assert consult(db).action_ids == ["copy_1"]
    reopened = ConsultationLedger(root, session, policy)
    try:
        assert reopened.usage()["calls"] == 1
        assert consult(reopened).action_ids == ["copy_1"]
        with pytest.raises(AdviceFault, match="consultation_budget_exhausted"):
            consult(reopened)
    finally:
        reopened.close()
    with pytest.raises(AdviceFault, match="policy_revision_changed"):
        ConsultationLedger(root, session, AdvicePolicy(max_calls=5))


def test_approval_is_exact_content_and_scope_bound(suite):
    db, *_ = suite
    known = []
    def authorize(grant):
        known.append(grant)
        return grant.evidence_sha256 == evidence().sha256

    # The packet digest is of canonical bound evidence, not just note text.
    with pytest.raises(AdviceFault, match="consultation_not_authorized"):
        consult(db, authorize=authorize)
    assert len(known) == 1
    assert len(known[0].action_scope_sha256) == 64
    assert db.usage()["calls"] == 0


def test_approved_reviewer_can_refuse_changed_evidence_scope(suite):
    db, *_ = suite
    baseline = []
    with pytest.raises(AdviceFault, match="consultation_not_authorized"):
        consult(db, authorize=lambda grant: baseline.append(grant) or False)
    approved = baseline[0]
    assert consult(db, authorize=lambda grant: grant == approved).action_ids == ["copy_1"]
    with pytest.raises(AdviceFault, match="consultation_not_authorized"):
        consult(db, evidence=[
            evidence().model_copy(update={"text": "altered", "sha256":
                hashlib.sha256(b"altered").hexdigest()})
        ], authorize=lambda grant: grant == approved)
    with pytest.raises(AdviceFault, match="consultation_not_authorized"):
        consult(db, allowed_action_ids={"copy_1", "other"},
                authorize=lambda grant: grant == approved)
    assert db.usage()["calls"] == 1


def test_astra_requires_matching_persisted_failed_or_unresolved_codex(suite):
    db, *_ = suite
    with pytest.raises(AdviceFault, match="fallback_not_authorized"):
        consult(db, provider="astra", codex_unresolved=True)
    assert db.usage()["calls"] == 0

    # Model provided no usable action: explicitly unresolved advisory.
    assert consult(db, transport=ManualCodexReview(
        '{"summary":"No safe action","action_ids":[]}'
    )).action_ids == []
    assert db.has_unresolved_codex(
        db.db.execute("SELECT evidence_digest FROM events").fetchone()[0]
    )
    with pytest.raises(AdviceFault, match="fallback_not_authorized"):
        consult(db, provider="astra", codex_unresolved=False)
    with pytest.raises(AdviceFault, match="fallback_not_authorized"):
        consult(db, provider="astra", codex_unresolved=True,
                allowed_action_ids={"copy_1", "different"})
    assert consult(db, provider="astra", codex_unresolved=True).action_ids == ["copy_1"]
    assert db.usage()["calls"] == 2
    assert db.usage()["cost"] > 0


def test_codex_failed_call_charged_and_allows_explicit_fallback(suite):
    db, *_ = suite
    def failed(*args):
        raise TimeoutError("synthetic")
    with pytest.raises(TimeoutError):
        consult(db, transport=failed)
    assert db.usage()["calls"] == 1
    assert consult(db, provider="astra", codex_unresolved=True).summary == "Review copy"


def test_missing_or_replaced_consultation_ledger_blocks_budget_reset(suite):
    db, directory, session, policy = suite
    assert consult(db).action_ids == ["copy_1"]
    marker = directory / "consultation-anchor.json"
    prior = marker.read_bytes()
    assert b"generation" in prior
    marker.unlink()
    with pytest.raises(AdviceFault, match="ledger_identity_missing"):
        ConsultationLedger(directory, session, policy)
    marker.write_bytes(prior)
    assert ConsultationLedger(directory, session, policy).usage()["calls"] == 1
    marker.write_bytes(b'{"generation":0}')
    with pytest.raises(AdviceFault, match="ledger_identity_mismatch"):
        ConsultationLedger(directory, session, policy)


def test_missing_database_does_not_recreate_budget(suite):
    db, directory, session, policy = suite
    assert consult(db).action_ids == ["copy_1"]
    db.close()
    (directory / "consultations.sqlite3").unlink()
    with pytest.raises(AdviceFault, match="ledger_identity_missing"):
        ConsultationLedger(directory, session, policy)


def test_crash_ambiguous_completion_never_refunds(suite):
    db, directory, session, policy = suite
    def dying(*_args):
        raise TimeoutError("mock transport timeout")
    with pytest.raises(TimeoutError):
        consult(db, transport=dying)
    assert db.usage()["calls"] == 1
    opened = ConsultationLedger(directory, session, policy)
    try:
        assert opened.usage()["calls"] == 1
        assert opened.has_unresolved_codex(
            opened.db.execute("SELECT evidence_digest FROM events").fetchone()[0]
        )
    finally:
        opened.close()
