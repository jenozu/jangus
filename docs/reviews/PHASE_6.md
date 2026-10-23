# Phase 6 — reviewed Obsidian memory (provisional)

Date: 2026-10-23. Proposed branch `memory/06-obsidian`; PR base
`runtime/05-local-agents` remains an unmerged, hardware-gated draft.

## Scope implemented

- Numbered Obsidian-compatible vault template with 9 working sections plus
  `_templates/`. No live notes, secrets, account contents or vault are committed.
- Bounded and strict YAML frontmatter with canonical IDs, scope, sharing,
  provenance, status, exact-file-byte revision hashing, duplicate detection
  and safe, bounded direct scanning without a trusted search index.
- Private SQLite review ledger and revision snapshots with explicit trusted
  authorization callback; standalone external anchor to reject stale SQLite
  restores, permanent retirement, revocation, and read-only approval checks.
- Eligibility-first scope/sharing filtering of root and linked notes, bounded
  ranking, conflict-aware topic selection, pre-prompt revalidation and mandatory
  private retrieval audit. Registry unavailability fails closed.
- Deterministically identified, exclusive-create drafts in `70_Inbox_Agent/`.
  A human edit produces a conflict, not an overwrite or auto-approval.
- Synthetic A14–A17 unit/integration tests; documented exact limits and separate
  desktop release gates in [MEMORY.md](../development/MEMORY.md).
- Integrated the latest Phase 5 coordinated model roles into this branch without
  merging PRs. Added optional exact-revision memory retrieval at each local
  role boundary, sanitized per-role references and retrieval audits; failures
  cancel required-memory preflight before effects but cannot falsify verified
  post-effect controller outcomes.
- An independently passphrase-authenticated local review CLI now permits a
  human to inspect exact raw note bytes/hash and explicitly confirm an
  approve/revoke/retire action. No agent-accessible approval tool exists.
- Successful owner-authorized verified synthetic runs can now propose
  idempotent unapproved lessons. Conflicting approved topic/scope alternatives
  are withheld rather than silently picking one. Cross-component synthetic
  CI tests cover approved retrieval, edit/revoke/missing registry, reviewer
  authentication, and no-dispatch required-memory failures.

## Evidence / remaining acceptance

Phase 6 code can be reviewed and regression-tested without an Obsidian client
or LLM. Record hosted Windows/Ubuntu results in PR #6 after CI runs. The full A14–A17 workflow on the real owner's Windows desktop and
Obsidian vault, reviewer enrollment/OS trust, actual local model and complete
A01–A17 reference run remains **pending**. Synthetic end-to-end mock-role tests
are not the same as a verified live vault or actual provider. Hardware model choice,
A09–A13 and Taskmaster 12–17 remain pending from Phase 5. Because task 18
depends on task 17, Phase 6 Taskmaster tasks 18–22 remain **pending** rather
than fabricating prerequisite completion.

This phase is **not an accepted local MVP**. Approval is a trusted application
boundary, not protection from administrators controlling both database and
external anchor. Retrieval-side hash checks are not an OS atomic lock against
hostile concurrent vault modification. No cloud access, VPS worker, or phase
7 features are included, and no prior PR is merged.
