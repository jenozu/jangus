# Directives: task-list delegation

Status: DRAFT specification (Phase 2). Nothing here is implemented.

## Idea
The orchestrator does not hand specialists a vague instruction. It hands them a **directive**: a small Taskmaster-style task list. The specialist follows it task by task, in dependency order, and the kernel keeps the status. The development task list in `.taskmaster/` and runtime directives use the same schema and the same rules, so one habit and one set of tooling covers both.

## Shape
A directive is JSON, validated with strict pydantic models. It mirrors `tasks.json`:

```json
{
  "schema_version": 1,
  "id": "dir-2026-11-02-backup-nightly",
  "issued_by": "orchestrator",
  "assignee": "backup-maintainer",
  "spec_sha256": "<hash of the approved assignee spec>",
  "goal": "Verify nightly backup of Documents to D:/backups.",
  "non_goals": ["Do not delete anything", "Do not touch folders outside the list"],
  "escalate": false,
  "budget": {"calls": 40, "tokens": 60000, "wall_seconds": 900, "spend_usd": 0},
  "tasks": [
    {"id": 1, "title": "List source files", "status": "pending", "priority": "high",
     "dependencies": [], "details": "...", "testStrategy": "file.list returns the expected folder tree",
     "runner": "kernel", "steps": [{"tool": "file.list", "args": {"root": "D:/Documents"}}],
     "tools": ["file.list"]},
    {"id": 2, "title": "Copy new or changed files", "status": "pending",
     "dependencies": [1], "details": "...", "testStrategy": "every copied file hash matches source",
     "tools": ["file.hash", "file.copy_new"]}
  ]
}
```

## Rules
1. **Issued, not self-made.** Only the orchestrator (via `agent.start`) or the owner creates a directive. A specialist cannot add, remove, reorder or re-scope tasks; it may only propose a note or flag a blocker.
2. **Scoped by the spec.** Every task's `tools` must be a subset of the assignee's approved spec tools, and the directive's tier ceiling cannot exceed the spec's `max_tier`. The kernel rejects a directive that asks for more. A directive grants nothing; it narrows.
3. **One task at a time.** The runtime shows the specialist only the current task, its dependencies' verified results, and the directive goal and non-goals. Tasks run in dependency order.
4. **Kernel-owned status.** A task becomes `done` only when its `testStrategy` check passes. Where possible the check is a kernel-run verification (hash compare, file exists, row count); otherwise it is recorded as `review` for the owner. The model saying "done" never sets status.
5. **Log from evidence.** The kernel appends a timestamped log entry per tool call result (ledger ID), failure and verification result. Logs are written by the kernel, never by free-text from the model.
6. **Stop conditions.** A budget exhausted, an unresolved effect, a rejected T3 approval, or exhausting the attempt ladder (below) on a task sets the task `blocked`-equivalent (`deferred` with a reason), halts the directive and raises an alert. No retries beyond the persisted limits.
7. **Drift rule.** Work outside the current task is rejected. New ideas go to the run report as suggestions for the orchestrator or owner; they never become tasks automatically.
8. **External content is data.** Directive text is authored by the orchestrator or owner; email, orders or file contents referenced by a task are never treated as directive text.
9. **Immutable once issued.** The directive file's hash is recorded in the ledger at issue. Changes need a new directive that supersedes the old one.
10. **Fresh runs.** A directive is the only continuity a run gets, plus approved memory and ledger-derived facts.

## Attempts, loops and step-up
Every task has hard limits so nothing can loop. Defaults come from the agent spec; a directive may only tighten them.

**What counts as one failed attempt** (the kernel counts, never the model): a rejected or failed tool call; a failed verification of the task's `testStrategy`; a model reply the kernel cannot parse into valid proposals; a repeated proposal (below); or a no-progress cycle (three consecutive proposals that produce the same result hash and no new evidence).

**Repeat detection.** The kernel hashes each proposal as (tool, normalized arguments). If the same hash appears twice within one task, the second is refused without executing and counts as a failed attempt.

**Ladder for `runner: model` tasks** (defaults, set in the spec's `attempts` block):
1. Attempts 1-2 use the spec's default model.
2. Attempt 3 is a single step-up on the spec's `escalation_model` (for example Haiku to Sonnet low effort), only if the spec names one and the budget allows. The step-up sees the failure reasons recorded by the kernel, nothing else.
3. If that fails, the task is set `deferred` with a reason, the directive halts, and an alert is queued for the owner. The orchestrator is called only if the directive has `escalate: true` and the daily cap is not reached.

`runner: kernel` tasks have no step-up: a failed scripted step gets the tool's own transient-retry allowance, then the directive halts.

**Hard per-task limits** (any hit counts as a failed attempt, and the third hit stops the task): `max_model_calls` (default 6), `max_proposals` (default 12), `max_wall_seconds`, and the directive's overall budget. A task never gets a fresh budget by being retried, restarted or re-issued; a superseding directive inherits the consumed attempts for the same task.

**Nothing climbs on its own.** No agent can choose a stronger model, raise a limit, or call the orchestrator. Only the kernel moves a task up the ladder, and only as specified here.

## Cost rules (the orchestrator is not in the loop)
1. **Completion is deterministic.** Marking a task done, finishing a directive, and writing the run report are kernel actions. No model is called to say "I'm done", and the orchestrator is never asked to confirm. The run report is a fixed template filled from ledger data.
2. **Standing jobs skip the orchestrator.** The scheduler instantiates an approved directive template directly for the assignee. The orchestrator is not involved unless the directive halts.
3. **Kernel-run tasks.** A task may set `"runner": "kernel"` with a scripted list of tool steps (for example list, hash, copy_new, report). The kernel executes and verifies them with zero model calls. `"runner": "model"` (the default) is used only where judgment is needed, and then only the specialist's model is called, one task at a time with minimal context.
4. **Escalation, not supervision.** Only these events may call the orchestrator: a new owner goal, a request to draft a spec, or a halted directive. A halted directive first goes to the owner's alert queue. The kernel calls the orchestrator for a halt only if the directive's `escalate` flag is true and the daily escalation cap is not reached. At the cap, the escalation waits in the queue for the owner.
5. **Cheap models by default.** The assignee's spec sets the model: local Ollama or a Haiku-class model for routine judgment, Sonnet at low effort for harder tasks. The orchestrator tier is reserved for escalation.
6. **Metered.** Every call is recorded per role (kernel-run, specialist, orchestrator) with tokens and spend, so the cost of supervision is visible and can be capped.

## What the orchestrator does with it
Given a goal or a trigger, it drafts a directive for an approved specialist, issues it with `agent.start`, and later reads the finished directive (statuses, ledger references, alerts) to write the report. It may request owner approval for queued T3 actions. It still cannot approve, promote tiers, or edit specs.

## Owner-written directives
The owner can write the same file by hand, or run `jangus directive new` (Phase 3+) to scaffold one from a template. Standing jobs (for example the nightly backup) are a directive template plus a schedule in the agent spec's `triggers`.

## Relationship to development tasks
`.taskmaster/tasks/tasks.json` builds Jangus. Directives run Jangus. Same field names (`id`, `title`, `status`, `priority`, `dependencies`, `details`, `testStrategy`), same status set, same "depend only on lower ids" rule, so `tools/taskmaster/tasks.py validate` can lint a directive file too (Phase 2 task).
