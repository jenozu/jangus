"""Phase 8 provider gating, privacy and persistent budget tests."""

import hashlib
import json
import uuid

import pytest

from jangus.integrations.advice.broker import AdviceBroker
from jangus.integrations.advice.contracts import AdviceFault, AdvicePolicy, Evidence
from jangus.integrations.advice.ledger import ConsultationLedger
from jangus.integrations.advice.providers import AstraTextOnly, ManualCodexReview


def evidence():
    text = "synthetic redacted failure"
    return Evidence(reference="synthetic:1", text=text, sha256=hashlib.sha256(text.encode()).hexdigest(), sharing="external-ok", redacted=True)


@pytest.fixture
def suite(tmp_path):
    session = str(uuid.uuid4())
    root = tmp_path / "private"
    policy = AdvicePolicy(codex_enabled=True, astra_enabled=True, max_calls=2,
                          max_estimated_tokens=6000, max_reserved_microdollars=200000)
    db = ConsultationLedger(root, session, policy)
    try:
        yield db, root, session, policy
    finally:
        db.close()


def consult(db, **kwargs):
    args = dict(provider="codex", evidence=[evidence()], allowed_action_ids={"copy_1"},
                local_recovery_exhausted=True, owner_approved_call=True,
                transport=ManualCodexReview('{"summary":"Review copy","action_ids":["copy_1"]}'))
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
    assert db.usage()["calls"] == 0


def test_invalid_response_charges_calls_and_does_not_expand_tools(suite):
    db, *_ = suite
    with pytest.raises(AdviceFault, match="advice_out_of_scope"):
        consult(db, transport=ManualCodexReview('{"summary":"no","action_ids":["shell_exec"]}'))
    with pytest.raises(AdviceFault, match="invalid_advice"):
        consult(db, transport=ManualCodexReview('{"summary":"x","action_ids":[],"tools":[]}' ))
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


def test_astra_needs_explicit_final_fallback(suite):
    db, *_ = suite
    with pytest.raises(AdviceFault, match="fallback_not_authorized"):
        consult(db, provider="astra")
    assert db.usage()["calls"] == 0
    assert consult(db, provider="astra", codex_unresolved=True).action_ids == ["copy_1"]
    assert db.usage()["cost"] > 0
