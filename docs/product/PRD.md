# Product requirements

Status: Phase 1 specification. Owner: repository owner. Date: 2026-10-22.
Implementation and acceptance evidence are pending unless explicitly recorded otherwise.

## Problem and outcome

Routine work currently requires repeated chat instructions, tool output forwarding, and manual tracking. Jangus should perform established sequences in ordinary code, ask a local model for judgment when needed, and make failures inspectable without rebuilding context from a chat transcript.

The primary user operates a Windows desktop and an Ubuntu VPS. They need bounded autonomy, low recurring model costs, reusable procedures, durable progress, and a clear record of what happened. Coding agents are secondary users of the repository and development workflow.

## User journeys

1. Submit a bounded file task, see progress, inspect the verified output, and export diagnostics.
2. Reconnect after a restart and continue from confirmed progress without duplicating uncertain actions.
3. Diagnose failure from linked events and evidence, try a limited local recovery, and stop honestly if unresolved.
4. Retrieve reviewed knowledge for a relevant task, then propose a lesson without silently changing trusted notes.
5. Later, submit a VPS job, disconnect, and reconcile its durable status using the same job identity.

## Requirements and delivery gates

“MUST” defines a release requirement at its specified gate, not a claim about Phase 1.

| ID | Requirement | First acceptance gate |
|---|---|---|
| FR01 | CLI submission, status, cancellation, resume, and diagnostic export | Phase 4 |
| FR02 | Validate plans, task dependencies, typed arguments, known tools, and bounded scope before actions | Phases 4–5 |
| FR03 | Persist execution state, action intent/results, evidence, remaining budgets, and version identity | Phase 4 |
| FR04 | Registered bounded file operations with canonical workspace checks and no silent overwrite | Phase 4 |
| FR05 | Verify actual output independently of model assertions; report failed or uncertain verification | Phase 4 |
| FR06 | Recover within persisted attempt/time/action budgets; reconcile interrupted effects before retry | Phase 4 |
| FR07 | Local model planning, worker assignments, and local recovery with cloud disabled | Phase 5 |
| FR08 | Isolated worker contexts with structured result summaries and evidence references | Phase 5 |
| FR09 | Explicit rules/skill discovery, selective loading, validation, and content-revision logging | Phase 5 |
| FR10 | Context budgets, output limits, reserved response space, and durable handoffs | Phase 5 |
| FR11 | Taskmaster MCP development task/dependency tracking with verification-based completion | Phase 3 |
| FR12 | Selective memory retrieval with exact-revision approval, applicability, sharing, and freshness checks | Phase 6 |
| FR13 | Idempotent draft lesson proposals, evidence, review, conflicts, and retirement | Phase 6 |
| FR14 | Durable private VPS jobs, reconnect/status reconciliation, cancellation, and duplicate suppression | Phase 7 |
| FR15 | Optional advice-only Codex consultation; supported Astra integration only if explicitly enabled | Phase 8 |
| FR16 | Optional Graphify and Headroom experiments against a reproducible baseline | Phase 8 |

## Cross-cutting quality requirements

| ID | Required behavior | Evidence |
|---|---|---|
| NFR01 | No hidden cloud dependency or paid fallback in the local MVP | Complete demonstration with outbound model consultation disabled |
| NFR02 | A restart or new session preserves authority and limits | Fault injection and handoff comparisons |
| NFR03 | Every side effect has correlated intent, outcome, and verification or unresolved status | Action timeline inspection |
| NFR04 | Secrets and private content remain excluded according to policy | Synthetic canary tests across logs, exports, and outbound requests |
| NFR05 | One text inference at a time initially; roles may share a model | Scheduling test and hardware benchmark |
| NFR06 | Bounded context/output, retention, and disk usage; logging failure stops new side effects | Oversized input and storage-failure tests |
| NFR07 | Windows desktop and Ubuntu worker instructions are separate and reproducible | Installation checks on each target before claiming support |
| NFR08 | Cancellation prevents new work and reconciles any in-flight effect | Deterministic cancellation and recovery tests |

## Success measures

The full local MVP passes every local acceptance scenario in [MVP.md](MVP.md), including approved-memory scenarios. Record pass/fail and evidence rather than claiming a speculative error-reduction percentage.

Measure verified task completion, retries across revisions, unknown outcomes, duplicate effects, wall-clock and inference latency, peak resource use, tokens (measured versus estimated), and diagnosis completeness. Establish baselines on the actual machines before setting performance targets or choosing a model. Tool-status polling and established job transport should not require LLM calls.

## Product boundaries

Phase 1 is documentation only. The first execution milestone covers deterministic synthetic files; the local MVP includes agents, skills, context, and memory through Phase 6. Remote execution and optional consultation follow.

Excluded from the local MVP: unrestricted shell or generated code execution, browser/Windows GUI automation, arbitrary personal documents, financial transactions, automatic messages, vision, a dashboard, high concurrency, and autonomous promotion of memory or skills. Each later capability needs its own authorization and acceptance criteria.

## Risks and open decisions

- Model quality and memory/latency fit require real benchmarks; do not assume a small model can reliably plan.
- VPS specifications, GPU availability, model placement, and exact model choice are unconfirmed.
- Phase 3 pins Taskmaster 0.43.1 and verifies four offline MCP task operations with explicit local-only provider configuration. No model-backed planning is exposed. Actual local planning inference remains a Phase 5/task 12 hardware check and requires reviewed enablement; no automatic paid fallback is allowed.
- Dependency versions and supported Python/Node ranges are selected and locked in Phases 2–3.
- Astra is a design label, not a verified API model identifier. Confirm supported access, model identity, and pricing before implementation.
- Obsidian approval is an application boundary, not protection from an administrator able to modify all state.
- License choice remains open. No license grant is implied.
- Performance gains from compression or code graphs are hypotheses until measured.

## Requirement ownership

This PRD owns product requirements; [MVP.md](MVP.md) owns release acceptance scenarios; [rules.md](../../rules.md) owns shared working rules. [Architecture decisions](../architecture/decisions/README.md) explain design choices. Taskmaster becomes the sole detailed development backlog in Phase 3; the phase plan is a delivery map, not a duplicate task-status tracker.
