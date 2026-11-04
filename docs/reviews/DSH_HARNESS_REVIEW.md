# Review: DeepSeek Harness (`dsh`) as a base for Jangus

Date: 2026-11-04. Reviewer: coding agent, owner request. **Review only: no implementation code, no task started, no PR merged.** Current phase per MEMORY.md: Phase 2b built, awaiting owner merge; Phase 3+ not authorized.

Source reviewed: `github.com/deepseek-ai/deepseek-harness`, shallow clone at commit `fcc0ad46` (2026-11-04). Files read in full: `README.md`, `AGENTS.md`, `SAFETY.md`, `docs/architecture.md`. `docs/tool-catalog.md` (2,699 lines) read by section: package map, `bash`, `pwsh`, `run_code`, `propose_action`, `subagent_oar`, `plugin_manager`. Package READMEs skimmed (first ~40 lines plus grep): `sandbox/sandbox-policy`, `llm/token-meter`, `llm/llm-retry`, `llm/llm-deepseek`, `guard/repeat-tool-reminder`.

Goal assumed (the request left the placeholder unfilled): *let models help with file backup and shop monitoring while keeping the kernel in charge.* Owner: correct this if the goal differs.

## 1. How dsh works vs how Jangus works

| Area | DeepSeek Harness (evidence) | Jangus (CLAUDE.md / MEMORY.md) |
|---|---|---|
| Who acts | The agent loop executes tool calls itself: `tool/call* -> tools/pre-execute -> tools/execute -> tools/post-execute -> tool/result*` inside each step (architecture.md, Turn flow). The loop is itself a replaceable plugin ("There is no privileged core"). | Models only propose structured calls; the deterministic kernel validates against registry, approved spec, tier and budget, then executes, verifies, logs. |
| Loop driver | Model-driven: "tools owe another request ... -> next step" continues until nothing is owed. Interactive, chat-shaped. | Directive-driven: kernel runs ordered tasks; kernel sets status from verified evidence; orchestrator escalation-only. |
| Tools | Broad, model-facing: `bash` ("Execute a bash command (`bash -c`)"), `pwsh`, persistent terminals, `run_code` (TypeScript against all tools), fs write/patch, browser/computer use, `plugin_manager` (install/enable bundles), `schedule_create`, subagents. | Registry of declared tools, each with schema, tier, reversibility, `verify`, `reconcile`. No shell tool. |
| Approvals | Per-session permission *modes* plus per-call approval prompts in a UI (e.g. `run_code.sandbox_permissions` "requires justification and approval"; `plugin_manager` "requires danger-full-access permission or approval for this call"). | T1 free / T2 run+log / T3 queued for owner approval bound to payload hash and expiry, single use; unknown or over-tier fails closed. |
| Sandbox | `sandbox-policy` modes, default `read-only`, opt-in `workspace-write`; session mode switchable at runtime and persisted. Windows ACL backend exists. SAFETY.md: "has not undergone a security audit", sandboxing "do[es] not guarantee isolation". | No sandbox; safety comes from *not* exposing arbitrary execution: only registered tools with narrow schemas. |
| Token controls | `token-meter`: replay-based estimation for context pressure and compaction. Its README says occupancy "is a reference figure, not a billing record: nothing in the harness makes decisions from it". `compaction` package. No spend cap found. | Per-call metering from provider usage, price table, pre-call worst-case budget gate, 50/80/100% alerts, daily/monthly caps, result cache, context budget (Phase 2b). |
| Loop limits | `repeat-tool-reminder` is "advisory: it never blocks or delays a legitimate repeated call"; `llm-retry` offers bounded *or unlimited "always-mode"* retries. | Kernel-counted attempts, repeat detection, hard limits, ladder 2+1 then stop/alert; limits never reset. |
| Providers | DeepSeek adapters, `llm-pi-ai`, external runtimes via `subagent_oar` (Codex, Claude Code). `llm-retry` README describes "fallback" ordering in its recovery waterfall. | Ollama loopback + Claude, hub with no silent fallback, Claude off unless `claude_enabled`. |
| Logging | Strong: "Model-visible means logged"; append-only session log; every request reconstructable. | Hash-chained ledger; intent persisted before effect. |
| Stack | TypeScript, Node >= 24.9, pnpm workspaces, Cordis plugin framework (vendored). A Python SDK launches the Node `dsh` binary. "Developer preview ... THERE WILL BE COMPATIBILITY-BREAKING CHANGES." | Python 3.12, uv, pydantic strict, SQLite WAL, dependency-light kernel. |

## 2. Conflicts with CLAUDE.md rules

