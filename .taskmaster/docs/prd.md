# Jangus 2.0

Full product spec: [docs/PRD.md](../../docs/PRD.md). This file is the short drift-check version used by the task-driven build. If the two disagree, fix both and log it below.

## Goal
Build an orchestrator that starts specialist agents to do the owner's day-to-day work (file/backup upkeep, shop monitoring, inbox triage) without intervention, where a deterministic kernel is the only thing allowed to act. Done means: the file/backup agent runs on a schedule for 7 days with zero manual fixes, the orchestrator delegates to it using a task-list directive, and every action is tiered, approved where required, and in a tamper-evident audit log.

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
- Approved-spec loader; model providers (Ollama, Claude); propose-then-execute loop; fresh runs.
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
src/jangus2/kernel/       <- registry, policy, executor, ledger, state, approvals, secrets
src/jangus2/directives/   <- directive/task models, status rules, validation
src/jangus2/agents/       <- spec loader, approval registry, providers, run loop
src/jangus2/orchestrator/ <- orchestrator tools (agent.start, approval.request, spec.draft, report.write)
src/jangus2/tools/        <- tool packs (files, later shop, mail)
src/jangus2/cli.py        <- jangus status/tick/approve/...
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
`uv run pytest` (unit, crash-boundary, adversarial tool-call, canary-secret tests), `uv run ruff check`, `uv run mypy --strict src/jangus2/kernel`. Fake-model transcripts for agent loops; real-model checks are run by the owner and recorded as separate evidence.

## Architecture and stack
Python 3.12, uv, pydantic strict, SQLite WAL (synchronous=FULL), ruff, mypy, pytest. Ollama and Claude API behind one provider interface. Windows Credential Manager for secrets. Rejected: letting agents call tools directly; persistent agents; extending v1 in place. Details in [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) and ADR-001.

## Risks and open questions
Prompt injection via email/orders (structural defense: tiers, spec-scoped tools); local model tool-use quality unproven; per-action reconcile rules for Shopify/Printify/email; alert channel beyond CLI; license undecided; package name default `jangus2`.

## Decision log
- 2026-11-01: Kernel is sole actor; tiers T1/T2/T3; hybrid models; PC first; fresh runs; spec approval by hash; 7-day gate before business agents (owner, design interview). Affects tasks 3-27.
- 2026-11-01: Added directives: the orchestrator delegates work as Taskmaster-style task lists; the kernel owns task status. Owner request. Adds task 8 and 18; changes orchestrator `agent.start` to carry a directive.
