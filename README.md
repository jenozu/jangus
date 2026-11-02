# Jangus 2.0

**An orchestrator that starts specialist agents to do your day-to-day work, without you driving every step, and without burning tokens.**

> **Status: Phase 1 (kernel) just started.** The specifications, task list and build plan are written and accepted. The repo has only the package scaffold, tooling and CI; **none of the system's features exist yet.** Everything below describes the intended system unless marked "planned". See [Status and roadmap](#status-and-roadmap).

---

## Contents
- [What it is](#what-it-is)
- [Why it exists](#why-it-exists)
- [How it works](#how-it-works)
- [Key concepts](#key-concepts)
- [Safety model](#safety-model)
- [Keeping token use low](#keeping-token-use-low)
- [Where work runs](#where-work-runs)
- [Planned usage](#planned-usage)
- [Repository layout](#repository-layout)
- [How this repo is built (Taskmaster)](#how-this-repo-is-built-taskmaster)
- [Status and roadmap](#status-and-roadmap)
- [Documentation index](#documentation-index)
- [FAQ](#faq)
- [Relationship to Jangus v1](#relationship-to-jangus-v1)
- [License](#license)

## What it is
Jangus 2.0 is a local-first system for running routine work on its own. You define **specialist agents**, each a "professional" in one area (file and backup upkeep, shop and order monitoring, inbox triage, and so on). An **orchestrator** starts them with clear task lists, and a **deterministic kernel** (ordinary Python, no AI) is the only part allowed to actually do anything.

The first jobs planned, in build order:
1. File and backup maintenance (verified copies, no silent overwrite or delete).
2. Shop and order monitoring (Shopify, Printify), read-only first.
3. Inbox triage with drafts (sending is always owner-approved).

The core is general. Shopify, email and files are plug-in "tool packs"; adding a new area means adding a tool pack and an agent spec, not changing the core.

## Why it exists
Version 1 of Jangus was a safe file-copy controller with models limited to one-word advisory answers. It could not run open-ended routine work. Version 2 keeps v1's safety ideas (hash verification, crash-safe state, tamper-evident logs, fail-closed behavior) and adds what was missing: agents, a tool registry, scheduling, approvals, and strict cost control.

Design goals, in priority order:
1. **Safety**: a confused or manipulated model can never do more than its approved spec allows.
2. **Low token use and hard limits**: no loops, no runaway spend, expensive models only when needed.
3. **Autonomy**: routine work runs on a schedule; you review a short report and an approval queue.
4. **Auditability**: every action has a logged intent, result and verification.

## How it works

```
                 owner / schedule / watchers
                           |
                           v
   +----------------+   directive    +--------------------+
   |  Orchestrator  | -------------> |  Specialist agent  |
   | (escalation    |  (task list)   | (cheap or local    |
   |  only, capped) |                |  model, or none)   |
   +----------------+                +---------+----------+
            ^                                  | proposes tool calls
            | escalate only                    v
            |                         +--------------------+
            +------------------------ |       KERNEL       |
                                      | validate -> tier   |
                                      | policy -> execute  |
                                      | -> verify -> log   |
                                      +----+----------+----+
                                           |          |
                              approval queue (T3)   audit ledger,
                              (you approve)         state, budgets
```

1. A **trigger** (schedule, watcher, or you) creates a job from an approved **directive**.
2. The kernel loads the approved agent spec and checks budgets.
3. The agent (if the task needs judgment at all) returns **proposed tool calls** as structured data.
4. The kernel validates each proposal against the tool registry, the agent's spec, the tier policy and budgets, then executes it, **verifies the result independently**, and logs intent and outcome.
5. Task status is set by the kernel from verified evidence. The model saying "done" changes nothing.
6. The run ends with a kernel-written report. **No model is called to confirm completion.**

## Key concepts

**Kernel.** Deterministic Python. The only component that performs side effects. Owns the tool registry, policy, SQLite state, hash-chained audit ledger, budgets, approvals queue and secret resolution. Contains no model-calling code.

**Tools and tiers.** Every tool declares its input schema, tier, reversibility, how to verify its result, and how to reconcile after a crash.

| Tier | Policy | Examples |
|---|---|---|
| T1 | Runs freely, logged | list, read, hash, shop read |
| T2 | Runs, logged, shown in the daily review | copy to a new path, create a draft, write a report |
| T3 | **Queued for your approval**, bound to the exact payload | delete, overwrite, send email, publish, refund, spend |

You can promote a specific action from T3 to T2 for a specific agent; the promotion is owner-signed, logged and reversible.

**Agent spec.** A YAML file defining one specialist: role, allowed tools, max tier, model (and an optional single step-up model), budgets, attempt limits, schedule, secrets it may use, and where it runs. A spec is **inert until you approve its SHA-256 hash**; editing it deactivates it. Agents run **fresh per job**; nothing carries over except approved memory notes and facts derived from the ledger.

**Orchestrator.** An ordinary agent spec with four tools only: `agent.start`, `approval.request`, `spec.draft`, `report.write`. It cannot approve anything, change tiers, edit active specs, read secrets or use domain tools. It is **escalation-only**: called for a new goal from you, a request to draft a new agent, or a halted directive marked for escalation, under a daily cap. It is never called to confirm completion or write reports.

**Directives.** The orchestrator (or you) hands a specialist a Taskmaster-style task list: goal, non-goals, budget, and ordered tasks with dependencies, allowed tools and a test strategy. The specialist works one task at a time. A directive can only narrow what the spec allows, never grant more. See [docs/DIRECTIVES.md](docs/DIRECTIVES.md).

**Attempt limits and the escalation ladder.** The kernel counts failed attempts (failed or rejected call, failed verification, unparseable reply, repeated proposal, no-progress cycle). A task gets 2 attempts on its default model and one step-up on a stronger model named in the spec, then it stops and alerts you. Identical proposals are refused. Limits are never reset by retrying, restarting or re-issuing. Only the kernel can climb the ladder; an agent cannot choose a model, raise a limit, or call the orchestrator.

**Recipes.** When a model solves the same kind of task the same way several times, the kernel drafts a **recipe**: a script of registered tool calls with parameters, guards and verification. After you approve it, that task runs with zero tokens. If a recipe ever fails, it falls back to the model once and is marked stale. See [docs/RECIPES.md](docs/RECIPES.md).

**Watchers.** Cheap kernel-side checks (new orders since a cursor, a file hash changed, a mail rule matched) decide whether anything runs at all. No change, no model call.

**Memory (later phase).** Approved Obsidian notes, used only after your approval, treated as lower-trust reference data.

## Safety model
- **Models think; the kernel acts.** No model output can run a command, change policy, edit a spec, approve an action or touch a secret.
- **External content is data, never instructions.** Emails, orders, web pages, files and memory notes are passed as quoted data and never parsed for commands. Defense against prompt injection is structural (tiers, spec-scoped tools, approvals), not model judgment.
- **Intent before effect.** The kernel persists intent (and charges the attempt) before any side effect, then records result and verification. If the ledger or state can't be written, new effects stop.
- **Crash-safe.** After a restart, the kernel reconciles what actually happened before retrying. Uncertain effects block retries.
- **Secrets** live in Windows Credential Manager (or environment variables), injected by the kernel into a tool call. Never in config, specs, prompts, logs or commits. Each agent gets only the secrets its spec names.
- **No silent fallback.** A failing or capped model provider means stop and alert, never switch to a different or paid provider.
- **Tamper-evident log.** The audit ledger is append-only and hash-chained.

## Keeping token use low
Token and spend use is the main operating constraint. The design spends tokens only where judgment is genuinely needed:

1. **Recipes**: solved tasks become approved zero-token scripts.
2. **Kernel-run tasks**: mechanical tasks (like backups) are scripted from the start.
3. **Watchers** and **rules** run before models; unchanged input costs nothing.
4. **Result cache** keyed by input content hash.
5. **Minimal context** per task, with kernel-side summarization of large outputs; the orchestrator sees kernel-written summaries, never raw logs.
6. **Short structured outputs** with tight limits; prompt caching and batch endpoints where measured to save.
7. **Cheapest model first**: local model, then Haiku-class, then Sonnet at low effort, then the orchestrator tier on escalation only.

And it is measured and capped:
- Every model call is recorded with role, reason, model and **measured** input, output and cached tokens, priced from an owner-maintained table.
- A pre-call budget check refuses over-budget calls before they are sent.
- Alerts at 50%, 80% and 100% of daily and monthly caps, plus flags for big calls and runaway tasks.
- Daily and monthly cost reports list orchestrator calls with reasons, step-ups, cache hits and estimated savings (labelled as estimates).
- A dedicated API key with a provider-side spend limit is the backstop, and a monthly reconciliation compares the ledger with the provider's usage.

Details: [docs/TOKEN_ECONOMY.md](docs/TOKEN_ECONOMY.md).

## Where work runs
Work is placed as low-cost as possible, in this order: kernel-run (no model) → local model on your PC (Ollama) → local model on a VPS → Haiku-class API → Sonnet at low effort → orchestrator tier. Each agent spec declares a `host` (`pc`, `vps`, or `actions`) and the kernel refuses to run it where its files or secrets aren't available.

The system runs on your Windows PC first, started by Windows Task Scheduler (`jangus tick`, planned). Agents that need no local files (for example API-only shop monitoring) can later move to a VPS. GitHub Actions is an option to evaluate later, not a default, because its machines are wiped after each run (state and ledger durability, overlapping runs, secrets handling). That evaluation needs its own decision record first.

## Planned usage
> These commands are **planned** and do not exist yet.

```powershell
jangus status                 # runs, alerts, queued approvals, spend today
jangus tick                   # run due agents (called by Task Scheduler)
jangus approvals list         # T3 actions waiting for you
jangus approvals approve <id> # approve the exact payload
jangus spec approve <file>    # activate an agent spec by hash
jangus recipe approve <file>  # activate a recipe by hash
jangus directive new          # scaffold a directive
jangus cost report            # tokens and spend per role, agent and task type
jangus cost reconcile <file>  # compare the ledger with a provider usage export
```

## Repository layout
```
CLAUDE.md                 rules for Claude Code sessions working on this repo
MEMORY.md                 project memory: decisions, open items, log (read first, update last)
README.md                 this file
agents/drafts/            inert draft agent specs (orchestrator.yaml)
docs/                     PRD, MVP scenarios, architecture, phases, directives, recipes, token economy, build plan, ADRs
.taskmaster/              development PRD summary, 34-task master list, parking lot
tools/taskmaster/         tasks.py, the stdlib-only task helper
src/jangus/              (planned) kernel, directives, agents, orchestrator, tools
tests/                    (planned)
```
Planned source layout: `kernel/` (registry, policy, executor, ledger, state, approvals, secrets, metering), `directives/`, `agents/`, `orchestrator/`, `tools/` (tool packs), `cli.py`.

## How this repo is built (Taskmaster)
The plan lives in files, not in chat, so any session on any device can pick up where the last stopped.

```bash
python3 tools/taskmaster/tasks.py status     # progress and next task
python3 tools/taskmaster/tasks.py next       # what to work on
python3 tools/taskmaster/tasks.py show 8     # task details and dependencies
python3 tools/taskmaster/tasks.py validate   # check the dependency graph
```
Rules: one task at a time; new ideas go to `.taskmaster/docs/parking-lot.md`, not into code; direction changes update the PRD decision log first, then the tasks; finished tasks are never edited (add corrective ones); each task is done only after its test strategy actually runs. The same schema is used at runtime for directives, so one habit covers both. See [CLAUDE.md](CLAUDE.md) and [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) for what Claude can do autonomously and what needs you.

## Status and roadmap

| Phase | Goal | Status |
|---|---|---|
| 0 | Specs, ADR-001, task list | Done (ADR-001 accepted) |
| 1 | Kernel with fake tools | In progress (scaffold done, task 3 next) |
| 2a | Providers and local model benchmark | Not started |
| 2b | Agent runtime, attempt policy, metering, budget gate, cache | Not started |
| 3 | File/backup agent, scheduler, 7-day soak | Not started |
| 3b | Recipes, watchers, model-driven soak | Not started |
| 4 | Orchestrator, approval CLI, alerting | Not started |
| 5 | Shop agent | Not started |
| 6 | Inbox agent | Not started |
| 7 | Memory, VPS, Actions evaluation | Not started |

Acceptance is defined by 47 scenarios (B01-B47) in [docs/MVP.md](docs/MVP.md). Results will be recorded with the distinction between fake-model, real local-model and real Claude runs, and between simulated and hardware-tested behavior.

## Documentation index
| Document | Purpose |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Product requirements (V2-FR, V2-NFR) |
| [docs/MVP.md](docs/MVP.md) | Milestones and acceptance scenarios B01-B47 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, tool registry, agent spec, run lifecycle, placement |
| [docs/DIRECTIVES.md](docs/DIRECTIVES.md) | Task-list delegation, attempts, ladder, cost rules |
| [docs/RECIPES.md](docs/RECIPES.md) | Turning solved tasks into zero-token scripts |
| [docs/TOKEN_ECONOMY.md](docs/TOKEN_ECONOMY.md) | Metering, budgets, alerts, cache, reconciliation |
| [docs/PHASES.md](docs/PHASES.md) | Phases and exit gates |
| [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) | What Claude can do vs what the owner must do |
| [docs/decisions/001-v2-orchestrator-and-agents.md](docs/decisions/001-v2-orchestrator-and-agents.md) | Core design decision (proposed) |
| [.taskmaster/docs/prd.md](.taskmaster/docs/prd.md) | Short PRD and decision log used for drift checks |

## FAQ
**Is this only for Shopify?** No. The core knows nothing about Shopify. Shopify and Printify are tool packs scheduled for Phase 5.

**Does the orchestrator check every task?** No. The kernel verifies and records everything. The orchestrator is called only on escalation, under a daily cap.

**Can an agent loop forever?** No. Every task has kernel-counted attempts, repeat detection, hard call and time limits, and a budget that survives restarts.

**What if a model is wrong or gets tricked by an email?** It can only propose actions its approved spec allows, at or below its tier. Anything irreversible, outbound or involving money waits for your approval.

**Can agents create other agents?** The orchestrator can draft a spec. It stays inert until you approve it, and the orchestrator can never approve its own drafts.

**Will it work offline?** Local-model and kernel-run work does. Cloud models are optional and capped.

## Relationship to Jangus v1
v1 lives at [github.com/jenozu/jangus-v1](https://github.com/jenozu/jangus-v1) (branch `release/acceptance-readiness`), a deterministic file-copy controller with advisory local models. v2 ports selected modules (strict config, SQLite state, hash verification, hash-chained ledger, loopback endpoint check, secret scanner, Ollama provider, memory registry) and replaces the fixed-manifest plan, the one-word model roles and the fixed five-tool set. v1 acceptance work is paused.

## License
None, by owner decision (2026-11-01). All rights reserved by default; no license grant is implied. To be revisited before the repo is shared or accepts contributions.
