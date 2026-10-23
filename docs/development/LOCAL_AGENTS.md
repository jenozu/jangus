# Phase 5 local agent components (in progress)

Phase 5 builds on the verified Phase 4 file controller. **Hardware benchmark and A09–A13 acceptance are pending.** This code is intentionally a constrained file-workflow integration, not a general-purpose agent or Windows application controller.

## What exists

- `jangus.agents.provider.OllamaProvider`: explicit **loopback-only** Ollama JSON requests, 64 KiB response bound, per-instance single inference semaphore, capped request timeout, structured failures, no cloud fallback. Cancellation is checked before/after a blocking request and cannot interrupt an in-flight HTTP call immediately. Ollama token counts are `provider_reported`, not locally measured.
- `jangus.agents.workflow.run_verified_workflow`: creates a fresh, isolated two-message model context with the reviewed rules and selected reviewed skills. The model sees canonical tool/action IDs, dependencies, rule/skill revisions and limits, not input file contents or paths. It can only return **the exact owner-compiled action order**; an invalid proposal is cancelled before file execution. Independent completion always comes from the existing controller verifier.
- `jangus.agents.skills.SkillRegistry`: explicit allowlist with Git blob revision pinning, SHA-256 revisions, limited file sizes, link checks, selective loading and a check that skill tool requirements fit controller permissions. Text cannot grant permissions. Reviewing an edited procedure requires updating its catalog pin in a separate reviewed change.
- `jangus.agents.context`: bounded excerpts with exact SHA-256 reference and bytes/4 **estimates**, never mislabeled tokenizer measurements. Private atomic handoffs record verified action state, cancellation and revisions; reopening re-reads controller SQLite status and **never restores counters** from the summary.
- Five initial reviewed `SKILL.md` procedures for file execution, failure diagnosis, diagnostics, handoff and drafting a *non-approved* lesson. Obsidian approval/retrieval is Phase 6.

Simulated provider tests check invalid/altered plans, rejected unreviewed skills, permission expansion, revision changes, bad model envelopes, no model, cancellation, output bounds and handoff tampering. These are **software checks**, not a local-model hardware benchmark, not real autonomous repair, and not a separate worker process. The loopback provider is opt-in and does not run during CI. All side effects still use Phase 4 registered file tools.

## Manual local demonstration (later, on the desktop)

Complete the [benchmark and model selection](LOCAL_MODEL_BENCHMARK.md) first, then prepare a private synthetic fixture using [the execution guide](EXECUTION.md). Edit its private config to set `model.name` to the actually installed Ollama tag; keep endpoint at `http://127.0.0.1:11434`, `max_concurrent_inference: 1`, `codex_enabled: false` and `astra_enabled: false`.

From a repo checkout, for a **new fixture root**:

```powershell
uv run --locked python scripts/run_agent_fixture.py --config "$demoRoot/config.yaml" --workspace "$demoRoot/workspaces/demo" --manifest "$demoRoot/manifest.json"
```

Do not re-run against the same output directory; exclusive creation prevents overwrites. Check the actual returned run state, action verification receipts and the private handoff under the configured data directory. Record hardware model tag/digest, resource use, latency, and output validity as separate owner-machine evidence before claiming A09/A17. Do not send diagnostic backups or prompt contents to external services.

## Known boundaries

No automatic model selection, autonomous arbitrary plan execution, preemptive inference cancellation, separate OS worker sandbox, Taskmaster AI planning, Obsidian integration, VPS integration, or paid consultant integration is provided. Stable Phase 4 state and budgets are authoritative. Handoff snapshots are not recovery authorization. The task-12 benchmark remains the prerequisite for Taskmaster tasks 13–17 to be marked complete, even though their code can be tested provisionally in CI.
