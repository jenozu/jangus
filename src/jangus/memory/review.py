"""Human review authentication separate from note metadata and agent output.

Interactive local operator flow only; not a network identity provider or OS sandbox.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

from jangus.config import _checkout_roots
from jangus.execution.files import no_links
from jangus.memory.schema import MemoryFault

ITERATIONS = 600_000


class ReviewAuthority:
    """One locally enrolled reviewer with a separately checked passphrase."""

    def __init__(self, directory: Path):
        no_links(directory)
        self.directory = directory.expanduser().resolve()
        if any(
            self.directory.is_relative_to(repo) or repo.is_relative_to(self.directory)
            for repo in _checkout_roots()
        ):
            raise MemoryFault("review_inside_checkout")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        no_links(self.directory)
        self.path = self.directory / "reviewer-identity.json"
        no_links(self.path)

    def initialize(self, reviewer: str, password: str) -> None:
        if (
            not re.fullmatch(r"[a-zA-Z0-9._@-]{2,80}", reviewer)
            or len(password) < 16
            or len(password.encode()) > 1024
        ):
            raise MemoryFault("reviewer_invalid")
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
        raw = json.dumps(
            {
                "version": 1,
                "reviewer": reviewer,
                "iterations": ITERATIONS,
                "salt": salt.hex(),
                "verifier": digest.hex(),
            },
            sort_keys=True,
        ).encode()
        try:
            with self.path.open("xb") as stream:
                if os.name != "nt":
                    self.path.chmod(0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise MemoryFault("reviewer_already_enrolled") from None
        except OSError:
            raise MemoryFault("reviewer_unavailable") from None

    def authenticated_callback(self, reviewer: str, password: str):
        """Verify local operator secret before constructing the trusted callback."""
        no_links(self.path)
        try:
            raw = self.path.read_bytes()
            if len(raw) > 2048:
                raise ValueError("oversized")
            data = json.loads(raw)
            if (
                set(data) != {"version", "reviewer", "iterations", "salt", "verifier"}
                or data["version"] != 1
                or data["iterations"] != ITERATIONS
                or data["reviewer"] != reviewer
                or len(password.encode()) > 1024
            ):
                raise ValueError("identity mismatch")
            salt = bytes.fromhex(data["salt"])
            verifier = bytes.fromhex(data["verifier"])
            if len(salt) != 32 or len(verifier) != 32:
                raise ValueError("invalid verifier")
            derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
            if not hmac.compare_digest(derived, verifier):
                raise ValueError("wrong passphrase")
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            raise MemoryFault("review_unauthorized") from None

        def authorize(claimed_reviewer: str, decision: str, evidence: str) -> bool:
            return (
                claimed_reviewer == reviewer
                and decision in {"approved", "revoked", "retired"}
                and bool(re.fullmatch(r"[a-zA-Z0-9._:/#-]{2,160}", evidence))
            )

        return authorize
