# Phase 2b review: agent runtime and token controls (tasks 14, 15, 16, 25, 26, 28, 29, 32)

Date: 2026-11-03. Scope: durable budgets, propose-then-execute run loop, attempt policy and ladder, directive runner, cost metering and price table, pre-call budget gate and spend alerts, result cache, context budget. Authorized by the owner on 2026-11-03 ("proceed with the next phase"). Built as stacked PRs #20-#27 on top of #19 (authorization record) because merging beyond #18 was not authorized in the session; **none of #19-#27 was merged by the agent**, so `main` does not contain this work until the owner merges them in order.

> **Later note (2026-11-04):** this is a dated record of what was built and is left as written. The model decision it assumed has since changed: Claude Haiku over the direct Anthropic API is now the default cheap model and the local Ollama provider is optional and off by default ([ADR-002](../decisions/002-claude-direct-default-model.md), and the gate change in [PHASES.md](../PHASES.md)). The Ollama benchmark described here is no longer required to continue.

Gate (docs/PHASES.md): B08-B12, B24-B26, B29-B34, B36-B41.

## What is real and what is fake

| Piece | Evidence | Real hardware / service? |
|---|---|---|
| Budgets, alerts, attempts, cache, state migration | pytest on SQLite files in temp dirs, restart simulated by closing and reopening the files | Real SQLite and ledger files; **not** run on the owner's PC |
| Run loop, directive runner, gate, pricing, report | pytest with a **scripted fake model** (`tests/agent_harness.py`) that returns canned replies and records requests | **No real model call of any kind**: not Ollama, not Claude |
| Prices | A test table written in the tests | **No real price list**; the owner must write `prices.yaml` |
| Token counts, cached tokens, latency | Numbers the fake model reports | Never measured against a provider |
| Windows | CI `windows-latest` on every PR | Not run on the owner's PC |

## Commands (Linux, Python 3.12, tip of the stack = task 32 branch)

```
uv run --locked ruff check     -> All checks passed!
uv run --locked mypy           -> Success: no issues found in 39 source files   (strict on jangus.kernel)
uv run --locked pytest --cov=jangus
                               -> 437 passed; total coverage 96%
python3 tools/taskmaster/tasks.py validate -> 0 errors
```

New-module coverage: runner 97%, directives/runner 94%, gate 96%, pricing 95%, context 97%, report 96%, kernel/attempts 97%, budgets 99%, cache 100%, escalation 94%, attempt_tracker 100%, metering 100%.

## Scenario results

