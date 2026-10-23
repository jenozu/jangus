# ADR-006: Deterministic execution and conservative recovery

Status: accepted; Phase 4 implementation. Refines ADR-004.

## Context

The first operational milestone needs observable side effects and restart-safe
limits before model planning exists. A small synthetic workflow provides real
filesystem and SQLite boundaries without granting a model generic execution.

## Decision

Implement one local Python controller in `src/jangus/execution/`. Group its
contracts, storage, files, verification, and diagnostics in this package instead
of creating separate empty subsystem trees. Compile an owner-approved manifest
to immutable copy/report actions. Record intent and charged attempts in the same
SQLite transaction before each effect. Use exclusive output creation and verify
actual bytes against the original manifest; tool claims are not completion.

Use an OS-held executor lock and transactional cancellation requests. On restart,
reconcile recorded intents: adopt matching output, retry only proven absence
within original budgets, and wait on ambiguous content. Cancelled uncertainty
remains visible. Plan revision changes and configuration/permission changes are
rejected in this slice, rather than implementing an unverified replanning path.

Diagnostic exports use structured allowlists and omit all raw content and paths.
A verification receipt is the permitted exported artifact. Full private backups
use SQLite's backup API; they are not diagnostic exports. Retention only prunes
terminal records without unresolved effects and never deletes task files.

## Alternatives and consequences

Blind retries or replacing outputs could duplicate uncertain effects. Temporary
output renames alone would not make filesystem and database writes one atomic
transaction. A general shell/agent loop would expand the authority and failure
surface before Phase 5. The chosen implementation claims reconciliation, not
general exactly-once execution.

Workspaces must be private, local, and free from concurrent external mutation.
Checks reject symlinks, junctions, special files, hard links, unsafe names, and
changed handles; they are not a hostile-user OS sandbox. File work is bounded,
so cancellation is cooperative at action boundaries, without pretending to undo
completed effects. Host power-loss durability remains subject to filesystem and
hardware semantics; acceptance covers process interruption on Windows/Ubuntu.

## Verification implications

A01–A08 require real file/SQLite results, independent output corruption checks,
actual process termination at three boundaries, retained retries/deadlines,
concurrent cancellation, unavailable persistence, and privacy canaries. Verify
installed wheels and both OS matrices. Model, vault, and VPS gates remain later.

See [execution workflow](../../development/EXECUTION.md) and
[Phase 4 evidence](../../reviews/PHASE_4.md).
