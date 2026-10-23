# System architecture

Status: intended runtime design. Phases 2–3 implement package setup, read-only configuration validation, and an offline development Taskmaster adapter; see [README.md](../../README.md) for current capabilities. Shared requirements live in [PRD.md](../product/PRD.md); authority rules live in [rules.md](../../rules.md).

## Components and authority

| Component | Responsibility | Does not own |
|---|---|---|
| Controller | Durable run lifecycle, scheduling, limits, cancellation, recovery | Model reasoning |
| Local orchestrator | Interpret request, propose plans, assign focused work | Permission grants |
| Workers | Specialized reasoning within explicit assignments | Independent budget resets |
| Tool broker and policy | Validate registered actions and enforce configured capabilities | Authority from note text |
| Verifier | Check actual results against acceptance criteria | Trust in model success claims |
| Context manager | Select evidence, rules, skills, memory; prepare handoffs | Canonical execution state |
| Logger | Correlated events, redaction, timelines, diagnostic exports | Development task status |
| Skill loader | Catalogue, validate, load, and identify reviewed procedures | Tool authorization |
| Memory service | Approved revision retrieval and draft proposals | Executable authority |
| Taskmaster adapter | MCP development task operations | Live job state |
| Remote worker | Durable allowed VPS jobs, status, evidence | Routine desktop orchestration |
| Consultants | Optional bounded guidance after local recovery | Tool execution or policy changes |

Start with one Python application and SQLite rather than a service per role. The VPS worker becomes a separate process when Phase 7 begins. Multiple roles can share one local model; initial inference concurrency is one. Model placement is a benchmark decision.

## Execution lifecycle

A proposed run lifecycle is: queued, planning, running, verifying, recovering, waiting, and terminal succeeded/failed/cancelled. These are design concepts; concrete schemas and migrations are delivered later.

Every action has a stable ID and durable intent before execution. Completion requires observed results and independent verification. A crash between an effect and its recorded result produces an uncertain action, not a new action. Recovery inspects the real output and original intent, adopts a matching verified result, or pauses if the outcome cannot be proven. Never claim general exactly-once execution across process/network failures.

Validate dependency cycles, duplicate task IDs, missing prerequisites, unknown tools, and stale plan revisions before dispatch. The task dependency graph is separate from the controller state machine: recovery loops do not create new unlimited task budgets.

Cancellation stops new dispatch and requests cancellation of in-flight work. Record any completed or unresolved effects. Automatic rollback is not promised.

## Contracts to define in implementation

| Contract | Required information |
|---|---|
| Run | ID, requested outcome, workspace, policy/config versions, limits, remaining budgets, state |
| Assignment | Run/task/parent IDs, objective, acceptance criteria, input/evidence refs, allowed capabilities, skill/rules refs, budgets |
| Plan | Version, dependency graph, known tool requests, expected effects, verification strategy |
| Action | Stable ID, tool/version, validated arguments, intent, idempotency identity, state, result/evidence |
| Worker result | Verified findings, output refs, failures, uncertain effects, remaining work, evidence |
| Handoff | Scope, verified progress, failures, uncertain effects, exact references, remaining budgets, versions |
| Event | Event ID, timestamp/order, run/task/action/session/machine correlation, category, redacted payload |
| Memory retrieval | Note ID/hash, excerpt, eligibility/approval/scope, sharing class, retrieval reason |
| Remote job | Stable job/idempotency key, payload digest, protocol version, status, result/evidence |
| Consultation | Reason, permitted compact evidence, remaining limits, validated advisory response |

Persist canonical state and revisioned transitions transactionally. Model summaries are views of state, not replacements for it. Concrete schemas belong to later phases; these requirements prevent incompatible component assumptions.

## Context and recovery

Workers receive explicit assignments in separate sessions inspired by Roo's isolated-subtask pattern; Roo itself is not a required dependency. Return compact results with references to detailed evidence.

Build prompts from relevant state, a mandatory rules core, task-specific rules, reviewed skills, eligible memory, and bounded tool outputs. Reserve response space and distinguish token estimates from provider measurements. Keep exact technical evidence available by reference.

Before rollover, persist a structured handoff. Rehydrate budgets, permissions, and uncertain actions from authoritative state and reconcile the handoff. A summary cannot renew retries or broaden scope. Plain concise language is the baseline; compressed styles require comparison tests.

Recovery starts with bounded worker diagnosis, then the local orchestrator. Optional Codex consultation follows local exhaustion; Astra is only a final explicitly enabled fallback if supported. Consultant outputs are advisory plan proposals subject to the same validation. Provider outages must never trigger an unapproved paid fallback.

## Logging and diagnostics

Logging precedes model-driven side effects. Correlate requests, plan versions, action intent/results, verification, failures, retries, cancellation, recovery, context changes, memory retrieval, and remote reconciliation.

Capture machine/model identity, prompt/config/rules/skill revisions, timings, usage with measurement provenance, and permitted evidence references. Capture decision summaries rather than private model chain-of-thought.

Retain original permitted evidence locally under redaction, retention, access, and size policies; compression affects model context only. Never preserve raw secrets merely to claim full logging. Diagnostics contain a readable summary, structured events, permitted artifacts, checksums/provenance, and a manifest explaining omissions. Export is a separate disclosure boundary; redaction applies before any external transmission.

If required state or event persistence fails, stop new side effects. Later implementation must define retention defaults, disk limits, and consistent backup/restore checks.

## Skills and Obsidian

