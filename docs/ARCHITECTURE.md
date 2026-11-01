# Jangus v2 architecture

Status: DRAFT intended design. Nothing here is implemented. Authority model follows [ADR-001](decisions/001-v2-orchestrator-and-agents.md).

## Principle

Models think; the kernel acts. An agent can only return *proposed* tool calls as structured data. The kernel validates each against the tool registry, the agent's spec, the tier policy, and budgets, then executes, verifies, and logs. No model, note, email, or order text can grant a capability.

## Components

| Component | Responsibility | Does not own |
|---|---|---|
| Kernel | Tool registry, policy, execution, verification, ledger, budgets, approvals queue | Reasoning |
| Tool packs | Registered tools per domain (files, Shopify, Printify, email) | Policy decisions |
| Agent runtime | Load approved spec, build prompt, call model, parse proposals, loop under budget | Executing anything |
| Agent specs | Declarative definition of a specialist | Self-modification |
| Orchestrator | A privileged *agent spec* with tools `agent.start`, `approval.request`, `spec.draft`, `report.write` | Approving, tier changes |
| Scheduler | `jangus tick` finds due agents, starts runs, coalesces missed ticks | Run logic |
| Approval queue | Holds T3 actions; CLI list/show/approve/reject | Auto-approval |
| Model providers | Ollama (loopback) and Claude API behind one interface | Fallback decisions |
| Memory (later) | Approved Obsidian notes, lower-trust data | Authority |
| Remote worker (later) | Runs agents needing no local files | Local files |

One Python application plus SQLite to start. One local inference at a time. The orchestrator is not a special code path with extra power; it is an agent whose spec lists only four tools.

## Tool registry

Each tool declares: `name`, `version`, input schema (strict), `tier` (T1/T2/T3), `reversible` (bool), `dry_run` and `undo` (optional callables), `secrets` (names), `idempotency` rule, `verify` (how the kernel independently checks the result), and `reconcile` (how to establish what happened after a crash). A tool without `verify` and `reconcile` may not be T2 or T3.

Tier meaning:

| Tier | Policy | Examples |
|---|---|---|
| T1 | Run freely, logged | list, read, hash, shop read, inbox read |
| T2 | Run, logged, appears in daily review | copy to new path, create draft, write report |
| T3 | Queued until owner approves the exact payload | delete, overwrite, send email, publish product, refund, spend |

Effective tier = max(tool tier, any elevation by a rule such as "target outside workspace"). An agent's spec sets `max_tier`; a proposal above it is rejected, not queued.

## Agent spec (YAML)

```yaml
schema_version: 1
name: backup-maintainer
role: "You maintain verified backups of the folders listed in your task."
model: {provider: ollama, name: <exact tag>, max_tokens: 1024}
tools: [file.list, file.read, file.hash, file.copy_new, report.write]
max_tier: T2
secrets: []
memory_scope: [env.backups]
triggers: [{cron: "0 2 * * *"}]
budget: {calls: 40, tokens: 60000, wall_seconds: 900, spend_usd: 0}
escalation_model: {provider: claude, name: <haiku-or-sonnet tag>, effort: low}   # optional single step-up
attempts: {default_model: 2, step_up: 1, max_model_calls: 6, max_proposals: 12}
host: pc          # pc | vps | actions; the kernel refuses to run where required paths/secrets are unavailable
```

A spec is active only if its SHA-256 appears in the owner's approval registry. Editing it deactivates it. The orchestrator can write a draft to a `drafts/` directory; drafts are inert.

## Directives

The orchestrator hands specialists a **directive**, a Taskmaster-style task list ([DIRECTIVES.md](DIRECTIVES.md)), instead of a free-form instruction. `agent.start` carries the directive. The kernel validates it against the assignee's approved spec (tools subset, tier ceiling), records its hash, shows the agent one task at a time, and sets task status only from verified evidence. The development backlog in `.taskmaster/` uses the same schema.

## Model tiers and cost

Supervision tokens are the main avoidable cost, so the design keeps expensive models off the happy path:

| Role | Model | When called |
|---|---|---|
| Kernel | none | Always: status, verification, reports, scheduling, approvals queue |
| Specialist, `runner: kernel` task | none | Scripted steps, zero tokens |
| Specialist, `runner: model` task | Haiku-class or local; Sonnet at low effort for harder work | One task at a time, minimal context |
| Orchestrator | Highest tier used | Only on escalation: new owner goal, spec drafting, halted directive with `escalate: true`, under a daily cap |

