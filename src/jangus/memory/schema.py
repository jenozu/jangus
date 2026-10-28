"""Strict note metadata and exact-byte immutable revision identities."""

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import Field, ValidationError, field_validator
from yaml.tokens import AliasToken, AnchorToken

from jangus.config import StrictModel
from jangus.execution.files import no_links

MAX_NOTE = 32 * 1024
MAX_SCAN = 300
NOTE_KINDS = {"environment", "runbook", "tool", "lesson", "run", "preference", "index"}
SHARING = {"local", "external-ok"}


class MemoryFault(ValueError):
    """Stable diagnostic code; never include private note content."""


class NoteMetadata(StrictModel):
    id: str
    title: str = Field(min_length=1, max_length=120)
    kind: str
    topic: str = Field(min_length=1, max_length=80)
    scope: str
    provenance: str = Field(min_length=1, max_length=160)
    sharing: str
    status: str
    links: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("id")
    @classmethod
    def stable_id(cls, value: str) -> str:
        if str(uuid.UUID(value)) != value:
            raise ValueError("invalid uuid")
        return value

    @field_validator("links")
    @classmethod
    def distinct_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate links")
        for item in value:
            if str(uuid.UUID(item)) != item:
                raise ValueError("invalid link")
        return value

    @field_validator("kind")
    @classmethod
    def known_kind(cls, value: str) -> str:
        if value not in NOTE_KINDS:
            raise ValueError("unknown kind")
        return value

    @field_validator("scope")
    @classmethod
    def known_scope(cls, value: str) -> str:
        if value != "global" and not re.fullmatch(r"project:[a-z0-9][a-z0-9_-]{0,63}", value):
            raise ValueError("unknown scope")
        return value

    @field_validator("topic")
    @classmethod
    def known_topic(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", value):
            raise ValueError("unknown topic")
        return value

    @field_validator("sharing")
    @classmethod
    def known_sharing(cls, value: str) -> str:
        if value not in SHARING:
            raise ValueError("unknown sharing")
        return value

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str) -> str:
        if value not in {"draft", "active", "archived"}:
            raise ValueError("unknown note status")
        return value


@dataclass(frozen=True)
class Note:
    path: Path
    relative: str
    metadata: NoteMetadata
    body: str
    sha256: str
    raw: bytes


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if type(key) is not str or key in result:
                raise MemoryFault("note_metadata_invalid")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_note(path: Path, root: Path) -> Note:
    """Hash raw on-disk bytes. CRLF, BOM and whitespace edits change approvals."""
    root = root.resolve()
    no_links(path)
    if not path.resolve().is_relative_to(root):
        raise MemoryFault("note_outside_vault")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_NOTE + 1)
    except OSError:
        raise MemoryFault("note_unavailable") from None
    if len(raw) > MAX_NOTE:
        raise MemoryFault("note_too_large")
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n")
        if not text.startswith("---\n"):
            raise ValueError("missing frontmatter")
        closing = text.find("\n---\n", 4)
        if closing == -1:
            raise ValueError("missing closing marker")
        frontmatter = text[4:closing]
        if any(isinstance(token, (AliasToken, AnchorToken)) for token in yaml.scan(frontmatter)):
            raise ValueError("aliases not allowed")
        # UniqueLoader subclasses SafeLoader, so this is safe loading.
        metadata = yaml.load(frontmatter, Loader=UniqueLoader)  # nosec B506
        if not isinstance(metadata, dict):
            raise ValueError("metadata not mapping")
        parsed = NoteMetadata.model_validate(metadata)
        body = text[closing + 5 :]
        if not body.strip():
            raise ValueError("empty note")
    except (UnicodeError, ValueError, yaml.YAMLError, ValidationError, RecursionError):
        raise MemoryFault("note_metadata_invalid") from None
    return Note(
        path=path,
        relative=path.relative_to(root).as_posix(),
        metadata=parsed,
        body=body,
        sha256=hashlib.sha256(raw).hexdigest(),
        raw=raw,
    )


class Vault:
    def __init__(self, root: Path):
        no_links(root)
        self.root = root.resolve()
        if not self.root.is_dir():
            raise MemoryFault("vault_unavailable")

    def load(self, path: Path) -> Note:
        return parse_note(path, self.root)

    def scan(self) -> dict[str, Note]:
        """Index is optional: bounded direct scan, no symlink following or silent duplicates."""
        notes = {}
        count = 0
        for path in sorted(self.root.rglob("*.md")):
            count += 1
            if count > MAX_SCAN:
                raise MemoryFault("vault_scan_limit")
            # Reject unsafe files, even if archived; never follow linked directories.
            no_links(path)
            if not path.is_file():
                raise MemoryFault("note_unavailable")
            relative = path.relative_to(self.root)
            if relative.parts[0] in {"70_Inbox_Agent", "90_Archive", "_templates"}:
                continue
            note = self.load(path)
            if note.metadata.id in notes:
                raise MemoryFault("duplicate_note_id")
            notes[note.metadata.id] = note
        return notes
