# Jangus v2 product requirements

Status: ACCEPTED specification (ADR-001 accepted 2026-11-01); implementation in progress (see MEMORY.md for the current phase). Owner: repository owner. Date: 2026-11-01.
Decision record: [ADR-001](decisions/001-v2-orchestrator-and-agents.md).

## Problem and outcome

The owner wants an **orchestrator** that creates and runs **specialist agents** ("professionals" in one area each) to maintain and perform routine tasks **without intervention**, while keeping a hard, auditable limit on what any agent can do.

Outcome: routine work (file/backup upkeep, shop and order monitoring, inbox triage) happens on a schedule; the owner reviews a short report and an approval queue instead of driving each step.

## Users

Primary: the repository owner on a Windows 11 desktop (32 GB RAM) with an optional Ubuntu VPS. Coding agents are secondary users of the repository.

## Terms

- **Kernel**: deterministic Python (no AI). The only component that performs side effects.
- **Tool**: a registered, schema-validated action with a tier. Agents can only *propose* tool calls.
- **Agent spec**: an owner-approved file defining one specialist (role, tools, max tier, model, memory scope, triggers, budget, secret names).
- **Agent run**: one fresh execution of a spec for one job. No state survives except approved memory notes and the audit log.
- **Orchestrator**: an agent whose only powers are to start agent runs from approved specs, request approvals, and report.
- **Tiers**: T1 read-only or trivially reversible, runs freely. T2 reversible change, runs and is logged for later review. T3 irreversible, outbound, or money-related, needs owner approval.

## Journeys

1. A schedule fires; the orchestrator starts the backup agent; it verifies and copies files, logs everything, and the owner reads a one-screen summary.
2. The shop agent notices a failed order sync, proposes a fix (T3), and it waits in the approval queue; the owner approves from the CLI.
3. The owner says "make me a bookkeeping agent." The orchestrator drafts a spec; it is inert until the owner approves it.
4. After weeks of correct behavior the owner promotes one specific action from T3 to T2; the promotion is recorded and reversible.
5. An agent fails or hits its budget; the kernel stops it, records why, and alerts the owner. Nothing retries beyond its persisted limits.

## Requirements

| ID | Requirement | Gate |
|---|---|---|
| V2-FR01 | Tool registry: every tool declares name, version, input schema, tier, reversibility, dry-run and undo (if any), required secrets, and idempotency rule. Unknown tools fail closed | P1 |
| V2-FR02 | Policy engine maps tier to run / run-and-log / queue for approval. Agents cannot change tiers, specs, or policy | P1 |
| V2-FR03 | Append-only, hash-chained audit ledger: intent persisted before effect; result and verification recorded after; persistence failure stops new effects | P1 |
| V2-FR04 | Durable state (SQLite) with idempotency keys and crash reconciliation | P1 |
| V2-FR05 | Per-agent and global budgets: calls, tokens, wall time, and monthly spend cap. Budgets survive restarts | P2 |
| V2-FR06 | Agent spec loader with strict schema; a spec is active only if its content hash is approved by the owner | P2 |
| V2-FR07 | Model provider interface with local Ollama and Claude API; model selection is part of the spec; no silent fallback to a different or paid provider | P2 |
| V2-FR08 | Propose-then-execute loop: agent returns structured proposed tool calls; kernel validates and executes; results return to the agent as data | P2 |
| V2-FR09 | Agents run fresh per job; continuity only via approved memory and audit log | P2 |
| V2-FR10 | Scheduler entry point (`jangus tick`) driven by Windows Task Scheduler; missed ticks do not stack unbounded work | P3 |
| V2-FR11 | File/backup maintenance agent with verified copies and no silent overwrite or delete | P3 |
| V2-FR12 | Approval queue with CLI list/show/approve/reject; approval binds to the exact action payload hash and expires | P4 |
| V2-FR13 | Orchestrator starts runs from approved specs and drafts new specs for owner approval; cannot approve its own drafts | P4 |
| V2-FR14 | Failure alerting: any failed, stuck, over-budget, or unresolved run appears in `jangus status` and a daily report | P4 |
| V2-FR15 | Shop agent: read-only Shopify/Printify first; writes added per action with tiers | P5 |
| V2-FR16 | Inbox agent: read and draft only; sending is T3 | P6 |
| V2-FR17 | Secrets in Windows Credential Manager (or environment); never in config, specs, prompts, or logs; each agent receives only its named secrets | P1 |
| V2-FR18 | Tier promotion/demotion is an owner-signed, logged change, per action and per agent | P4 |
| V2-FR19 | Approved Obsidian memory retrieval (exact-hash approval, lower trust) | P7 |
| V2-FR23 | Escalation-only orchestrator: it is called only for a new owner goal, a spec-drafting request, or a halted directive that needs judgment. Task completion, run completion, scheduled standing jobs, T3 queueing and the daily report never call it | P2-P4 |
| V2-FR24 | Kernel-run tasks: a directive task may declare `runner: kernel` and execute scripted tool steps with zero model calls; model calls are used only where judgment is needed | P2 |
| V2-FR25 | Model tiering: specialists default to Haiku-class or local models, Sonnet at low effort for harder work; the orchestrator tier is used only on escalation, with a daily escalation cap; at the cap the escalation is queued for the owner, never silently raised | P2 |
| V2-FR26 | Per-role cost metering: tokens, calls and spend recorded per run and per role (kernel-run, specialist, orchestrator) and shown in the daily report | P2 |
| V2-FR27 | Attempt policy: the kernel counts failed attempts (failed/rejected call, failed verification, unparseable reply, repeated proposal, no-progress cycle). Defaults: 2 on the default model, 1 step-up, then stop and alert | P2 |
| V2-FR28 | Repeat detection: an identical (tool, args) proposal twice in one task is refused and counted | P2 |
| V2-FR29 | Hard per-task limits (model calls, proposals, wall time); limits cannot be reset by retry, restart or re-issue | P2 |
| V2-FR30 | One-step escalation ladder per spec (`escalation_model`); only the kernel climbs it; agents cannot pick models, raise limits or call the orchestrator | P2 |
| V2-FR31 | Host placement: specs declare `host` (pc, vps, actions); the kernel refuses to run an agent where required paths or secrets are unavailable. Work runs as low-cost as possible (kernel-run, local model, VPS, Haiku, Sonnet, orchestrator) | P3-P7 |
| V2-FR32 | Token ledger: every model call records role, reason, model, measured input/output/cached tokens and cost; kernel-run tasks record zero | P2 |
| V2-FR33 | Owner-maintained price table; unknown paid model refused; stale table alerts | P2 |
| V2-FR34 | Pre-call worst-case budget check against task, directive, daily, orchestrator and monthly limits; refusal counts as a failed attempt | P2 |
| V2-FR35 | Spend alerts at 50/80/100% plus big-call and runaway flags; provider blocked at 100% | P2 |
| V2-FR36 | Result cache keyed by task type, input hash, spec hash and prompt version | P2 |
| V2-FR37 | Recipes: candidate detection from repeated verified solves, owner-approved by hash, zero-token execution, fallback and stale marking ([RECIPES.md](RECIPES.md)) | P3b |
| V2-FR38 | Watchers: kernel-evaluated change checks gate runs so unchanged input costs nothing | P3b |
| V2-FR39 | Per-task `max_input_tokens`, kernel-side summarization of large outputs, and use of prompt caching and batch endpoints where measured to save | P2-P3b |
| V2-FR40 | Daily and monthly cost reports from the ledger, with monthly reconciliation against provider usage and a dedicated API key with a provider-side spend limit | P3 |
| V2-FR20 | Optional VPS execution of agents that need no local files | P7 |
| V2-FR21 | Directives: the orchestrator delegates work to specialists as Taskmaster-style task lists (see [DIRECTIVES.md](DIRECTIVES.md)); the kernel owns task status and log entries, derived from verified evidence | P2 |
| V2-FR22 | The development backlog and runtime directives share one schema and tooling (`tools/taskmaster/tasks.py`) | P2 |

