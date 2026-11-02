# MEMORY.md: Jangus 2.0 project memory

Read at session start. Update at session end: add dated entries under Log, revise Current phase and Open items. Keep it short and factual. This is development memory for coding agents; it is not the runtime Obsidian memory (Phase 7) and grants no permissions.

## Task tracking
Development plan: `.taskmaster/tasks/tasks.json` (24 tasks, phases 0-7; only Phase 1 and early Phase 2 are detailed). Helper: `python3 tools/taskmaster/tasks.py status|next|show|set-status|log|add|validate`. PRD summary and decision log: `.taskmaster/docs/prd.md`. Runtime directives use the same schema (docs/DIRECTIVES.md).

## Current phase
Phase 1 (kernel) is built and merged: tasks 2-10 done, evidence in docs/reviews/PHASE_1.md (B01-B07 pass with fake tools, 120 tests, CI green on ubuntu and windows). Waiting for the owner to accept the gate and authorize Phase 2a (providers and local-model benchmark). Do not start Phase 2 without that. `tasks.py next` will suggest Phase 2 tasks; ignore them until authorized.

## Owner and environment
- Owner GitHub: jenozu. Windows 11 desktop, 32,457 MB RAM (GPU unchecked). Python 3.12.10 (`py -3.12`), Git 2.56.0, Ollama 0.40.13, installed via winget. Optional Ubuntu VPS.
- Shops/tools in scope later: Shopify, Printify (shop "tsuya"), email.
- v1 repo: jenozu/jangus-v1. PRs #1-#16 unmerged; v1 acceptance work is paused.

## Decisions (settled)
1. First jobs: shop/order monitoring, inbox triage with drafts, file/backup maintenance.
2. Deterministic kernel is the only actor; agents propose.
3. Tiers T1/T2/T3; owner can promote specific actions T3 to T2 (owner-signed, logged, reversible).
4. Hybrid models: local Ollama for routine; Claude API for orchestration/hard judgment; monthly spend cap; no silent fallback.
5. PC first with Windows Task Scheduler (`jangus tick`); VPS later.
6. Agents run fresh per job.
7. Orchestrator drafts specs; inert until owner approves.
8. Approvals via CLI first; phone notifications later; bound to payload hash and expiry.
9. Secrets in Windows Credential Manager; per-agent scoping.
10. Orchestrator delegates via directives (Taskmaster-style task lists); kernel owns task status.
11. Cost discipline: orchestrator is escalation-only with a daily cap; completion/reports/standing jobs are kernel-only; kernel-run tasks use zero tokens; specialists on Haiku-class/local, Sonnet low effort when needed; cost metered per role.
12. Attempt policy: kernel-counted failures; repeat detection; hard per-task limits; ladder 2 + 1 step-up then stop/alert; only the kernel climbs.
13. Placement: cheapest first (kernel-run, local PC, VPS local model, Haiku, Sonnet low, orchestrator); specs declare `host`; GitHub Actions to be evaluated later (state/ledger durability, secrets).
14. Token economy (docs/TOKEN_ECONOMY.md, docs/RECIPES.md): metering per call, price table, pre-call check, alerts 50/80/100%, result cache, recipes from repeated verified solves (owner-approved), watchers, monthly reconciliation, dedicated API key with provider spend limit.
15. Gate: file/backup agent must run 7 days unattended with zero manual fixes before shop or inbox agents.

## Open items (owner)
- Accept the Phase 1 gate and authorize Phase 2a.
- Optional: run the test suite once on the Windows PC.
- Pick the local model after benchmarking (Phase 2).
- Set the monthly Claude spend cap and create the API key (Phase 2).
- Alert channel beyond CLI and daily report.

## Open design points
Reconcile rules per Shopify/Printify/email action; test strategy for LLM-driven agents (scripted fake models plus recorded real transcripts).

