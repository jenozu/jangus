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


class RuntimeFault(Exception):  # noqa: N818 - name fixed by the task spec
    """A deterministic, expected failure carrying a stable code.

    The message must never contain secrets or untrusted external content.
    """

    def __init__(self, code: FaultCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