The orchestrator is never called to confirm completion, to read a finished run, or to write the daily report. Standing jobs start from the scheduler. Cost per role is metered in the ledger and shown in the daily report. At the escalation cap the request waits for the owner; the system never raises the cap or switches model on its own.

## Where work runs

The cheapest token is the one never spent, so work is placed as low on this list as it can go:

1. **Kernel-run tasks** (no model): anywhere the kernel runs.
2. **Local model on the PC** (Ollama): routine judgment, free, private, can reach local files.
3. **Local model on the VPS** (if it has the memory or a GPU): always-on, for work that needs no PC files. Needs a benchmark; model size and speed are unknown.
4. **Haiku-class API**, then **Sonnet at low effort**: only where local models are not reliable enough, or as the single step-up.
5. **Orchestrator tier**: escalation only, under a daily cap.

`host:` in the agent spec says where an agent runs, and the kernel checks that the host has the paths and secrets the spec needs. Agents that need local files stay on the PC. Agents that only call web APIs (shop monitoring, for example) can move to the VPS.

**GitHub Actions** is a possible host for kernel-only or API-only jobs, but it is not a free lunch. Runners are ephemeral, so the SQLite state and the audit ledger would need durable storage between runs, and the ledger's hash chain must not be forkable by two overlapping runs. Secrets would live in GitHub Secrets rather than Credential Manager, and scheduled workflows can be delayed or paused. Running a model on a runner is likely too slow and weak to be useful. Treat Actions as an option to evaluate after the PC and VPS paths work, through a task and a new ADR, not as the default.

## Run lifecycle

1. Trigger (schedule, owner command, or orchestrator) creates a job with a stable idempotency key and a directive.
2. Kernel loads the approved spec, creates budgets, opens the ledger.
3. Runtime builds the prompt: spec role, task, mandatory rules core, relevant approved memory, bounded tool results. External content is wrapped as data.
4. Model returns structured proposals.
5. For each proposal: validate -> tier decision -> (queue | persist intent -> execute -> verify -> persist result) -> return result as data.
6. Loop until the agent says done, a budget is exhausted, or an unresolved effect appears.
7. Kernel writes a run summary. Failures raise an alert flag.

Fresh run each time. Nothing but approved memory and ledger-derived facts carries over.

## Safety design

- **Data vs instructions**: tool outputs and external text are passed as quoted data blocks; the kernel never parses them for commands. Injection defense is structural: tier limits and spec tools bound the damage regardless of what the model is persuaded to do.
- **Approvals**: bound to the SHA-256 of the exact normalized action payload, the run ID, and an expiry. A changed payload needs a new approval.
- **Promotion**: moving an action T3 -> T2 for a given agent is an owner-signed registry entry. Demotion is immediate.
- **Secrets**: resolved by the kernel at execution time from Windows Credential Manager (or environment), injected into the tool call only, never into prompts, logs, or exports. Each agent can use only secrets named in its spec.
- **Providers**: no silent fallback. Cloud model outage or spend cap reached means stop and alert. Claude spend is tracked durably against a monthly cap.
- **Ledger**: append-only, hash-chained, intent before effect. If it cannot be written, new effects stop.
- **Reconciliation**: after a crash, each tool's `reconcile` establishes whether the effect happened; uncertain effects block retry.

## Storage

| Data | Home |
|---|---|
| Code, tool packs, rules | Git |
| Agent specs and approvals registry | Private config directory outside the checkout; specs may also be versioned in a private repo |
| Runs, actions, budgets, approvals, spend | Private SQLite (WAL, synchronous=FULL) |
| Audit ledger and run reports | Private directory |
| Secrets | Windows Credential Manager |
| Knowledge | Obsidian vault outside Git (later phase) |

## Reuse from v1

Port: strict config models, SQLite state and idempotency, hash verification, hash-chained ledger, loopback endpoint check, secret scanner, memory registry, Ollama provider. Replace: fixed manifest plan, one-word model roles, fixed five-tool set.

## Deployment

PC first, driven by Windows Task Scheduler running `jangus tick`. VPS execution is a later option for agents without local-file needs, using the v1 tunnel and token pattern.

## Open design points

Alert channel beyond CLI and daily report; per-tool reconcile rules for Shopify/Printify/email; test strategy for LLM-driven agents (scripted fake models plus recorded real-model transcripts).
