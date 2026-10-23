"""Opt-in, non-authoritative consultation with mandatory budget reservations."""

import hashlib
import json
from collections.abc import Callable

from pydantic import ValidationError

from jangus.integrations.advice.contracts import (
    AdviceFault,
    Evidence,
    Suggestion,
    pack_evidence,
    token_estimate,
)
from jangus.integrations.advice.ledger import ConsultationLedger


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
        transport: Callable[[str, int, int], str],
        codex_unresolved: bool = False,
    ) -> Suggestion:
        policy = self.ledger.policy
        if not owner_approved_call or not local_recovery_exhausted:
            raise AdviceFault("consultation_not_authorized")
        if provider == "codex" and not policy.codex_enabled:
            raise AdviceFault("provider_disabled")
        if provider == "astra" and (not policy.astra_enabled or not codex_unresolved):
            raise AdviceFault("fallback_not_authorized")
        if provider not in {"codex", "astra"}:
            raise AdviceFault("unknown_provider")
        if not allowed_action_ids or len(allowed_action_ids) > 20:
            raise AdviceFault("invalid_scope")
        packet = pack_evidence(evidence)
        input_tokens = token_estimate(packet)
        cost = (
            10 * input_tokens + 50 * policy.max_answer_bytes
            if provider == "astra"
            else policy.max_reserved_microdollars // max(1, policy.max_calls)
        )
        event_id = self.ledger.reserve(
            provider,
            input_tokens + policy.max_answer_bytes,
            cost,
            hashlib.sha256(packet.encode()).hexdigest(),
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
            self.ledger.finish(event_id, hashlib.sha256(raw.encode()).hexdigest(), "advisory")
            return result
        except BaseException:
            self.ledger.finish(event_id, None, "failed")
            raise
