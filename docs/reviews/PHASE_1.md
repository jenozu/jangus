# Phase 1 review: kernel (B01-B07)

Date: 2026-11-02. Scope: tasks 2-10 and this evidence task, all merged to `main` through PRs #1, #3-#10 (one PR per task, CI green on ubuntu-latest and windows-latest before each merge). Everything here uses **fake tools and no models**; no real local-model or Claude results exist yet.

## Commands run (Linux, Python 3.12.3, merged `main` at 120677d)

```
uv run --locked ruff check      -> All checks passed!
uv run --locked mypy            -> Success: no issues found in 14 source files   (strict on jangus.kernel)
uv run --locked pytest -v --cov=jangus
                                -> 120 passed in 1.06s; total coverage 95%
python3 tools/taskmaster/tasks.py validate -> OK (warnings only: later tasks not yet expanded)
```

Coverage by module: policy 100%, errors 100%, approvals 98%, directives/models 96%, executor 96%, registry 96%, secrets 96%, config 97%, ledger 90%, state 89%.

## Scenario results

| ID | Result | Tests that exercise it |
|---|---|---|
| B01 Unknown tool | PASS | `test_policy::test_unknown_tool_rejected`; `test_executor::test_unknown_tool_rejected_and_logged` (ledger holds a `rejected` entry, no effect, no budget charged) |
| B02 Tier enforcement | PASS | `test_executor::test_t1_runs_and_is_logged`, `test_t2_runs_verified_and_duplicate_key_does_not_rerun`, `test_t3_is_queued_and_does_not_run`; `test_approvals::test_t3_is_queued_not_run_then_runs_once_after_approval` |
| B03 Agent exceeds max tier | PASS | `test_policy::test_above_max_tier_rejected_not_queued`, `test_elevation_outside_workspace_raises_tier`; `test_executor::test_above_max_tier_is_rejected_not_queued` (rejected, nothing queued) |
| B04 Schema violation | PASS | `test_policy::test_malformed_args_rejected` (6 malformed inputs); `test_executor::test_schema_violation_rejected_without_effect` |
| B05 Crash around effects | PASS (simulated) | `test_executor::test_crash_then_restart_no_duplicate_effect` at 5 points: after intent in state, after intent in ledger, after effect, after verify, after ledger result. Each restart reconciles to exactly one effect and `done`. Also `test_unknown_reconcile_marks_unresolved_and_never_reruns`, `test_args_survive_restart_for_reconcile` |
| B06 Ledger/state failure | PASS | `test_executor::test_unwritable_ledger_blocks_new_effects`, `test_ledger_failing_between_intent_and_effect_prevents_effect`, `test_unwritable_state_blocks_new_effects`; `test_ledger::test_write_failure_raises_and_blocks_effects`, `test_second_writer_detected_and_blocks`; `test_state::test_failed_write_blocks_all_further_writes`; `test_approvals::test_unwritable_ledger_means_no_approval` |
| B07 Secret scoping | PASS | `test_secrets::test_agent_without_secret_in_spec_is_denied_before_any_effect`; `test_canary_never_reaches_ledger_state_results_or_errors` (modes ok, raise, echo: canary absent from ledger, state db, results and error text; echoed value redacted) |

Also covered, from later phases' scenario list, because the models exist now: B29 (kernel step outside task tools rejected) and B33 (limit looser than spec rejected) in `test_directives.py`.

## What these results do not show

- **Crash tests simulate process death** by raising a `BaseException` at named points inside one process. They do not kill a real process or cut power, so SQLite `synchronous=FULL` and fsync durability are designed for but not proven.
- **Windows:** the suite runs on `windows-latest` in CI, but nothing has been run on the owner's PC. `WindowsCredentialBackend` (keyring) has never touched a real Credential Manager; CI only checks that it fails closed when keyring is missing.
- **No models, no real tools.** Fake tools touch an in-memory dict only.
- **Ledger truncation** (deleting the newest entries) is not detectable by the hash chain alone. See parking lot.
- **Reconcile callables do not receive secrets** yet; real Shopify/email tools will need that (parking lot).
- `recover()` fails closed (unresolved) for tools that need secrets unless the caller supplies an allow-list lookup.
- The state schema is still `v1`: `args_json`, the `unresolved` status and the `approvals` table were added in place during this phase. No database existed outside tests, so there was nothing to migrate; any future change after real data exists needs a version bump and migration.
- `actions.attempts` exists but nothing increments it; attempt counting and the escalation ladder belong to Phase 2b.
- Consumed-attempt carry-over for a superseding directive is runtime state (Phase 2b); the model only records `supersedes`.

## Owner decisions needed

1. Accept the Phase 1 gate (B01-B07) or ask for more, then authorize Phase 2a. Phase 2 was not started.
2. Optional: run `uv sync --locked && uv run --locked pytest` once on the Windows PC to confirm the suite there.
