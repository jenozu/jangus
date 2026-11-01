# Jangus v2 delivery phases

Status: DRAFT. Phase entry requires owner authorization. Detailed task tracking moves to Taskmaster after acceptance.

| Phase | Goal | Exit gate |
|---|---|---|
| 0 | Accept v2 docs and ADR-001; decide new repo `jangus-v2` vs this repo; choose license; set repo rules | Owner sign-off |
| 1 | Kernel: tool registry, tier policy, audit ledger, state, secrets, with fake tools | B01–B07 |
| 2 | Agent runtime: spec loader and approvals, providers (Ollama, Claude), budgets, propose-then-execute loop with scripted fake model | B08–B12 |
| 3 | File/backup agent with scheduler; seven-day unattended soak | B13–B15 |
| 4 | Orchestrator, approval queue, spec drafting, tier promotion, alerting, local-only mode | B16–B19, B23 |
| 5 | Shop monitor (read-only first, then per-action writes) with injection tests | B20, B21 + clean week |
| 6 | Inbox triage (read and draft; send is T3) | B20, B22 + clean week |
| 7 | Approved memory (ported), optional VPS execution | Memory scenarios, remote reconciliation |

Rules: no phase starts before the previous gate passes; a business agent needs a clean unattended week before the next is added; each new write action is added with its own tier, verify, and reconcile rules and tests.
