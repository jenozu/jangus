# Jangus 2.0

Full product spec: [docs/PRD.md](../../docs/PRD.md). This file is the short drift-check version used by the task-driven build. If the two disagree, fix both and log it below.

## Goal
Build an orchestrator that starts specialist agents to do the owner's day-to-day work (file/backup upkeep, shop monitoring, inbox triage) without intervention, where a deterministic kernel is the only thing allowed to act. Done means: the file/backup agent runs on a schedule for 7 days with zero manual fixes, the orchestrator delegates to it using a task-list directive, and every action is tiered, approved where required, and in a tamper-evident audit log.

## Cost discipline
The orchestrator model is called only on escalation (new goal, spec drafting, halted directive under a daily cap). Completion, reports and standing jobs are kernel work. Tasks may be kernel-run with zero tokens. Specialists use Claude Haiku through the direct Anthropic API (the default cheap model since 2026-11-04), Sonnet at low effort when needed. A local Ollama model is an optional provider, off by default. See docs/DIRECTIVES.md.

## Attempt limits and placement
The kernel counts failed attempts, refuses repeated proposals, enforces hard per-task limits, and climbs a one-step ladder (2 attempts default model, 1 on the escalation model, then stop and alert). Work runs on the cheapest suitable place: kernel-run, Haiku, Sonnet low, orchestrator. A local model on the PC or VPS is an optional rung the owner may enable, not part of the default ladder. See docs/DIRECTIVES.md and docs/ARCHITECTURE.md.

## Token economy
Every call metered and pre-checked; price table; alerts at 50/80/100%; result cache; recipes compiled from repeated verified solves (owner-approved, zero tokens); watchers; monthly reconciliation with a dedicated, provider-limited API key. See docs/TOKEN_ECONOMY.md and docs/RECIPES.md.

## Non-goals
- No model ever executes anything directly, changes policy, edits a spec, approves an action, or reads a secret.
- No unrestricted shell or generated-code execution; no GUI/browser automation.
- No autonomous sending, publishing, deleting or spending without owner approval (T3).
- No persistent agents; each run is fresh.
- No web dashboard, multi-user access, or cloud-hosted runtime in the first milestones.
- No silent fallback between model providers.
- No second backlog: this task list is the only development backlog.

## Success criteria
- B01-B23 in [docs/MVP.md](../../docs/MVP.md) pass, with fake-model, real-local-model and real-Claude evidence kept separate.
- 7 consecutive unattended days of the backup agent, zero manual fixes, zero unresolved effects.
- A directive (task list) issued by the orchestrator is executed task-by-task by a specialist; the kernel, not the model, records each task's status.

## Capabilities
### Kernel
- Tool registry with tiers, schemas, verify and reconcile; policy engine; hash-chained audit ledger; durable SQLite state, budgets, approvals queue; secret resolver.
### Agent runtime
- Approved-spec loader; model providers (Claude by default, Ollama optional and off by default); propose-then-execute loop; fresh runs.
### Directives (task-list delegation)
- A directive is a Taskmaster-style task list the orchestrator hands to a specialist: ordered tasks with dependencies, test strategies and per-task budgets. The specialist works one task at a time; status changes and log entries are written by the kernel from verified evidence. See [docs/DIRECTIVES.md](../../docs/DIRECTIVES.md).
### Orchestrator
- Starts runs from approved specs, issues directives, requests approvals, drafts specs, writes reports.
### Operations
- Scheduler (`jangus tick`), approval CLI, alerting, daily report.
### Tool packs
- Files first; Shopify, Printify, email later.

## Structure
```
src/jangus/kernel/       <- registry, policy, executor, ledger, state, approvals, secrets
src/jangus/directives/   <- directive/task models, status rules, validation
src/jangus/agents/       <- spec loader, approval registry, providers, run loop
src/jangus/orchestrator/ <- orchestrator tools (agent.start, approval.request, spec.draft, report.write)
src/jangus/tools/        <- tool packs (files, later shop, mail)
src/jangus/cli.py        <- jangus status/tick/approve/...
tools/taskmaster/tasks.py <- development task helper (stdlib only)
```

## Dependency order
kernel (config, state, ledger, registry, executor) -> directives -> agents runtime -> tool packs (files) -> scheduler -> orchestrator + approvals -> shop -> inbox -> memory/VPS. No cycles; kernel never imports agents or providers.

## Phases
- Phase 0: docs, ADR, task list. Exit: owner accepts ADR-001.
- Phase 1: kernel with fake tools. Exit: B01-B07 pass; `uv run pytest` green.
- Phase 2: agent runtime and directives with a scripted fake model. Exit: B08-B12 and directive tests pass.
- Phase 3: file/backup agent on a schedule. Exit: B13-B15 incl. 7-day soak.
- Phase 4: orchestrator, approvals CLI, alerting. Exit: B16-B19, B23.
- Phases 5-7: shop, inbox, memory/VPS (broken down when Phase 3 is done).

## Test strategy
`uv run pytest` (unit, crash-boundary, adversarial tool-call, canary-secret tests), `uv run ruff check`, `uv run mypy --strict src/jangus/kernel`. Fake-model transcripts for agent loops; real-model checks are run by the owner and recorded as separate evidence.

## Architecture and stack
Python 3.12, uv, pydantic strict, SQLite WAL (synchronous=FULL), ruff, mypy, pytest. Claude API (default) and Ollama (optional, off by default) behind one provider interface. Windows Credential Manager for secrets. Rejected: letting agents call tools directly; persistent agents; extending v1 in place. Details in [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) and ADR-001.

