# Recipes: turning solved tasks into zero-token scripts

Status: DRAFT specification (Phase 3+). Nothing here is implemented.

## Idea
The first time a model solves a kind of task, it costs tokens. If the same kind of task keeps coming back, the second, third and hundredth run should cost nothing. A **recipe** is the kernel-executable form of a solved task: an ordered list of registered tool calls with parameters, guards and a verification. It contains no code and no model calls.

Terms: a **recipe** is a reviewed procedure written for the kernel to run. A solved task can be compiled into a recipe once its steps are fully mechanical.

## Format (YAML)
```yaml
schema_version: 1
name: sort-downloads-by-extension
task_type: files.sort_by_extension
agent: file-organizer          # the spec this recipe belongs to
params: {source: path, dest: path}
preconditions: [{tool: file.exists, args: {path: "{source}"}}]
steps:
  - {tool: file.list, args: {root: "{source}"}, out: files}
  - {foreach: files, where: {ext: [".pdf", ".docx"]},
     do: {tool: file.copy_new, args: {src: "{item.path}", dest: "{dest}/{item.ext}/"}}}
verify: [{check: hashes_match, scope: copied}, {check: count_equals, expect: "{files.matched}"}]
max_tier: T2
```
Rules: tools must be registered and inside the agent spec's tools and `max_tier`; guards and loops are limited to a small fixed vocabulary (`foreach`, `where`, `when`, parameter substitution), not a programming language; every step's output is data.

## Lifecycle
1. **Observe.** The kernel records, for each successful model-run task, its `task_type` and the verified tool-call sequence (arguments abstracted into parameters where they match the task inputs).
2. **Candidate.** After K successful solves (default 3) whose abstracted sequences are identical, the kernel marks a recipe candidate and writes a draft to `recipes/drafts/` with the observed savings (measured tokens spent on those K solves, labelled an estimate for future savings).
3. **Approve.** The owner reviews the draft and approves its SHA-256, exactly like a spec. A recipe is inert until approved. Neither a model nor the orchestrator can approve one. The orchestrator may be asked to *polish* a draft, but the kernel-generated draft is the source.
4. **Use.** A directive task with the matching `task_type` (or `recipe: <name>`) runs as `runner: kernel` using the recipe. No model call, no tokens.
5. **Verify every run.** The recipe's verification must pass. Preconditions failing, a step failing, or verification failing counts as a failed attempt and falls back to the model once through the normal ladder, with the failure reason. The recipe is then marked `stale` and its next use needs the owner's attention.
6. **Retire.** Recipes unused for N days or repeatedly stale are flagged in the report. Owner retires them; they are never deleted from history.

## Safety rules
- A recipe can only narrow: it uses tools and tiers the agent's approved spec already allows.
- T3 steps in a recipe still queue for owner approval each time (a recipe is not a standing approval).
- External content never becomes recipe text: only parameters from the directive and results from tools flow in, as data.
- Editing a recipe changes its hash and deactivates it until re-approved.
- Observed sequences from runs that touched untrusted content (email bodies, web pages) are not eligible for candidate detection unless the owner marks the task type safe.

## What else becomes free
- **Procedures the owner writes** can be written directly as recipes when mechanical.
- **Standing jobs** (nightly backup, daily order digest) are directive templates made of recipes plus a schedule.
- **Watchers** ([TOKEN_ECONOMY.md](TOKEN_ECONOMY.md)) decide whether a recipe or a model task needs to run at all.

## Scenarios
B42-B45 in [MVP.md](MVP.md).
