"""Approved retrieval with link eligibility and immutable pre-prompt revision checks."""

import re
from dataclasses import dataclass

from jangus.logs import get
from jangus.memory.registry import ApprovalRegistry
from jangus.memory.schema import MemoryFault, Note, Vault

_LOG = get("memory")


@dataclass(frozen=True)
class Selection:
    note_id: str
    sha256: str
    scope: str
    sharing: str


def _eligible_graph(
    note: Note,
    notes: dict[str, Note],
    registry: ApprovalRegistry,
    scope: str,
    destination: str,
    seen: set[str] | None = None,
) -> bool:
    seen = set() if seen is None else seen
    if note.metadata.id in seen:
        return True
    if len(seen) >= 32 or not registry.approved(note, scope, destination):
        return False
    seen.add(note.metadata.id)
    for linked_id in note.metadata.links:
        linked = notes.get(linked_id)
        if linked is None or not _eligible_graph(linked, notes, registry, scope, destination, seen):
            return False
    return True


def select(
    vault: Vault,
    registry: ApprovalRegistry,
    *,
    query: str,
    scope: str,
    destination: str = "local",
    limit: int = 3,
    required: bool = False,
) -> list[Selection]:
    if (
        not 1 <= limit <= 5
        or not 1 <= len(query) <= 240
        or (scope != "global" and not re.fullmatch(r"project:[a-z0-9][a-z0-9_-]{0,63}", scope))
        or destination not in {"local", "external"}
    ):
        raise MemoryFault("retrieval_invalid")
    try:
        notes = vault.scan()
        eligible = [
            note
            for note in notes.values()
            if _eligible_graph(note, notes, registry, scope, destination)
        ]
        terms = set(re.findall(r"[a-z0-9_-]+", query.casefold()))
        # Multiple independently approved revisions for one topic/scope are a
        # human-review conflict, never choose an arbitrary winning revision.
        counts = {}
        for candidate in eligible:
            key = (candidate.metadata.topic, candidate.metadata.scope)
            counts[key] = counts.get(key, 0) + 1

        def conflict_free(start: Note) -> bool:
            stack = [start]
            seen_ids = set()
            while stack:
                current = stack.pop()
                if current.metadata.id in seen_ids:
                    continue
                seen_ids.add(current.metadata.id)
                if counts.get((current.metadata.topic, current.metadata.scope)) != 1:
                    return False
                stack.extend(notes[id_] for id_ in current.metadata.links)
            return True

        scored = []
        for note in eligible:
            if not conflict_free(note):
                continue
            indexed = (note.metadata.title + " " + note.metadata.topic + " " + note.body).casefold()
            score = sum(indexed.count(term) for term in terms)
            if score:
                scored.append((score, note))
        scored.sort(key=lambda pair: (-pair[0], pair[1].metadata.id))
        chosen = []
        topics = set()
        for _score, note in scored:
            key = (note.metadata.topic, note.metadata.scope)
            if key in topics:
                continue
            topics.add(key)
            chosen.append(
                Selection(note.metadata.id, note.sha256, note.metadata.scope, note.metadata.sharing)
            )
            if len(chosen) >= limit:
                break
        if required and not chosen:
            raise MemoryFault("memory_required_unavailable")
        return chosen
    except MemoryFault as exc:
        if required:
            raise
        # Optional memory is skipped, but never silently: log the stable code only.
        _LOG.warning("memory skipped: %s", exc)
        return []


def render_for_prompt(
    vault: Vault,
    registry: ApprovalRegistry,
    selections: list[Selection],
    *,
    scope: str,
    destination: str,
    evidence: str,
    max_bytes: int = 8192,
) -> str:
    """Recheck selected bytes and the entire link graph immediately before exposure."""
    if not 0 <= max_bytes <= 8192 or len(selections) > 5:
        raise MemoryFault("retrieval_invalid")
    notes = vault.scan()
    eligible = [
        item
        for item in notes.values()
        if _eligible_graph(item, notes, registry, scope, destination)
    ]
    counts = {}
    for item in eligible:
        key = (item.metadata.topic, item.metadata.scope)
        counts[key] = counts.get(key, 0) + 1
    rendered = []
    verified = []
    seen = set()
    for choice in selections:
        note = notes.get(choice.note_id)
        if (
            note is None
            or note.sha256 != choice.sha256
            or note.metadata.scope != choice.scope
            or note.metadata.sharing != choice.sharing
            or not _eligible_graph(note, notes, registry, scope, destination)
            or counts.get((note.metadata.topic, note.metadata.scope), 0) != 1
        ):
            raise MemoryFault("memory_revision_changed")
        stack = [note]
        while stack:
            selected = stack.pop()
            if selected.metadata.id in seen:
                continue
            if (
                not registry.approved(selected, scope, destination)
                or counts.get((selected.metadata.topic, selected.metadata.scope)) != 1
            ):
                raise MemoryFault("memory_not_approved")
            seen.add(selected.metadata.id)
            verified.append(selected)
            rendered.append(
                f"[Reviewed note {selected.metadata.id} revision {selected.sha256}]\n"
                f"{selected.metadata.title}\n{selected.body}"
            )
            stack.extend(notes[link] for link in selected.metadata.links)
    result = "\n\n".join(rendered)
    if len(result.encode("utf-8")) > max_bytes:
        raise MemoryFault("retrieval_context_limit")
    for note in verified:
        refreshed = vault.load(note.path)
        if refreshed.sha256 != note.sha256:
            raise MemoryFault("memory_revision_changed")
        registry.record_selection(refreshed, scope, destination, evidence)
    return result
