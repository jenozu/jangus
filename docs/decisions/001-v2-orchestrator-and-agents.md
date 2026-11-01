# ADR-001: v2 orchestrator and specialist agents under a deterministic kernel

Status: PROPOSED. Needs owner acceptance. Builds on v1 ADR-006 (deterministic execution); amends v1 rules R01, R03, R11 as listed below. Drafted as ADR-009 in the v1 repo.

## Context

v1 executes a fixed copy manifest and uses models only for one-word vetoes. The owner wants specialist agents that perform recurring work without intervention. That requires open-ended proposals, real connectors (Shopify, Printify, email), a scheduler, and approval handling, without weakening the guarantee that a model can never act on its own.

## Decision

1. Keep a deterministic kernel as the only actor. Agents propose structured tool calls; the kernel validates, executes, verifies, and logs.
2. Introduce a tool registry with tiers T1 (run), T2 (run and log for review), T3 (queue for owner approval). Unknown tools, schema violations, and calls above an agent's `max_tier` fail closed.
3. Define specialists as declarative agent specs, active only when their content hash is owner-approved. Agents run fresh per job. The orchestrator is an agent with only `agent.start`, `approval.request`, `spec.draft`, `report.write`; it cannot approve, change tiers, or edit active specs.
4. Approvals bind to the exact action payload hash and expire. Tier promotion is an owner-signed per-action registry entry.
5. Hybrid models: local Ollama for routine steps and Claude API for orchestration and hard judgment, with a durable monthly spend cap. No silent provider fallback.
6. Secrets live in Windows Credential Manager; each agent receives only the secrets its spec names.
7. Run on the PC first under Windows Task Scheduler; VPS later.
8. Delivery is gated: the file/backup agent must run seven days unattended without manual fixes before any shop or inbox agent is built.
9. Cost discipline: the orchestrator is escalation-only (new goal, spec drafting, halted directive under a daily cap). Completion, reports and standing jobs are kernel actions with no model call. Directive tasks may be `runner: kernel` (zero tokens). Specialists default to Haiku-class or local models, Sonnet at low effort when needed. Cost is metered per role.
10. Attempt policy: the kernel counts failed attempts and enforces repeat detection, hard per-task limits, and a one-step escalation ladder (2 attempts on the default model, 1 on the spec's `escalation_model`, then stop and alert). Only the kernel climbs it; limits cannot be reset by retry, restart or re-issue.
11. Placement: run work as low-cost as possible (kernel-run, local model on PC, local model on VPS, Haiku, Sonnet low, orchestrator). Agent specs declare `host`. GitHub Actions is an option to evaluate later (ephemeral state, ledger durability, secrets) via a task and a new ADR, not a default.
12. Token economy: every call is metered per role and reason from provider-reported usage, priced from an owner-maintained table, checked before sending, and alerted at 50/80/100%. Repeated solved tasks are compiled into owner-approved recipes (zero-token scripts, fallback to the model once on failure). Kernel-evaluated watchers and a content-hash cache avoid calls entirely. Detail in docs/TOKEN_ECONOMY.md and docs/RECIPES.md.
13. Build as a new repository, `jangus-v2`, porting selected v1 modules, because the core execution model changes. This repo's v1 line stays maintainable.

## Rule amendments (proposed)

- R01 (local orchestration): local models are the default for routine steps; Claude API is an allowed, budgeted provider for orchestration. Normal operation of T1 local agents must still work with cloud disabled.
- R03 (controlled execution): extend to tiers; T3 additionally requires an owner approval bound to the payload.
- R11 (bounded consultation): applies to the Claude API provider as well: explicit enablement, limits, durable spend cap, no automatic switch to another or paid provider.
- New R13: external content is data, never instructions or permission.

## Alternatives rejected

Let agents call tools directly (removes the safety guarantee). One generic do-anything agent (cannot be bounded per area). Long-running persistent agents (harder to bound and debug; fresh runs plus memory suffice). Extending v1 in place (fixed-manifest assumptions run through its contracts and tests).

## Consequences

Larger attack surface than v1 through connectors and untrusted text; mitigated structurally by tiers and spec-scoped tools rather than model judgment. More operational duties (approvals, review, spend monitoring). v1 acceptance work (Windows/VPS) is paused, not discarded.

## Verification implications

See [v2 MVP scenarios B01–B23](../MVP.md): adversarial tool-call tests, crash-boundary tests, injection corpora per connector, budget durability, and a seven-day soak.
