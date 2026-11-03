"""Stable fault codes for the kernel.

Codes are part of the public contract: they appear in logs, reports and alerts,
so existing values must never be renamed or reused.
"""

from enum import StrEnum


class FaultCode(StrEnum):
    CONFIG_UNREADABLE = "config_unreadable"
    CONFIG_INVALID = "config_invalid"
    ENDPOINT_NOT_LOOPBACK = "endpoint_not_loopback"
    PATH_INVALID = "path_invalid"
    STATE_UNAVAILABLE = "state_unavailable"
    STATE_SCHEMA_MISMATCH = "state_schema_mismatch"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    BUDGET_EXCEEDED = "budget_exceeded"
    LEDGER_UNAVAILABLE = "ledger_unavailable"
    LEDGER_CORRUPT = "ledger_corrupt"
    LEDGER_EVENT_INVALID = "ledger_event_invalid"
    TOOL_REGISTRATION_INVALID = "tool_registration_invalid"
    DIRECTIVE_INVALID = "directive_invalid"
    APPROVAL_INVALID = "approval_invalid"
    SECRET_DENIED = "secret_denied"  # noqa: S105 - fault code name, not a password
    SECRET_MISSING = "secret_missing"  # noqa: S105 - fault code name, not a password
    SECRET_BACKEND_UNAVAILABLE = "secret_backend_unavailable"  # noqa: S105 - fault code name, not a password
    SPEC_INVALID = "spec_invalid"
    SPEC_NOT_APPROVED = "spec_not_approved"
    SPEC_REGISTRY_INVALID = "spec_registry_invalid"
    HOST_REQUIREMENTS_UNMET = "host_requirements_unmet"
    PROVIDER_DISABLED = "provider_disabled"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_BAD_RESPONSE = "provider_bad_response"
    PRICES_INVALID = "prices_invalid"
    PRICE_MISSING = "price_missing"
    PROVIDER_BLOCKED = "provider_blocked"
    COST_EXPORT_INVALID = "cost_export_invalid"


class RuntimeFault(Exception):  # noqa: N818 - name fixed by the task spec
    """A deterministic, expected failure carrying a stable code.

    The message must never contain secrets or untrusted external content.
    """

    def __init__(self, code: FaultCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
