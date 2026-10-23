# Private Obsidian memory (Phase 6)

This is a **provisional synthetic implementation** on `memory/06-obsidian`.
The complete local MVP cannot be accepted until the Windows local-model gates
from Phase 5 (Taskmaster 12–17) and the real A09–A17 integration demonstration
pass. No personal vault, trusted human-review UI, or desktop Obsidian session
is connected to CI. The PR is stacked on unmerged Phase 5.

## Vault and metadata

The reviewed, synthetic [vault template](../../templates/obsidian/README.txt)
contains the agreed numbered directories, plus `_templates/`. Copy them
to a **private external** vault; do not track your real notes in Git.

Every real note is a Markdown file with bounded YAML frontmatter:

| Field | Contract |
|---|---|
| `id` | Canonical stable UUID, unique across all active scan folders |
| `title`, `topic`, `kind` | Concise descriptors; topic is a stable search/conflict key |
| `scope` | `global` or `project:<lowercase-id>` |
| `provenance` | Source/evidence description, never authorization |
| `sharing` | `local` or `external-ok` (default must be deliberately set) |
| `status` | `draft`, `active`, or `archived` |
| `links` | Explicit list of canonical UUIDs; no unreviewed wiki-link expansion |

Notes must be UTF-8, at most 32 KiB; duplicate keys, YAML anchors/aliases,
unknown fields, duplicate IDs, links outside the approved graph, or unsafe
file links stop selection. CRLF is normalized **only for parsing**. SHA-256 is
computed over the original, exact on-disk bytes. Changing line endings,
whitespace, metadata, or body always invalidates a previous approval.
Renaming a note without changing bytes preserves its stable ID; moving to
`70_Inbox_Agent/` or `90_Archive/` makes it ineligible.

The optional index provides organization only. Retrieval uses a bounded direct
scan of at most 300 note files, and fails closed on corruption/ambiguity;
absence of index does not weaken approval.

## External approval ledger

`ApprovalRegistry` stores review decisions and retrieval audit rows in private
SQLite **outside the vault and Git checkout**. Each review binds stable note ID,
raw revision SHA, review scope/sharing, reviewer identity and evidence. It
requires a trusted caller-supplied authorization callback and the caller's
expected exact SHA; frontmatter text and agent claims alone never authorize
anything. Human review integration must authenticate reviewers independently
before supplying that callback. No public `approve` CLI is included.

On approval a private, immutable-by-convention snapshot of the exact bytes
is saved under the SHA, while operational retrieval still rechecks the **live**
file. Later revocation or retirement creates a new ledger event; retirement
is a durable tombstone and is not undone by a note status change.

A separate private `approval-anchor.json` stores instance identity and a
monotonically increasing generation. All review/retrieval audit writes increment
both the SQLite generation and the anchor. Restoring an older SQLite file without
a matching current anchor disables all retrieval; missing or corrupt anchor
also disables it. A crash between DB commit and anchor write deliberately
requires manual recovery rather than silently accepting memory. Administrators
who can roll back *both* private files can defeat this software boundary;
secure system backups and OS access control remain owner's responsibility.

## Retrieval and privacy

`select` first checks current active status, exact approval, scope, sharing,
and **all** linked notes, then ranks by query; conflicting same-topic/scope
results are not silently combined. `render_for_prompt` reopens the current
files and validates selected revisions and linked eligibility again directly
before creating prompt text. The result is capped at 8 KiB. Each included
revision is audit-logged with ID, digest, requested scope, destination and
evidence reference; raw note text is not logged in SQLite. Failure to audit
prevents returning prompt content. Changes after the final recheck, or a
hostile concurrent OS actor, remain outside the software's atomicity guarantees;
callers should avoid external concurrent edits while assembling prompts.

For optional memory, missing/corrupt approvals or ambiguous vault contents
return no selections. Required-memory requests must stop or pause with a
specific error; they must not silently fall back to unapproved text.
`external` selection permits only explicitly `external-ok` notes *and links*;
Phase 6 does not enable any external provider.

## Draft lesson proposals

`propose_lesson` uses a stable UUID derived from the requested scope, summary
and evidence reference. It exclusively creates a new draft under
`70_Inbox_Agent/`, and repeated identical proposals return the same ID.
If a human has edited that file, later automated proposals report a conflict
without overwriting changes. Drafts are never searchable operational memory.
No automatic approval, vault mutation during retrieval, or authority via text.

## Run the synthetic checks

```console
uv sync --locked
uv run --locked pytest tests/memory
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Test only temporary synthetic vaults and external registry directories. The
full matrix, installed-wheel workflow, Taskmaster dependency checks, real model,
and real user-machine acceptance are separate evidence requirements.

## Outstanding gates

Taskmaster task 17 is pending, so dependent Phase 6 tasks 18–22 **remain
pending** despite provisional source and simulated checks in this stacked PR.
A14–A17 synthetic cases can be tested in CI but A09–A17 integrated desktop
acceptance, authenticated review UI, and owner signoff are not claimed.
Phase 7 is not authorized by this implementation.
