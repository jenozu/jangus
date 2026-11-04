# Jangus v2 delivery phases

Status: DRAFT. Phase entry requires owner authorization. Detailed task tracking lives in `.taskmaster/tasks/tasks.json`; this file maps phases to exit gates (scenario IDs are in [MVP.md](MVP.md)).

| Phase | Goal | Exit gate |
|---|---|---|
| 0 | Specs, ADR-001, task list, repo rules. Owner accepts ADR-001, picks a license and package name, sets the default branch | Owner sign-off (task 1) |
| 1 | Kernel: tool registry, tier policy, audit ledger, state, secrets, approvals storage, directive models, with fake tools | B01–B07 |
| 2a | Providers and benchmark spike: Claude and Ollama behind one interface; measure structured-proposal reliability, latency and RAM | Providers built and tested (done); **model-choice gate satisfied 2026-11-04 by owner decision, benchmark now OPTIONAL** (see Gate changes) |
| 2b | Agent runtime and token controls: spec loader and approvals, durable budgets, propose-then-execute loop, attempt policy and ladder, directive runner, metering, price table, pre-call check, alerts, result cache | B08–B12, B24–B26, B29–B34, B36–B41 |
| 3 | File/backup agent with scheduler, host check, cost reconciliation; seven-day unattended soak | B13–B15, B35, B47 |
| 3b | Token economy proof: recipes, watchers, one model-driven read-only task, seven-day model-driven soak with measured tokens and **measured spend** | B42–B46 plus a week with zero manual fixes and token totals within budget |
| 4 | Orchestrator (escalation-only), approval CLI, spec drafting, tier promotion, alerting, provider-disabled mode | B16–B19, B23, B27, B28 |
| 5 | Shop monitor (read-only first, then per-action writes) with injection tests | B20, B21 + clean week |
| 6 | Inbox triage (read and draft; send is T3) | B20, B22 + clean week |
| 7 | Approved memory (ported), VPS execution, GitHub Actions evaluation (ADR first) | Memory scenarios, remote reconciliation |

Rules: no phase starts before the previous gate passes; a business agent needs a clean unattended week before the next is added; each new write action is added with its own tier, verify and reconcile rules and tests; every phase reports measured tokens and spend.

## Gate changes

Gates are only ever changed in writing, with a date and a reason.

**2026-11-04 (owner decision, [ADR-002](decisions/002-claude-direct-default-model.md)).** Phase 2a's exit gate read: *"Benchmark results recorded; owner chooses the default local/cheap model or decides to use Haiku."* The second half is now **satisfied**: the owner chose Claude Haiku over the direct Anthropic API, because the owner's PC runs Ollama models too slowly. The first half, running `scripts/benchmark_ollama.py` on the PC, becomes **optional** and no longer blocks any later phase.

Exactly what changed, and what did not:

| | Before | After |
|---|---|---|
| 2a model choice | Blocked until the benchmark was run and a model picked | Satisfied by the 2026-11-04 decision |
| 2a benchmark run | Required | Optional; kept as a tool for a faster PC or a GPU VPS |
| 2a provider work | Built and tested with fake transports | Unchanged, and still only fake transports |
| 3 / B13–B15, B35, B47 | Unchanged | Unchanged. The backup agent still calls no model |
| 3b / B42–B46 | Tokens measured | Unchanged, plus measured spend, because the cheap rung now costs money |
| 4 "local-only mode" | Degraded mode on a local model | Reworded: kernel-run work, recipes and watchers continue with the paid provider disabled. No gate removed |
| every other gate | – | Unchanged |

No gate was weakened: B01–B47 still have to pass, the seven-day unattended soak (B15) is still Phase 3's exit, and a business agent still needs a clean unattended week before the next one is added. What changed is which model answers a `runner: model` task, and that a measurement nobody can act on no longer blocks the build.

One prerequisite follows from the decision, because the cheap rung is no longer free: Phase 3b cannot start before `[caps]`, `prices.yaml` and a dedicated provider-limited key exist (COST_SETUP.md, task 43). The privacy trade-off in ADR-002 was accepted by the owner on 2026-11-04 for the mailbox as well, so it does not gate Phase 6.