| ID | Result | Tests |
|---|---|---|
| B08 Budget durability | PASS | `test_budgets`: call, token, wall-time and spend budgets stay exhausted after closing and reopening state with new objects; re-defining limits does not reset `spent`; a raised limit re-arms the alert |
| B09 Spec approval | PASS (Phase 2a) | `test_agent_specs`; the run loop is handed an already-loaded spec |
| B10 Fresh runs | PASS | `test_runner::test_fresh_run_receives_nothing_from_the_previous_run` |
| B11 Provider rules | PASS (fake) | Phase 2a hub tests plus `test_runner`: a failing provider ends the run, alerts and the other provider receives nothing; `test_gate`: a blocked provider is refused by the gate and by the hub |
| B12 Scripted agent loop | PASS | `test_runner`: proposals run only through the executor, results return as quoted data, loop ends on done, budget, limit, provider error |
| B24 Directive scope | PASS (Phase 1) | `test_directives`: tool outside the spec, tier above `max_tier` rejected at issue |
| B25 Kernel-owned status | PASS | `test_directive_runner`: a model claiming done does not set done; done only after the kernel check passes; no check means `review`; status changes are ledger events |
| B26 Directive stop rules | PASS | `test_directive_runner`: budget exhaustion, unresolved effect, T3 waiting for approval, rejected T3 approval, spent ladder each defer the task, halt the directive and queue an alert. Note: "two failed attempts" halts when the spec has no step-up; with a step-up it is three (B32) |
| B29 Kernel-run tasks | PASS | `test_directive_runner`: a kernel-run directive completes and verifies with no provider request and no `model_call` record |
| B30 Cost report | PASS | `test_metering`: report totals equal the ledger's `model_call` events and the budgets' spent; per role, per run and per agent |
| B31 Repeat detection | PASS | `test_attempts`: second identical (tool, normalized args) refused, not executed, counted |
| B32 Escalation ladder | PASS | `test_attempts`: 2 on the default model, 1 on the step-up model, then stop with an alert; a new job on the same task makes no call; the step-up sees only the kernel's failure reasons |
| B33 Hard task limits | PASS | `test_attempts`: limit hits count as failed attempts and survive a restart; a superseding directive inherits consumed attempts; a task cannot loosen spec limits |
| B34 No self-escalation | PASS | `test_runner`, `test_attempts`: replies asking for another model, a higher limit or the orchestrator are ignored (key names are logged) and do not move the ladder |
| B36 No-progress | PASS | `test_attempts`: three same-hash results with no new evidence are one failed attempt, kept across a crash before the round closes |
| B37 Token ledger | PASS | `test_metering`: every call record carries role, reason, model, measured input/output/cached tokens, cost, estimate; unknown usage is charged at the estimate and flagged; kernel-run is always zero |
| B38 Price table | PASS | `test_metering`: unpriced paid model refused before sending; local models free; stale table alerts once |
| B39 Pre-call check | PASS | `test_gate`: worst-case cost over the agent, directive, day or month limit is refused before sending and counted as a failed attempt; never downgraded |
| B40 Spend alerts | PASS | `test_gate`: 50% and 80% fire once; 100% alerts and blocks the paid provider (durably, and at the hub) until the owner unblocks; big-call, runaway and drift flags |
| B41 Result cache | PASS | `test_cache`: repeat makes zero calls; spec, prompt version, input, task type, model change or TTL expiry miss; unclean rounds are not cached; stale entries are dropped; bounded |

Also covered (not gate items): V2-FR39 context budget (`test_context`: deterministic summaries, trimming order, refusal when it cannot fit), schema v1 to v3 in-place migration (`test_attempts`), directive files linted by `tasks.py validate`, the daily orchestrator escalation cap (`test_metering`).

## What these results do not show

- No real model has produced a proposal under this loop. Everything the model "says" was written in the test.
- Costs, token counts, cached-token billing and latency come from the fake; the price table is a test fixture. The estimator (bytes / 3) has not been calibrated against any provider.
- The first real run will show whether models obey the reply format, how often the cache hits, and what a task really costs. That is the Phase 3b soak.
- Model-task verification needs per-task verifiers; none exist yet. Without one a model task ends in `review`. The first real verifiers arrive with the file/backup agent (Phase 3).
- `Caps` (daily and monthly spend) are in the config model, but no CLI command starts a run yet, so nothing wires config to the gate; there is also no CLI for `unblock` or for reading alerts (`jangus status`).
- Alerts are ledger entries only. Push or email delivery, and the orchestrator itself, are Phase 4. The escalation cap is built and tested but nothing calls the orchestrator.
- Batch endpoints were deliberately not built (TOKEN_ECONOMY.md: only after measurement shows a saving).
- Task 33 (monthly reconciliation CLI) and the scheduler-driven daily report (task 19) are Phase 3.
- Not run on the owner's Windows PC.

## Owner steps before Phase 3

1. Review and merge PRs #19-#27 in order (each is stacked on the previous one).
2. Write `prices.yaml` from the provider's price list (format in `agents/pricing.py`) and choose daily and monthly caps.
3. Run the Phase 2a benchmark (task 17) and pick the default cheap model; both are still open.
4. Authorize Phase 3 (file tools, scheduler, backup agent) when ready.
