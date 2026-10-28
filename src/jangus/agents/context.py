"""Bounded context and non-authoritative durable handoffs over SQLite run state."""

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from jangus.execution.files import no_links

MAX_HANDOFF_BYTES = 32 * 1024


class ContextFailure(ValueError):
    """Safe error code for context or handoff integrity failures."""


def estimate_tokens(text: str) -> int:
    """Conservative bytes/3 approximation; never a measured tokenizer count.

    Hash- and JSON-heavy prompts tokenize at well under four bytes per token, so
    three is used until real-model benchmark data (`prompt_eval_count`) replaces it.
    """
    return (len(text.encode("utf-8")) + 2) // 3


def bounded_output(text: str, *, max_bytes: int, reference: str) -> dict:
    if not 0 <= max_bytes <= 8192 or not reference or len(reference) > 160:
        raise ContextFailure("invalid_context_budget")
    source = text.encode("utf-8")
    excerpt = source[:max_bytes].decode("utf-8", errors="ignore")
    return {
        "reference": reference,
        "excerpt": excerpt,
        "sha256": hashlib.sha256(source).hexdigest(),
        "truncated": len(source) > max_bytes,
        "estimated_tokens": estimate_tokens(excerpt),
        "estimate_only": True,
    }


def canonical_handoff(status: dict, *, skill_hashes: dict[str, str]) -> dict:
    """Read-only snapshot; never use it to restore counters or permissions."""
    return {
        "schema_version": 1,
        "run_id": status["run_id"],
        "state": status["state"],
        "revision": status["revision"],
        "rules_sha256": status["rules_sha256"],
        "config_sha256": status["config_sha256"],
        "dispatches": status["dispatches"],
        "max_dispatches": status["max_dispatches"],
        "cancel_requested": status["cancel_requested"],
        "actions": [
            {
                "id": row["id"],
                "state": row["state"],
                "attempts": row["attempts"],
                "remaining_attempts": row["remaining_attempts"],
                "error": row["error"],
                "evidence": row["evidence"],
            }
            for row in status["actions"]
        ],
        "skills": skill_hashes,
    }


class HandoffStore:
    """Private atomic summaries. Canonical run status always comes from Controller."""

    def __init__(self, directory: Path):
        self.directory = directory.absolute()
        no_links(self.directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        no_links(self.directory)

    def _path(self, run_id: str) -> Path:
        try:
            parsed = uuid.UUID(run_id)
        except (ValueError, AttributeError):
            raise ContextFailure("invalid_run_id") from None
        if str(parsed) != run_id:
            raise ContextFailure("invalid_run_id")
        return self.directory / f"{run_id}.json"

    def save(self, controller, run_id: str, skill_hashes: dict[str, str]) -> dict:
        data = canonical_handoff(controller.status(run_id), skill_hashes=skill_hashes)
        payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        if len(payload) > MAX_HANDOFF_BYTES:
            raise ContextFailure("handoff_too_large")
        path = self._path(run_id)
        no_links(path)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
                temp = Path(stream.name)
                if os.name != "nt":
                    os.chmod(temp, 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            if os.name != "nt":
                fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        return data

    def rehydrate(self, controller, run_id: str, skill_hashes: dict[str, str]) -> dict:
        path = self._path(run_id)
        no_links(path)
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_HANDOFF_BYTES:
                raise ContextFailure("handoff_too_large")
            prior = json.loads(raw)
        except (OSError, ValueError, TypeError, RecursionError):
            raise ContextFailure("handoff_unavailable") from None
        current = canonical_handoff(controller.status(run_id), skill_hashes=skill_hashes)
        for key in ("run_id", "revision", "rules_sha256", "config_sha256", "skills"):
            if prior.get(key) != current[key]:
                raise ContextFailure("handoff_revision_changed")
        # Do not trust historical counters, claimed progress, or even claimed cancellation.
        return current