## Risks and open questions
Prompt injection via email/orders (structural defense: tiers, spec-scoped tools); cloud-only models mean shop and email content leaves the PC for Anthropic (accepted trade-off 2026-11-04, see the decision log); local model tool-use quality unproven; per-action reconcile rules for Shopify/Printify/email; alert channel beyond CLI; license undecided; package name default `jangus`.

## Decision log
- 2026-11-01: Kernel is sole actor; tiers T1/T2/T3; hybrid models; PC first; fresh runs; spec approval by hash; 7-day gate before business agents (owner, design interview). Affects tasks 3-27.
- 2026-11-01: Added directives: the orchestrator delegates work as Taskmaster-style task lists; the kernel owns task status. Owner request. Adds task 8 and 18; changes orchestrator `agent.start` to carry a directive.
- 2026-11-01: Cost discipline: orchestrator is escalation-only; kernel-run tasks; cheap-model defaults; per-role metering; daily escalation cap. Owner request (token cost of supervision). Changes tasks 7, 15, 16, 19, 20, 22; adds task 25. Adds V2-FR23-26 and B27-B30.
- 2026-11-01: Attempt limits and placement. Kernel-counted failed attempts, repeat detection, hard per-task limits, one-step escalation ladder (2 + 1 then stop); work placed on the cheapest host/model (kernel-run, local PC, VPS, Haiku, Sonnet low, orchestrator); GitHub Actions to be evaluated later. Owner concern: token usage and infinite loops. Adds V2-FR27-31, NFR08, B31-B36; changes tasks 7, 12, 15, 16, 19, 24; adds tasks 26, 27.
- 2026-11-01: Token economy: metering, price table, pre-call check, alerts, cache, recipes (compiled solved tasks), watchers, reconciliation; PHASES.md rewritten with all scenario gates; benchmark spike moved early (Phase 2a); model-driven slice and soak added (Phase 3b). Owner priority: minimize tokens. Adds V2-FR32-40, B37-B47, tasks 28-34; changes tasks 7, 12, 13, 17, 19, 25.
- 2026-11-01: License: none for now (owner decision, no outside use expected). Revisit before sharing or accepting contributions. Affects task 1 only.
- 2026-11-02: v2 is the only design. v1 (jenozu/jangus-v1) is paused and out of scope; v1 references, the v1 port list and v1 terms removed from README, CLAUDE.md, MEMORY.md, PRD, ARCHITECTURE and RECIPES. ADR-001 and this log keep the history. Owner request. No task changes except task 27 wording.
- 2026-11-03: Deterministic dev-plan runner (owner request, DETERMINISTIC.md). Tasks gain `runner` (kernel, local, model, human) and `verify`; `tools/taskmaster/run.py` runs `kernel` tasks' verify commands from an allowlist with zero model calls and writes hand-off notes on failure. Planning tooling only: no change to PRD tiers, policy, attempt ladder or escalation. Adds tasks 37-40 (regression gates).
- 2026-11-03: Task 25 no longer depends on task 19 (scheduler, Phase 3). Phase 2b gate B30 needs a usage report function over the ledger, not the scheduler; task 19 will call that function for the daily report. Owner authorized Phase 2b. Affects task 25 only.
- 2026-11-03: Spend caps are set as percentages of the owner's plan limit (`plan_limit_usd`, `monthly_percent`, `daily_percent`), not dollar amounts, so the system stays under what the plan allots (owner request). Changes the config `Caps` model from task 28 only; no task changes.
- 2026-11-04: **Default cheap model is Claude Haiku through the direct Anthropic API; local models are no longer the default.** Owner decision: the owner's PC runs Ollama models too slowly to carry routine work. Escalation/step-up stays Sonnet at low effort and the orchestrator model is unchanged. The Ollama provider, its benchmark script and its tests stay in the repo as an **optional provider, off by default**; provider choice and `claude_enabled` remain explicit owner config and PRD rule "no silent provider fallback" is unchanged. Amends the settled decisions: hybrid local+Claude becomes Claude-direct with optional local, and the placement ladder "kernel-run, local, VPS, Haiku, Sonnet low, orchestrator" becomes **kernel-run, Haiku, Sonnet low, orchestrator**, with local on PC or VPS as an optional rung the owner may enable. Zero-token-first is unchanged: kernel-run tasks, recipes, watchers and the result cache still come before any model call, and Phase 3's backup agent still calls no model at all. **Known trade-off:** cloud-only models mean shop orders and, later, email text are sent to Anthropic; this matters most for the inbox agent (Phase 6) and must be weighed before that agent reads a real mailbox. **OpenRouter was considered and rejected for now** (parked in `.taskmaster/docs/parking-lot.md`): it needs a new provider adapter, its upstream routing/fallback would have to be disabled to satisfy the no-silent-fallback rule, third parties would see shop and email data, and none of this was verified against OpenRouter's current documentation. Affects docs PHASES (the Phase 2a benchmark gate becomes optional), ARCHITECTURE, TOKEN_ECONOMY, DIRECTIVES, BUILD_PLAN, PRD, README, COST_SETUP; ADR-002 records it. Finished tasks 13, 17 and 25 are not edited; corrective tasks 41 and 42 are added, and the wording of pending tasks 27, 30, 31 and 34 is adjusted.