1. **"Models think; the kernel acts"**: dsh's loop executes model tool calls directly. Using dsh as-is would let model output run commands. *Hard conflict.*
2. **Model-facing `bash` / `pwsh` / `run_code` / `terminal_*`**: arbitrary execution; cannot declare per-call `verify`/`reconcile` (rule 3) and has no tier meaning (rule 2).
3. **`plugin_manager`, `extensions/` ("runtime self-modification"), `schedule_create`**: model can change the runtime, install code, create schedules; conflicts with "never ... change policy" and "specs/recipes inert until owner approves" (rules 9, 13).
4. **Model calls outside the kernel**: dsh owns the provider call (`ctx.llm`), plus `subagent_oar` spawns Claude Code/Codex with their own permission settings: unmetered by Jangus, invisible to its budgets (rules 11, 13).
5. **Provider fallback / unlimited retry**: `llm-retry` always-mode retries without limit; recovery waterfall has a fallback step. Conflicts with rule 7 (no silent fallback) and rule 12 (limits never reset). Not verified whether fallback switches *provider* or only retries; treat as a conflict until proven otherwise.
6. **Approvals by session mode / UI prompt**, not bound to payload hash + expiry + single use (rule 2, decision 8). A session can switch to a wider mode at runtime.
7. **Advisory loop guard** vs hard kernel-counted limits (rule 12).
8. **No spend caps**: token meter explicitly is not used for decisions (rules 11, 13; decision 14).
9. **Fresh per job** (rule 8): dsh sessions persist, resume, fork; memory carries across turns.
10. **Secrets** (rule 6): dsh reads `DEEPSEEK_API_KEY` / `.env` and has its own `credentials/` package; Jangus requires Credential Manager injection per-agent by the kernel.
11. **External content as data** (rule 5): web/browser/fs tools feed page content straight into model context; no evidence of a quoting discipline (not verified either way).
12. **Stack** (Conventions): adds Node 24 + pnpm + a pre-stable TypeScript runtime beside a Python kernel.

## 3. Integration options

### (a) Borrow specific ideas only (no dependency)
- Ideas worth copying, as parking-lot items for the phase they fit: *"model-visible means logged"* as a testable invariant (every prompt reconstructable from ledger); the read-only-by-default, explicit-opt-in sandbox posture for any future file tool; deterministic token measurement by log replay (already close to task 32); recorded-session snapshot tests replayed keyless (matches our open design point on LLM-agent test strategy); `tool-catalog.md` generated from tool schemas.
- Phase gating: none; ideas wait for their phase. Kernel: unchanged. Dependencies: none. Cost: unchanged. Safety: unchanged.

### (b) Run dsh as a separate tool outside the kernel
- Shape: a kernel-registered tool (e.g. T3 `dsh.run_task`) that launches `dsh --profile headless` in a disposable VM/container for one bounded job, returns output as quoted data.
- Phase gating: new tool + new runtime = new design; needs ADR and at least Phase 3 authorization. Kernel: sees one opaque call; it cannot validate the dozens of inner actions, so `verify`/`reconcile` are near-impossible and the call must stay T3 forever. Dependencies: Node 24 + pnpm on the PC, pinned version against breaking changes. Cost: dsh's model calls bypass Jangus metering and caps; would need a separate DeepSeek key with provider-side spend limit and a kernel wall-clock/attempt cap. Safety: depends on VM isolation, which SAFETY.md says dsh itself does not provide. Weak fit for backup and shop monitoring, which are narrow, recurring, verifiable jobs.

### (c) Use dsh as the agent runtime
- Shape: replace Jangus's propose-then-execute loop (task 15) and directive runner (task 16) with dsh's agent loop; kernel becomes a dsh plugin intercepting `tools/pre-execute`.
- Phase gating: reverses merged/pending Phase 2b work; supersedes ADR-001; effectively a v3 redesign. Kernel: becomes a listener in someone else's waterfall where "listeners MUST call `next()`" and any plugin (incl. model-installed ones) can be mounted beside it; the kernel stops being the only actor. Dependencies: TypeScript runtime as the core; Python kernel reduced to a sidecar, or rewritten. Cost: must strip every model-facing exec tool, retry always-mode, subagent_oar; rebuild metering and caps inside Cordis. Safety: unaudited, pre-stable base for unattended operation. Violates rules 1, 7, 12 by default.

## 4. Recommendation

**Option (a): borrow ideas only.** Do not depend on dsh. Its design centre (interactive, model-driven loop with broad execution tools, advisory guards, UI approvals) is the opposite of Jangus's (deterministic kernel, narrow registered tools, hash-bound approvals, hard budgets). For file backup and shop monitoring, Jangus's directive + kernel path already covers what is needed; dsh adds risk and a second stack without adding a capability those jobs require.

Decisions that need the owner:
- **Owner approval:** confirm the goal statement above; accept or reject option (a); decide whether any borrowed idea is promoted from the parking lot (each at its own phase, no Phase 3 work now).
- **ADR required** if the owner wants (b) or (c): (b) adds a new runtime and an opaque T3 tool (new ADR-002, changes ARCHITECTURE and the tool registry rules); (c) supersedes ADR-001 and rules 1, 7, 12 and must update PRD decision log, ARCHITECTURE, PHASES and tasks together.
- No ADR needed for (a); individual ideas go through the normal PRD-log-then-task flow when their phase is authorized.

## 5. Not verified

- No dsh code was run, built or installed; no model call made. All claims come from docs, not behaviour.
- Did not read the full tool catalog (approx. 30 of ~60 tool entries skimmed), the `docs/subsystems/*`, `tool-execution-pipeline.md`, or package sources.
- Did not confirm whether `llm-retry` "fallback" can switch providers, or the exact approval UI semantics (hash binding, expiry).
- Did not check how `sandbox-windows-acl` behaves on the owner's Windows 11 PC.
- License (MIT) and third-party notices noted, not reviewed.
