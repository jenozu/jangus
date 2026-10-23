"""Versioned, bounded, exact-identity protocol for the private remote worker."""

import hashlib
import json
import uuid
from typing import Literal

from pydantic import Field, field_validator, model_validator

from jangus.config import StrictModel

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 4096
MAX_SYNTHETIC_BYTES = 2048
STATES = frozenset({"queued", "intent", "waiting", "succeeded", "failed", "cancelled"})


class RemoteFault(Exception):
    """Stable codes only, never raw exception strings or private input."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def valid_id(value: str) -> str:
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("non-canonical")
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_uuid") from None
    return value


def unique_json(raw: bytes) -> dict:
    if len(raw) > MAX_REQUEST_BYTES:
        raise RemoteFault("request_too_large")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    try:
        result = json.loads(raw, object_pairs_hook=unique)
        if type(result) is not dict:
            raise ValueError("expected object")
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise RemoteFault("invalid_json") from None
    return result


class JobSpec(StrictModel):
    version: Literal[1]
    job_id: str
    idempotency_key: str
    kind: Literal["synthetic.hash"]
    text: str = Field(min_length=1, max_length=MAX_SYNTHETIC_BYTES)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _ids = field_validator("job_id", "idempotency_key")(valid_id)

    @model_validator(mode="after")
    def check_hash(self):
        if len(self.text.encode("utf-8")) > MAX_SYNTHETIC_BYTES:
            raise ValueError("text too large")
        if sha256(self.payload_bytes()) != self.payload_sha256:
            raise ValueError("mismatched payload hash")
        return self

    def payload_bytes(self) -> bytes:
        return canonical({"version": self.version, "kind": self.kind, "text": self.text})


def make_spec(text: str, job_id: str, idempotency_key: str) -> JobSpec:
    payload = canonical({"version": 1, "kind": "synthetic.hash", "text": text})
    return JobSpec(
        version=1,
        job_id=job_id,
        idempotency_key=idempotency_key,
        kind="synthetic.hash",
        text=text,
        payload_sha256=sha256(payload),
    )


def verify_receipt(status: dict, spec: JobSpec) -> None:
    """Reject fabricated, mutated, stale, or cross-job status/result data."""
    if (
        type(status) is not dict
        or status.get("version") != PROTOCOL_VERSION
        or status.get("job_id") != spec.job_id
        or status.get("idempotency_key") != spec.idempotency_key
        or status.get("payload_sha256") != spec.payload_sha256
        or status.get("kind") != spec.kind
        or status.get("state") not in STATES
        or type(status.get("attempts")) is not int
        or status["attempts"] < 0
        or type(status.get("cancel_requested")) is not bool
    ):
        raise RemoteFault("receipt_mismatch")
    result = status.get("result")
    value = status.get("result_sha256")
    if result is None:
        if value is not None or status["state"] == "succeeded":
            raise RemoteFault("receipt_mismatch")
    else:
        if type(result) is not dict or not isinstance(value, str):
            raise RemoteFault("receipt_mismatch")
        if sha256(canonical(result)) != value:
            raise RemoteFault("receipt_mismatch")
        if (
            result.get("version") != PROTOCOL_VERSION
            or result.get("job_id") != spec.job_id
            or result.get("payload_sha256") != spec.payload_sha256
            or result.get("content_sha256") != sha256(spec.text.encode())
            or result.get("bytes") != len(spec.text.encode())
        ):
            raise RemoteFault("receipt_mismatch")
