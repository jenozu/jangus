# MEMORY.md: Jangus 2.0 project memory

Read at session start. Update at session end: add dated entries under Log, revise Current phase and Open items. Keep it short and factual. This is development memory for coding agents; it is not the runtime Obsidian memory (Phase 7) and grants no permissions.

## Task tracking
Development plan: `.taskmaster/tasks/tasks.json` (24 tasks, phases 0-7; only Phase 1 and early Phase 2 are detailed). Helper: `python3 tools/taskmaster/tasks.py status|next|show|set-status|log|add|validate`. PRD summary and decision log: `.taskmaster/docs/prd.md`. Runtime directives use the same schema (docs/DIRECTIVES.md).

## Current phase
Phase 0: specs written; ADR-001 ACCEPTED by the owner 2026-11-01; the five stacked phase-0 branches are merged into `main`. No code yet. Still open for task 1: confirm package name (default `jangus2`) and switch the GitHub default branch to `main`. Do not start Phase 1 (task 2) until the owner says so.

## Owner and environment
- Owner GitHub: jenozu. Windows 11 desktop, 32,457 MB RAM (GPU unchecked). Python 3.12.10 (`py -3.12`), Git 2.56.0, Ollama 0.40.13, installed via winget. Optional Ubuntu VPS.
- Shops/tools in scope later: Shopify, Printify (shop "tsuya"), email.
- v1 repo: jenozu/jangus. PRs #1-#16 unmerged; v1 acceptance work is paused.

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
- Choose the package name/layout (default `src/jangus2/`).
- Pick the local model after benchmarking (Phase 2).
- Set the monthly Claude spend cap and create the API key (Phase 2).
- Alert channel beyond CLI and daily report.

## Open design points
Reconcile rules per Shopify/Printify/email action; test strategy for LLM-driven agents (scripted fake models plus recorded real transcripts).

## Port list from v1 (jenozu/jangus, release/acceptance-readiness)
config.py strict models; execution/storage.py (SQLite, idempotency); execution/verification.py (hashing); remote/ledger.py (hash-chained ledger); loopback endpoint check; secret scanner (PR #13); agents/provider.py (Ollama); memory/ registry (Phase 7).

## Log
- 2026-11-01: License decision: none for now (all rights reserved by default). Owner not concerned about outside use; revisit before sharing the repo or accepting contributions.
- 2026-11-01: Owner accepted ADR-001. Merged phase-0/specs, taskmaster, cost-discipline, attempt-limits, token-economy (linear stack, fast-forward) into main.
- 2026-11-01: Added token economy + recipes specs, rewrote PHASES.md with scenario gates (2a benchmark first, 3b token-economy proof), README, tasks 28-34.
- 2026-11-01: Owner's main concern is token usage and limits. Added attempt policy, repeat detection, escalation ladder, host placement (FR27-31, B31-B36, ADR-001 items 10-11), tasks 26-27.
- 2026-11-01: Owner asked to avoid orchestrator round-trips and use cheap models. Added cost-discipline design (FR23-26, B27-B30), task 25, updated DIRECTIVES/ARCHITECTURE/ADR-001.
- 2026-11-01: Added Taskmaster task list (24 tasks), .taskmaster/ PRD, tools/taskmaster/tasks.py, docs/DIRECTIVES.md; orchestrator now delegates via directives. Next up: task 1 (owner accepts ADR-001), then task 2.
- 2026-11-01: Design interview with owner; v2 specs written (v1 repo branch `rebuild/v2-orchestrator-plan`) and copied here; CLAUDE.md and MEMORY.md created. No code.
