"""Owner approval registry for agent specs.

A spec is active only if the registry holds its exact SHA-256 under its name.
There is one active hash per name: approving an edited spec replaces the old
approval, and an edited-but-unapproved file is inactive. The registry is a JSON
file outside the checkout. Approval is an owner action (the `jangus spec
approve` command); nothing a model returns can reach this module.

Order of effects on approve: ledger event first, then the registry file
(written atomically). If the ledger cannot be written, nothing is approved.
"""

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jangus.agents.spec import LoadedSpec
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.ledger import Ledger

SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Approval:
    name: str
    sha256: str
    approved_at: str
    note: str = ""


def _bad(reason: str) -> RuntimeFault:
    return RuntimeFault(FaultCode.SPEC_REGISTRY_INVALID, reason)


class SpecApprovalRegistry:
    def __init__(
        self, path: Path, ledger: Ledger | None = None, clock: Callable[[], str] = _now
    ) -> None:
        self._path = path
        self._ledger = ledger
        self._clock = clock

    def _read(self) -> dict[str, Approval]:
        if not self._path.exists():
            return {}
        try:
            data: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise _bad("registry file cannot be read or is not JSON") from None
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise _bad("registry has an unknown schema version")
        approved = data.get("approved")
        if not isinstance(approved, dict) or set(data) != {"schema_version", "approved"}:
            raise _bad("registry shape is wrong")
        result: dict[str, Approval] = {}
        for name, entry in approved.items():
            if (
                not isinstance(entry, dict)
                or set(entry) != {"sha256", "approved_at", "note"}
                or not all(isinstance(v, str) for v in entry.values())
                or len(entry["sha256"]) != 64
            ):
                raise _bad(f"registry entry for {name!r} is malformed")
            result[name] = Approval(name, entry["sha256"], entry["approved_at"], entry["note"])
        return result

    def _write(self, approvals: dict[str, Approval]) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "approved": {
                a.name: {"sha256": a.sha256, "approved_at": a.approved_at, "note": a.note}
                for a in sorted(approvals.values(), key=lambda a: a.name)
            },
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".spec-approvals-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self._path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            raise _bad("cannot write the registry file") from None

    def entries(self) -> list[Approval]:
        return sorted(self._read().values(), key=lambda a: a.name)

    def is_approved(self, loaded: LoadedSpec) -> bool:
        current = self._read().get(loaded.spec.name)
        return current is not None and current.sha256 == loaded.sha256

    def require_approved(self, loaded: LoadedSpec) -> None:
        """Fail closed: an unapproved or edited spec cannot run (B09)."""
        if not self.is_approved(loaded):
            raise RuntimeFault(
                FaultCode.SPEC_NOT_APPROVED,
                f"spec {loaded.spec.name!r} with hash {loaded.sha256[:12]} is not approved",
            )

    def approve(self, loaded: LoadedSpec, note: str = "") -> Approval:
        approvals = self._read()
        previous = approvals.get(loaded.spec.name)
        approval = Approval(loaded.spec.name, loaded.sha256, self._clock(), note)
        if self._ledger is not None:
            self._ledger.append(
                {
                    "type": "spec_approved",
                    "name": approval.name,
                    "sha256": approval.sha256,
                    "replaces": previous.sha256 if previous else None,
                }
            )
        approvals[approval.name] = approval
        self._write(approvals)
        return approval

    def revoke(self, name: str) -> bool:
        approvals = self._read()
        previous = approvals.get(name)
        if previous is None:
            return False
        if self._ledger is not None:
            self._ledger.append({"type": "spec_revoked", "name": name, "sha256": previous.sha256})
        del approvals[name]
        self._write(approvals)
        return True
