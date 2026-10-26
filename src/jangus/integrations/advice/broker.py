"""Opt-in, non-authoritative consultation with mandatory budget reservations."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from jangus.integrations.advice.contracts import (
    AdviceFault,
    Evidence,
    Suggestion,
    pack_evidence,
)
from jangus.integrations.advice.ledger import ConsultationLedger


@dataclass(frozen=True)
class ApprovalRequest:
    """Exact immutable content and scope to authorize in a trusted local UI."""

    session_id: str
    provider: str
    evidence_sha256: str
    action_scope_sha256: str


class AdviceBroker:
    def __init__(self, ledger: ConsultationLedger):
        self.ledger = ledger

    def consult(
        self,
        *,
        provider: str,
        evidence: list[Evidence],
        allowed_action_ids: set[str],
        local_recovery_exhausted: bool,
        owner_approved_call: bool,
        authorize: Callable[[ApprovalRequest], bool] | None = None,
        transport: Callable[[str, int, int], str],
        codex_unresolved: bool = False,
    ) -> Suggestion:
        policy = self.ledger.policy
        if not owner_approved_call or not local_recovery_exhausted:
            raise AdviceFault("consultation_not_authorized")
        if provider == "codex" and not policy.codex_enabled:
            raise AdviceFault("provider_disabled")
        if provider == "astra" and not policy.astra_enabled:
            raise AdviceFault("provider_disabled")
        if provider not in {"codex", "astra"}:
            raise AdviceFault("unknown_provider")
        if not allowed_action_ids or len(allowed_action_ids) > 20:
            raise AdviceFault("invalid_scope")
        packet = pack_evidence(evidence)
        packet_digest = hashlib.sha256(packet.encode()).hexdigest()
        scope_bytes = json.dumps(sorted(allowed_action_ids), separators=(",", ":")).encode()
        scope_digest = hashlib.sha256(scope_bytes).hexdigest()
        approval = ApprovalRequest(
            session_id=self.ledger.session_id,
            provider=provider,
            evidence_sha256=packet_digest,
            action_scope_sha256=scope_digest,
        )
        if authorize is None or authorize(approval) is not True:
            raise AdviceFault("consultation_not_authorized")
        # Bind an unresolved attempt to the EXACT evidence and action scope.
        # An untrusted boolean cannot manufacture prior Codex exhaustion.
        context_digest = hashlib.sha256(packet.encode() + b"|" + scope_bytes).hexdigest()
        if provider == "astra" and (
            not codex_unresolved or not self.ledger.has_unresolved_codex(context_digest)
        ):
            raise AdviceFault("fallback_not_authorized")
        # Byte size and extra instruction/header overhead are deliberately
        # conservative estimates, not observed billed tokens.
        input_ceiling = len(packet.encode()) + 2048
        cost = (
            10 * input_ceiling + 50 * policy.max_answer_bytes
            if provider == "astra"
            else policy.max_reserved_microdollars // max(1, policy.max_calls)
        )
        event_id = self.ledger.reserve(
            provider,
            input_ceiling + policy.max_answer_bytes,
            cost,
            context_digest,
        )
        try:
            raw = transport(packet, policy.timeout_seconds, policy.max_answer_bytes)
            if type(raw) is not str or len(raw.encode()) > policy.max_answer_bytes:
                raise AdviceFault("advice_too_large")
            try:
                result = Suggestion.model_validate(json.loads(raw))
            except (ValueError, TypeError, ValidationError, RecursionError):
                raise AdviceFault("invalid_advice") from None
            if not set(result.action_ids) <= allowed_action_ids:
                raise AdviceFault("advice_out_of_scope")
            disposition = (
                "unresolved" if provider == "codex" and not result.action_ids else "advisory"
            )
            self.ledger.finish(event_id, hashlib.sha256(raw.encode()).hexdigest(), disposition)
            return result
        except BaseException:
            self.ledger.finish(event_id, None, "failed")
            raise
