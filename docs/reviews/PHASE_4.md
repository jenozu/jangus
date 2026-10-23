# Phase 4 verification record

Date: 2026-10-23. Scope: deterministic logging and reliable file execution.

## Delivered

SQLite state and transactional ordered events; strict manifest/plan contracts;
registered bounded file operations and explicit permissions; independent
verification; persistent attempt/dispatch/deadline limits; cancellation;
uncertain-effect reconciliation; disclosure-minimized exports; private snapshot
backup/restore and explicit retention maintenance. The CLI and fixture creator
exercise the complete synthetic copy/report workflow.

## Acceptance evidence

| Gate | Executed check |
|---|---|
| A01 | Copies/report match trusted hashes; intent/result/verification correlate |
| A02 | Unknown tools/arguments, traversal, symlink/junction escapes, hard links, oversized files, and output conflicts rejected |
| A03 | Corrupted output and fabricated tool success cannot complete a run |
| A04 | One transient I/O fault recovers; repeated faults exhaust persisted attempts |
| A05 | Real process termination after intent, effect, and verification; matching outputs adopted; partial output waits |
| A06 | Cancellation from a second process; no next action; completed/uncertain effects retained |
| A07 | Failed intent/result transactions, read-only SQLite, and storage-budget failures stop new effects |
| A08 | Canary secrets/raw paths excluded from exports; useful receipts, events, omissions, and checksums remain |

Local Linux: 93 Python tests passed, including 46 execution tests and the 47
configuration/CLI regressions. Ruff lint/format also passed. The isolated wheel successfully completed the synthetic workflow outside the
checkout. All six hosted Windows/Ubuntu CI jobs passed for tested commit
`3361f6f29e509938b974a78e5985bd097fbe5d39` in
[the verified workflow](https://github.com/jenozu/jangus/actions/runs/39971549421). Ubuntu ran 93 Python tests;
Windows ran 92 with one POSIX-only skip. Both Python versions, installed-wheel
workflow, and Taskmaster MCP checks passed. The final bookkeeping commit is
separate from this tested implementation commit.

Additional checks exercise migration rollback, duplicate IDs, dependency cycles,
stale revisions, changed permissions, exclusive executor ownership, deadline
retention, backup/restore of an in-flight action, and non-pruning of cancelled
uncertainty. The POSIX named-pipe case is platform-specific; Windows checks a
real directory junction instead of assuming symlink privileges.

## Limits

This accepts the deterministic execution foundation only. No local model,
owner-desktop demonstration, Obsidian memory, VPS, or consultant integration is
claimed. No new Python production dependencies were required.

Use one state directory and private local workspaces with no external concurrent
mutations. Filesystem checks are not an OS sandbox. Crash testing covers process
termination, not arbitrary physical power failure. Cancellation is cooperative
and does not reverse effects. Diagnostic exports omit raw content; private
backups contain recovery metadata and must not be shared as exports.

This phase is stacked on `foundation/03-taskmaster` while PR #3 remains open.
[Task completion evidence](../../.taskmaster/evidence/11.json) uses the tested
implementation revision. Task 12 remains pending. Taskmaster development
completion and PR merge state remain separate.
