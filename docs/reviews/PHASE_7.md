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
remote LLM inference, or Phase 8 consultants. No cloud secrets, real VPS data,
service credentials, deployment or PR merges are included.
