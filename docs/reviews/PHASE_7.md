# Phase 7 — private VPS worker (provisional)

Date: 2026-10-23. Branch `remote/07-vps-worker`, stacked on unmerged
`memory/06-obsidian` PR #6. The earlier Phase 5 and Phase 6 hardware and
owner-acceptance gates are still pending.

## Implemented for review

- Version 1 registered `synthetic.hash` job contract: canonical immutable
  UUID identities, idempotency key, payload hash, bounded UTF-8 input and
  authenticated loopback-only HTTP. Unknown jobs/versions are rejected.
- Private service SQLite ledger with intent-before-exclusive-file effect,
  persisted attempt/deadline/cancellation, result hash and bounded replay.
  Post-crash verification adopts matching bytes without repeating the effect;
  absent output consumes a prior attempt, and uncertain output waits.
- Desktop private SQLite outbox: identity/request saved before POST; reconcile
  queries the original ID after a lost response and resubmits only unchanged
  content when remote status explicitly says missing. Both client/server
  independently check result identities and hashes.
- Additional developer-side safeguards: schema-v2 private SQLite ledger identity anchor, persistent sanitized transition audit, fail-closed missing/changed ledger and orphan artifacts, terminal client receipt monotonicity, cancellation of uncertain output without overwrite, worker-liveness health and fatal-failure supervision, a non-deploying Ubuntu preflight verifier, strict PowerShell tunnel helper, and private operator audit CLI.
- Owner-operated optional CLI, restrictive systemd unit template, SSH host-key
  and tunnel operating instructions, registered reviewed remote-worker skill,
  and synthetic network+restart tests.

## Actual evidence

Record final CI run, commit SHA, full test counts and platform variations on
PR #7 after GitHub Actions completes. Localhost CI is not a VPS acceptance test.

## Pending and explicit limitations

Taskmaster task 22 is pending, hence Phase 7 tasks 23–26 remain **pending**
under their declared prerequisite graph even though development source has
been created and tested synthetically. A real Ubuntu VPS installation, SSH
host-key verification, private token provisioning, systemd process kill and
restart, lost client acknowledgement/disconnect, cancellation and result
artifact checks on the actual host must still be performed before task 26 or
FR14 VPS acceptance can be claimed.

Only synthetic hashing is registered. This does not implement general remote
file processing, shell/code execution, a sandbox against administrators,
remote LLM inference, or Phase 8 consultants. Schema v2 requires a reviewed migration if any v1 state exists; deleting an identity or database to bypass preflight is not permitted. A joint rollback of both private DB and identity anchor remains outside current detection. No cloud secrets, real VPS data,
service credentials, deployment or PR merges are included.