Root `skills/` stores reviewed procedures. `src/jangus/skills/` implements discovery, metadata validation, selective loading, and revision tracking. Each skill defines trigger, inputs, platforms, required tools, steps, expected outputs, verification, and stop/escalation conditions. Supporting references/scripts load only when needed; scripts require registered execution like any other tool.

Initial procedures cover implementing a development task, diagnosing failure, exporting diagnostics, creating handoffs, proposing memory, and (in Phase 7) executing a VPS job. Skills are added with working procedures, not as empty placeholders.

The live vault remains outside Git. The planned template structure is:

```text
templates/obsidian/
├── 00_Index/
├── 10_Environment/
├── 20_Runbooks/
├── 30_Tool_References/
├── 40_Lessons/
├── 50_Runs/
├── 60_Preferences/
├── 70_Inbox_Agent/
├── 90_Archive/
└── _templates/
```

A note has a stable ID, type, scope, provenance, sharing classification, and concise content. An approval registry outside the vault binds an authorized review to the exact content hash, scope, and evidence. Frontmatter alone does not confer approval. Define consistent hashing in Phase 6, accounting for line endings; store immutable selected revisions for traceability.

Filter drafts, retired notes, invalid approvals, incompatible scope, and disallowed destinations before ranking, including any linked-note expansion. Recheck hashes and eligibility before prompt inclusion. Re-observe volatile environment facts when needed. A broken index permits bounded eligible-file search or no memory; unavailable approval state disables operational retrieval.

Agent proposals go to `70_Inbox_Agent/` with supporting evidence and idempotent identity. Expected-hash writes detect user edits and produce conflicts rather than overwrites. Repeated successful runs do not automatically approve a note. Retirement and restoration must not silently reactivate old approvals.

## Desktop and VPS

Use SSH for administration and an SSH tunnel to a loopback-bound worker service for jobs. Verify host identity and use a dedicated service identity with scoped filesystem access. Application authentication and protocol validation remain required; a private connection is not permission for every job type.

The service owns jobs durably, independently of the initiating SSH session. The desktop persists job identity before sending and reconciles by that identity after a lost acknowledgement. The service compares payload digests on repeated keys. Result retrieval verifies identity and artifact integrity. Do not resubmit blindly or assume cancellation reversed an effect.

GitHub Actions is for tests and optional later deployment, not the runtime job bus. Playwright is a future registered worker tool, not a VPS transport.

## Storage boundaries

| Data | Canonical home |
|---|---|
| Code, reviewed skills, prompts, rules, templates | Git repository |
| Development backlog | Taskmaster from Phase 3 |
| Runs, actions, budgets, policy approvals | Private runtime SQLite/state |
| Events and permitted larger evidence | Private event/artifact storage |
| Knowledge text | External Obsidian-compatible vault |
| Knowledge approval and outcomes | Private runtime registry |
| Search/code indexes | Rebuildable private caches with source revisions |
| Secrets and model weights | External credential/model storage |
| Exports and task workspaces | Private directories outside checkout |

No actual runtime directory is committed. Version/config examples appear in Phase 2 and use placeholders. A virtual environment is dependency isolation, not an operating-system security sandbox.

## Intended repository layout

Phase 1 documentation, Phase 2 package/configuration tooling, and the Phase 3 Node development adapter in `scripts/taskmaster/` exist now. The following remains a path allocation for the complete system; later directories and integrations are not yet implemented.

| Location | Purpose and phase |
|---|---|
| `README.md`, `AGENTS.md`, `rules.md` | Project navigation and rules: 1 |
| `docs/product/`, `docs/architecture/`, `docs/development/` | Requirements and design: 1 onward |
| `docs/planning/`, `docs/reviews/` | Phase map and review evidence: 1 onward |
| `pyproject.toml`, `uv.lock` | Python package and resolved dependencies: 2 |
| `config/`, `.env.example`, `.gitignore` | Sanitized examples and exclusions: 2 |
| `.github/workflows/`, contribution templates | Automated checks and workflow: 2 |
| `docs/setup/`, `scripts/windows/`, `scripts/linux/` | Separate setup and health checks: 2 onward |
| `package.json`, `package-lock.json`, `.taskmaster/` | Pinned development tooling and canonical tasks: 3 |
| `templates/task-brief.md`, `templates/task-result.md` | Focused development assignments: 3 |
| `src/jangus/cli/`, `controller/`, `contracts/`, `storage/` | Runtime foundation: 2–4 |
| `src/jangus/tools/`, `policy/`, `verification/`, `observability/` | Controlled execution: 4 |
| `src/jangus/agents/`, `providers/`, `context/`, `skills/` | Local intelligence and loading: 5 |
| Root `prompts/`, `skills/`, `templates/session-handoff.md` | Versioned instructions: 5 |
| `src/jangus/memory/`, `templates/obsidian/` | Reviewed memory: 6 |
| `src/jangus/remote/`, `deploy/systemd/` | Remote service: 7 |
| `src/jangus/integrations/taskmaster/` | Narrow development-task adapter: 3 onward |
| `src/jangus/integrations/graphify/`, `headroom/` | Optional experiments: 8 |
| `tests/unit/`, `integration/`, `acceptance/`, `fixtures/` | Meaningful tests as functionality arrives |
| `examples/tasks/`, `examples/diagnostics/` | Synthetic examples with implemented behavior |
| `src/jangus/ui/` | Later interface; outside local MVP |

Paths such as `controller/` in a grouped row share the stated `src/jangus/` prefix. Do not create empty classes merely to fill the layout.
