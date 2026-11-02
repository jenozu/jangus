"""Append-only, hash-chained audit ledger.

One JSON object per line: {seq, ts, event, prev, hash}. Each hash covers the
entry's own fields and the previous hash, so editing or reordering any entry
breaks the chain from that point on. Writes are fsynced. If the ledger cannot
be written, it blocks and the kernel must start no new effects.

Limit: the chain detects edits, not removal of the newest entries. Detecting
truncation needs an external anchor (parking lot).
"""

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jangus.errors import FaultCode, RuntimeFault

GENESIS = "0" * 64


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    ts: str
    event: dict[str, Any]
    prev: str
    hash: str


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _entry_hash(seq: int, ts: str, event: dict[str, Any], prev: str) -> str:
    return hashlib.sha256(
        _canonical({"seq": seq, "ts": ts, "event": event, "prev": prev})
    ).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _corrupt(reason: str) -> RuntimeFault:
    return RuntimeFault(FaultCode.LEDGER_CORRUPT, reason)


def _check_chain(raw: bytes) -> tuple[int, str]:
    """Return (entry count, last hash) or raise ledger_corrupt."""
    prev = GENESIS
    count = 0
    if raw and not raw.endswith(b"\n"):
        raise _corrupt("ledger ends with a partial entry")
    for number, line in enumerate(raw.splitlines(), start=1):
        try:
            data = json.loads(line)
            entry = LedgerEntry(
                seq=data["seq"],
                ts=data["ts"],
                event=data["event"],
                prev=data["prev"],
                hash=data["hash"],
            )
        except (ValueError, KeyError, TypeError):
            raise _corrupt(f"entry {number} is unreadable") from None
        if set(data) != {"seq", "ts", "event", "prev", "hash"}:
            raise _corrupt(f"entry {number} has unexpected fields")
        if entry.seq != number or entry.prev != prev:
            raise _corrupt(f"entry {number} breaks the chain")
        try:
            expected = _entry_hash(entry.seq, entry.ts, entry.event, entry.prev)
        except (TypeError, ValueError):
            raise _corrupt(f"entry {number} is unreadable") from None
        if entry.hash != expected:
            raise _corrupt(f"entry {number} hash mismatch")
        prev = entry.hash
        count = number
    return count, prev


class Ledger:
    def __init__(
        self, path: Path, count: int, last_hash: str, size: int, clock: Callable[[], str]
    ) -> None:
        self._path = path
        self._count = count
        self._last = last_hash
        self._size = size
        self._clock = clock
        self._blocked = False

    @classmethod
    def open(cls, path: Path, clock: Callable[[], str] = _now) -> "Ledger":
        try:
            raw = path.read_bytes() if path.exists() else b""
        except OSError as exc:
            raise RuntimeFault(FaultCode.LEDGER_UNAVAILABLE, f"cannot read ledger: {exc}") from exc
        count, last = _check_chain(raw)
        return cls(path, count, last, len(raw), clock)

    @property
    def effects_allowed(self) -> bool:
        """False once a write has failed; the kernel must start no new effects."""
        return not self._blocked

    def _block(self, reason: str) -> RuntimeFault:
        self._blocked = True
        return RuntimeFault(FaultCode.LEDGER_UNAVAILABLE, reason)

    def append(self, event: dict[str, Any]) -> LedgerEntry:
        if self._blocked:
            raise RuntimeFault(FaultCode.LEDGER_UNAVAILABLE, "ledger is blocked")
        seq = self._count + 1
        ts = self._clock()
        try:
            digest = _entry_hash(seq, ts, event, self._last)
            line = (
                _canonical(
                    {"seq": seq, "ts": ts, "event": event, "prev": self._last, "hash": digest}
                )
                + b"\n"
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeFault(
                FaultCode.LEDGER_EVENT_INVALID, "event is not plain JSON data"
            ) from exc
        try:
            on_disk = self._path.stat().st_size if self._path.exists() else 0
            if on_disk != self._size:
                raise self._block("ledger changed since last write (second writer?)")
            with self._path.open("ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise self._block(f"cannot write ledger: {exc}") from exc
        prev = self._last
        self._count = seq
        self._last = digest
        self._size += len(line)
        return LedgerEntry(seq, ts, event, prev, digest)

    def verify_chain(self) -> int:
        """Re-read the file and verify every link. Returns the entry count."""
        try:
            raw = self._path.read_bytes() if self._path.exists() else b""
        except OSError as exc:
            raise self._block(f"cannot read ledger: {exc}") from exc
        count, last = _check_chain(raw)
        if count != self._count or last != self._last:
            raise _corrupt("ledger does not match what this process wrote")
        return count

    def entries(self) -> list[LedgerEntry]:
        self.verify_chain()
        if not self._count:
            return []
        return [LedgerEntry(**json.loads(line)) for line in self._path.read_bytes().splitlines()]
