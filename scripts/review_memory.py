"""Operator-only local Obsidian review CLI. Never accepts passwords in argv."""

import argparse
import getpass
import json
import sys
from pathlib import Path

from jangus.memory.registry import ApprovalRegistry
from jangus.memory.review import ReviewAuthority
from jangus.memory.schema import MemoryFault, Vault


def _password(prompt: str) -> str:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise MemoryFault("interactive_review_required")
    return getpass.getpass(prompt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--identity-dir", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("enroll")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("note_id")
    decide = sub.add_parser("decide")
    decide.add_argument("note_id")
    decide.add_argument("--sha256", required=True)
    decide.add_argument("--decision", choices=("approved", "revoked", "retired"), required=True)
    decide.add_argument("--evidence", required=True)
    args = parser.parse_args()

    try:
        authority = ReviewAuthority(args.identity_dir)
        if args.action == "enroll":
            password = _password("New review passphrase (16+ characters): ")
            repeat = _password("Repeat review passphrase: ")
            if password != repeat:
                raise MemoryFault("reviewer_invalid")
            authority.initialize(args.reviewer, password)
            print("Review identity enrolled in private state.")
            return 0

        proof = authority.authenticated_callback(
            args.reviewer, _password("Review passphrase: ")
        )
        vault = Vault(args.vault)
        registry = ApprovalRegistry(args.registry, vault)
        try:
            notes = vault.scan()
            note = notes.get(args.note_id)
            if note is None:
                raise MemoryFault("note_unavailable")
            # The authenticated human sees the actual bytes, including any
            # instructions embedded by an attacker. They are not trusted rules.
            print(note.raw.decode("utf-8"))
            print("Exact raw revision SHA-256:", note.sha256)
            previous = registry.decision(args.note_id)
            print("Prior decision:", previous["decision"] if previous else "none")
            if args.action == "inspect":
                return 0
            if args.sha256 != note.sha256:
                raise MemoryFault("note_revision_changed")
            marker = f"{args.decision} {args.note_id}"
            if input(f"To confirm, type exactly '{marker}': ").strip() != marker:
                raise MemoryFault("review_unauthorized")
            registry.review(
                note,
                expected_sha256=args.sha256,
                decision=args.decision,
                reviewer=args.reviewer,
                evidence=args.evidence,
                authorize=proof,
            )
            print(json.dumps({"decision": args.decision, "note_id": note.metadata.id,
                              "sha256": note.sha256}))
            return 0
        finally:
            registry.close()
    except (MemoryFault, OSError, UnicodeError):
        print("error: local memory review unavailable or denied", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
