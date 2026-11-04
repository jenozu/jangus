# Phase 3 review: file tools, scheduler, backup agent, cost reconciliation (tasks 18, 19, 20, 33)

Date: 2026-11-03. Status: **built; the gate is NOT passed.** Gate B15 (seven clean unattended days on the owner's PC) has not started, so nothing here proves the phase. Owner steps are in [docs/SOAK.md](../SOAK.md).

Authorization: the owner said "let's continue" after Phase 2b (2026-11-03). The agent read that as authorization to build Phase 3 (tasks 18, 19, 20, 33), not to start the soak, which only the owner can run. Built as PRs #30-#33 (#31-#33 stacked on #30's branch); none merged by the agent.

## What is real and what is fake

| Piece | Evidence | Real hardware / service? |
|---|---|---|
| File tools (list, read, hash, copy_new, copy_new_tree, report.write, overwrite, delete) | pytest on real files in temp directories, real symlinks where the OS allows them | Linux CI and Windows CI temp folders only. **Not** the owner's real folders, drives or junctions |
| Scheduler, cron, run lock, coalescing | pytest with injected clock | No Windows Task Scheduler involved |
| Backup agent end to end | The actual `agents/drafts/` spec and directive run through `run_standing_directive` and `Scheduler` against temp folders | No real backup drive; no model at all (kernel-only agent) |
| Daily report, `status`, `cost` commands | pytest on ledger fixtures and the CLI entry point | No real usage |
| Reconciliation | Fixture CSV/JSON exports | **No real Anthropic export** (column names are an assumption) |
| `scripts/install_task.ps1` | Not run anywhere | Untested PowerShell |

## Commands (Linux, tip of the stack = task 33 branch)

```
uv run --locked ruff check     -> All checks passed!
uv run --locked mypy           -> Success: no issues found in 45 source files
uv run --locked pytest         -> 538 passed
python3 tools/taskmaster/tasks.py validate -> 0 errors
```
CI was green on ubuntu-latest and windows-latest on each pushed PR (see the PR pages).

## Scenario results

| ID | Result | Tests |
|---|---|---|
| B13 Scheduled run | PASS (simulated clock) | `test_scheduler`: cron matching; missed ticks coalesce (three nights, one run); an overlapping tick does nothing and loses no slot; stale lock takeover; a crash leaves the slot open; a halted directive is not retried every tick; unapproved or edited specs do not run. Task Scheduler invoking `jangus tick` is **not shown** |
| B14 Backup correctness | PASS (temp folders) | `test_files`, `test_backup_agent`: every copy byte-verified; tampered or pre-existing different destinations are detected and never overwritten; no silent overwrite or delete (T3 only, approval bound to the exact payload); traversal, relative, outside-folder and symlink escapes rejected |
| B15 Seven-day soak | **NOT RUN** | Owner task 21. `test_backup_agent` shows seven simulated consecutive days complete under a small daily budget |
| B35 Host check | PASS (simulated) | `test_backup_agent`: with the backup drive missing the run is refused with an alert and runs on a later tick once it is back |
| B47 Reconciliation | PASS (fixtures) | `test_cost_cli`: gap above 5% alerts; at or below does not; totals match the ledger; bad exports refused |
| B29 (carry-over) | PASS | The nightly backup writes no `model_call` record and open actions stay empty |

## Defects found while building (and fixed)

- Agent budgets were lifetime totals. A nightly agent would have run out of budget within days and broken the soak. They are now per day (`agent:<name>:<date>`). Only reachable once an agent runs repeatedly, which is why the Phase 2b tests did not see it.
- ruff's `extend-exclude = ["tools"]` silently skipped `src/jangus/tools`; narrowed to `tools/taskmaster`.

## What these results do not show

- Nothing ran on the owner's PC: no Task Scheduler, no real drive, no real folder permissions, no junctions, no large trees, no sleeping laptop.
- Behaviour when two ticks race in a way tests cannot reproduce, or when the clock moves backwards or the time zone changes. Cron uses local wall-clock time.
- `jangus tick` has never been run by anything but tests. The first real tick may expose configuration mistakes.
- Model-driven tasks still end in `review` for lack of per-task verifiers; the backup agent avoids the problem by being kernel-only. Nothing model-driven is in this phase.
- The 5% reconcile threshold and the export column names have not met a real export.

## Owner steps

1. Merge the open PRs in order (see MEMORY.md): #29, #30, #31, #32, #33, then this one.
2. Follow [docs/SOAK.md](../SOAK.md): edit the two draft files for your real folders, approve the spec, register the schedule, and log seven days.
3. Follow [docs/COST_SETUP.md](../COST_SETUP.md) before turning Claude on: dedicated key, provider-side limit, `[caps]`, `prices.yaml`.
4. Run the Phase 2a benchmark (task 17) and choose the default cheap model, still open.
