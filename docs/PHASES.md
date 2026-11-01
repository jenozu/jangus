# Jangus v2 delivery phases

Status: DRAFT. Phase entry requires owner authorization. Detailed task tracking lives in `.taskmaster/tasks/tasks.json`; this file maps phases to exit gates (scenario IDs are in [MVP.md](MVP.md)).

| Phase | Goal | Exit gate |
|---|---|---|
| 0 | Specs, ADR-001, task list, repo rules. Owner accepts ADR-001, picks a license and package name, sets the default branch | Owner sign-off (task 1) |
| 1 | Kernel: tool registry, tier policy, audit ledger, state, secrets, approvals storage, directive models, with fake tools | B01–B07 |
| 2a | Providers and benchmark spike: Ollama and Claude behind one interface; measure local model structured-proposal reliability, latency and RAM on the owner's PC | Benchmark results recorded; owner chooses the default local/cheap model or decides to use Haiku |
| 2b | Agent runtime and token controls: spec loader and approvals, durable budgets, propose-then-execute loop, attempt policy and ladder, directive runner, metering, price table, pre-call check, alerts, result cache | B08–B12, B24–B26, B29–B34, B36–B41 |
| 3 | File/backup agent with scheduler, host check, cost reconciliation; seven-day unattended soak | B13–B15, B35, B47 |
| 3b | Token economy proof: recipes, watchers, one model-driven read-only task, seven-day model-driven soak with measured tokens | B42–B46 plus a week with zero manual fixes and token totals within budget |
| 4 | Orchestrator (escalation-only), approval CLI, spec drafting, tier promotion, alerting, local-only mode | B16–B19, B23, B27, B28 |
| 5 | Shop monitor (read-only first, then per-action writes) with injection tests | B20, B21 + clean week |
| 6 | Inbox triage (read and draft; send is T3) | B20, B22 + clean week |
| 7 | Approved memory (ported), VPS execution, GitHub Actions evaluation (ADR first) | Memory scenarios, remote reconciliation |

Rules: no phase starts before the previous gate passes; a business agent needs a clean unattended week before the next is added; each new write action is added with its own tier, verify and reconcile rules and tests; every phase reports measured tokens and spend.
