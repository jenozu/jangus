"""One-run lock: a tick that finds another tick still running does nothing (B13).

The lock is a file created exclusively (works the same on Windows and Linux). It records
when it was taken; a lock older than `stale_seconds` is treated as left behind by a crash
and is taken over, with the takeover reported to the caller.
"""

import contextlib
import os
import time
from collections.abc import Callable
from pathlib import Path


class RunLock:
    def __init__(
        self,
        path: Path,
        stale_seconds: int = 6 * 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._stale = stale_seconds
        self._clock = clock
        self._held = False
        self.took_over_stale = False

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if not self._is_stale():
                    return False
                self.took_over_stale = True
                with contextlib.suppress(FileNotFoundError):
                    self._path.unlink()
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()} {self._clock()}\n")
            self._held = True
            return True
        return False

    def _is_stale(self) -> bool:
        try:
            taken = float(self._path.read_text(encoding="utf-8").split()[1])
        except (OSError, IndexError, ValueError):
            taken = 0.0  # unreadable lock: treat as ancient
        return self._clock() - taken > self._stale

    def release(self) -> None:
        if self._held:
            with contextlib.suppress(FileNotFoundError):
                self._path.unlink()
            self._held = False

    def __enter__(self) -> "RunLock":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