## Port list from v1 (jenozu/jangus-v1, release/acceptance-readiness)
config.py strict models; execution/storage.py (SQLite, idempotency); execution/verification.py (hashing); remote/ledger.py (hash-chained ledger); loopback endpoint check; secret scanner (PR #13); agents/provider.py (Ollama); memory/ registry (Phase 7).

## Log
- 2026-11-02: Task 11 done: Phase 1 evidence merged; gate awaiting owner acceptance.
- 2026-11-02: Phase 1 finished in one run (owner asked for the whole phase, merge after each task). Tasks 6-10 merged as PRs #6-#10; task 11 evidence in docs/reviews/PHASE_1.md. Limits recorded there: simulated crashes only, nothing run on the owner's PC, Windows Credential Manager backend untested.
- 2026-11-02: Task 10 done: kernel/approvals.py (approvals table in the state DB, schema still v1), bound to run, tool, exact payload hash and expiry, single use; ledger entry written before the state change; executor runs an approved T3 call and queues otherwise; merged.
- 2026-11-02: Task 9 done: kernel/secrets.py (Env, Memory, Windows keyring backends), per-agent allow-list, denial before any state change, result redaction; B07 canary tests; merged. Windows Credential Manager backend is untested on a real Windows store.
- 2026-11-02: Task 7 done: directives/models.py (strict models, spec narrowing, B29/B33 checks, kernel-only status API, ledger hash at issue); merged. Attempt carry-over for superseding directives is runtime state, deferred to Phase 2.
- 2026-11-02: Task 8 done (subtasks 8.1-8.3): kernel/executor.py; state gained args_json, unresolved status, list_open_actions, writable; B05 crash tests at 5 checkpoints and B06 fail-closed tests pass; merged.
- 2026-11-02: Task 6 done: kernel/registry.py and policy.py (B01-B04 tests, fake tools in tests/fakes.py); merged.
- 2026-11-02: Owner asked to merge each task's PR after it is complete (no endless stacking). Merged #3 and #4 (merge commits). Task 5 (ledger) on phase-1/audit-ledger, based on main; merge after CI green.
- 2026-11-02: Task 4 built on phase-1/state-store (stacked on phase-1/config-and-errors, PR #3 unmerged): kernel/state.py with runs/actions/budgets, WAL+FULL, atomic intent+charge, fail-closed after a failed write. Done only after CI green. Next: task 5.
- 2026-11-02: Task 3 built on branch phase-1/config-and-errors (errors.py, config.py, tests). Config format is TOML (stdlib), not YAML. Marked done only after CI green. Next: task 4.
- 2026-11-02: Owner switched the GitHub default branch to main. Task 1 done.
- 2026-11-02: Owner said go. Task 2 (scaffold) done on branch phase-1/scaffold, PR #1; local checks pass and CI green on ubuntu and windows. Task 1 still open (GitHub default branch; dependency forced on owner authority). Next: task 3 (config and error types).
- 2026-11-02: Repos renamed by owner: v1 is now `jenozu/jangus-v1`; this repo is `jenozu/jangus`. Python package and source dir are `jangus` / `src/jangus/` (no `2`). All docs and pending tasks updated. Note: v1 also uses the package name `jangus`; keep the two in separate virtualenvs.
- 2026-11-01: License decision: none for now (all rights reserved by default). Owner not concerned about outside use; revisit before sharing the repo or accepting contributions.
- 2026-11-01: Owner accepted ADR-001. Merged phase-0/specs, taskmaster, cost-discipline, attempt-limits, token-economy (linear stack, fast-forward) into main.
- 2026-11-01: Added token economy + recipes specs, rewrote PHASES.md with scenario gates (2a benchmark first, 3b token-economy proof), README, tasks 28-34.
- 2026-11-01: Owner's main concern is token usage and limits. Added attempt policy, repeat detection, escalation ladder, host placement (FR27-31, B31-B36, ADR-001 items 10-11), tasks 26-27.
- 2026-11-01: Owner asked to avoid orchestrator round-trips and use cheap models. Added cost-discipline design (FR23-26, B27-B30), task 25, updated DIRECTIVES/ARCHITECTURE/ADR-001.
- 2026-11-01: Added Taskmaster task list (24 tasks), .taskmaster/ PRD, tools/taskmaster/tasks.py, docs/DIRECTIVES.md; orchestrator now delegates via directives. Next up: task 1 (owner accepts ADR-001), then task 2.
- 2026-11-01: Design interview with owner; v2 specs written (v1 repo branch `rebuild/v2-orchestrator-plan`) and copied here; CLAUDE.md and MEMORY.md created. No code.