## Non-functional requirements

| ID | Requirement | Evidence |
|---|---|---|
| V2-NFR01 | External content (email, orders, web, files, memory) is data and never instructions or permission | Injection test corpus per connector |
| V2-NFR02 | A compromised or confused agent can do no more than its spec's tools at or below its max tier | Policy tests with adversarial tool calls |
| V2-NFR03 | Restart or new session never resets budgets, approvals, or failure counts | Fault injection |
| V2-NFR04 | Secrets and private content never reach logs, exports, or non-permitted models | Canary tests |
| V2-NFR05 | Unattended runs are bounded: no infinite loops, no unbounded spend, no unbounded queue growth | Budget and soak tests |
| V2-NFR06 | The system works with the cloud model disabled (local-only degraded mode) for tiers T1 | Disabled-provider test |
| V2-NFR08 | Token and spend use is the primary operating constraint: normal operation spends tokens only where a local or cheap model is insufficient; all spend is capped and metered | Cost report; soak token totals |
| V2-NFR07 | Every side effect has correlated intent, outcome, verification or unresolved status | Timeline inspection |

## Success measures

Verified task completion rate; manual interventions per week (target: zero for an accepted agent); duplicate or uncertain effects (target: zero); approval-queue latency; model spend versus cap; tokens measured vs estimated. Baselines are measured on the actual machine before targets are set.

## Boundaries

Excluded until separately authorized: unrestricted shell or generated-code execution, GUI/browser automation, financial transactions without T3 approval, auto-sending messages, agents creating or modifying their own specs or policy, multi-user access, a web dashboard.

## Risks and open decisions

- Local model tool-use quality is unproven; benchmark before assigning an agent to a local model.
- Prompt injection through inbox and shop data is the main security risk; tier limits and data/instruction separation are the defense, not model judgment.
- Claude API spend: set cap before first use; define behavior at cap (stop and alert).
- Shopify/Printify/email API write semantics and idempotency vary per action; each action needs its own reconciliation rule.
- No license, by owner decision (2026-11-01); revisit before sharing the repo or accepting contributions.
- Alert channel beyond CLI/daily report is deferred.
- Savings estimates (recipes, cache, watchers) are estimates until measured against a baseline; the cost report labels them so.
