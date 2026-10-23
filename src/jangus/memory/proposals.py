"""Idempotent, human-edit-safe draft lesson proposals."""

import hashlib
import os
import re
import uuid
from dataclasses import dataclass

import yaml

from jangus.execution.files import no_links
from jangus.memory.schema import MemoryFault, Vault, parse_note


@dataclass(frozen=True)
class Proposal:
    note_id: str
    relative: str
    sha256: str
    created: bool


def propose_lesson(
    vault: Vault,
    *,
    scope: str,
    summary: str,
    evidence: str,
) -> Proposal:
    if not (
        scope == "global" or re.fullmatch(r"project:[a-z0-9][a-z0-9_-]{0,63}", scope)
    ):
        raise MemoryFault("proposal_invalid")
    if (
        not 1 <= len(summary.strip()) <= 1200
        or not re.fullmatch(r"[a-zA-Z0-9._:/#-]{2,160}", evidence)
        or "\x00" in summary
    ):
        raise MemoryFault("proposal_invalid")
    value = scope + "\n" + summary.strip() + "\n" + evidence
    note_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "jangus:lesson:" + value))
    metadata = {
        "id": note_id,
        "title": "Proposed lesson " + note_id[:8],
        "kind": "lesson",
        "topic": "lesson-" + note_id[:8],
        "scope": scope,
        "provenance": "agent-proposal:" + evidence,
        "sharing": "local",
        "status": "draft",
        "links": [],
    }
    raw = (
        "---\n"
        + yaml.safe_dump(metadata, sort_keys=True, allow_unicode=True)
        + "---\n"
        + "# Draft lesson — human review required\n\n"
        + summary.strip()
        + "\n\nEvidence: "
        + evidence
        + "\n"
    ).encode("utf-8")
    folder = vault.root / "70_Inbox_Agent"
    no_links(folder)
    if not folder.is_dir():
        raise MemoryFault("inbox_missing")
    path = folder / f"{note_id}.md"
    no_links(path)
    wanted = hashlib.sha256(raw).hexdigest()
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != wanted:
            raise MemoryFault("proposal_conflict")
        return Proposal(note_id, path.relative_to(vault.root).as_posix(), wanted, False)
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            path.chmod(0o600)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != wanted:
            raise MemoryFault("proposal_conflict") from None
        return Proposal(note_id, path.relative_to(vault.root).as_posix(), wanted, False)
    except OSError:
        raise MemoryFault("proposal_write_failed") from None
    created = parse_note(path, vault.root)
    if created.metadata.status != "draft":
        raise MemoryFault("proposal_invalid")
    return Proposal(note_id, created.relative, wanted, True)
