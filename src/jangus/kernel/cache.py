"""Result cache keyed by content hash (B41, V2-FR36).

What is cached is a model *reply* (data), never an action. A hit replays the cached reply to
the run loop, which parses it and sends every proposal through the executor again, so policy,
tiers, budgets and verification apply exactly as for a fresh reply. A hit makes no model call
and costs nothing.

The key covers everything the reply depends on: task type, a hash of the full prompt text
(system and user), the model, the approved spec's hash and the prompt version. Change any of
them and the old entry is simply never found. Entries expire (TTL) and the table is bounded.
Only replies that parsed and ran without a failure are stored.
"""

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from jangus.kernel.state import StateStore

DEFAULT_TTL_SECONDS = 24 * 3600
MAX_ENTRIES = 1000
MAX_VALUE_CHARS = 32_000


@dataclass(frozen=True)
class CacheKey:
    task_type: str
    prompt_sha256: str
    model: str
    spec_sha256: str
    prompt_version: str

    def digest(self) -> str:
        text = "\x1f".join(
            (self.task_type, self.prompt_sha256, self.model, self.spec_sha256, self.prompt_version)
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\x1e{user}".encode()).hexdigest()


class ResultCache:
    def __init__(
        self,
        state: StateStore,
        clock: Callable[[], int] = lambda: int(time.time()),
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._state = state
        self._clock = clock
        self._ttl = ttl_seconds

    def get(self, key: CacheKey) -> str | None:
        digest, now = key.digest(), self._clock()

        def work(conn: sqlite3.Connection) -> str | None:
            row = conn.execute(
                "SELECT value, expires_at FROM result_cache WHERE key=?", (digest,)
            ).fetchone()
            if row is None:
                return None
            if row[1] <= now:
                conn.execute("DELETE FROM result_cache WHERE key=?", (digest,))
                return None
            conn.execute("UPDATE result_cache SET hits = hits + 1 WHERE key=?", (digest,))
            return str(row[0])

        return self._state.transaction(work)

    def put(self, key: CacheKey, value: str) -> bool:
        """Store a reply. Returns False (and stores nothing) if it is too large to keep."""
        if len(value) > MAX_VALUE_CHARS:
            return False
        digest, now = key.digest(), self._clock()

        def work(conn: sqlite3.Connection) -> bool:
            conn.execute(
                "INSERT INTO result_cache(key, value, created_at, expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "created_at=excluded.created_at, expires_at=excluded.expires_at",
                (digest, value, now, now + self._ttl),
            )
            conn.execute("DELETE FROM result_cache WHERE expires_at <= ?", (now,))
            conn.execute(
                "DELETE FROM result_cache WHERE key IN (SELECT key FROM result_cache "
                "ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (MAX_ENTRIES,),
            )
            return True

        return self._state.transaction(work)

    def invalidate(self, key: CacheKey) -> None:
        digest = key.digest()
        self._state.transaction(
            lambda conn: conn.execute("DELETE FROM result_cache WHERE key=?", (digest,))
        )

    def size(self) -> int:
        return int(self._state.query("SELECT count(*) FROM result_cache")[0][0])
