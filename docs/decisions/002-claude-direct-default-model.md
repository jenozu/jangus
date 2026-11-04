# ADR-002: Claude Haiku (direct Anthropic API) is the default cheap model; local models become optional

Status: ACCEPTED by the repository owner on 2026-11-04. Amends ADR-001 items 5, 9 and 11 and its rule amendment R01. ADR-001 keeps its own history: nothing in it is rewritten.

## Context

ADR-001 assumed a hybrid: a local Ollama model on the owner's PC for routine steps, with the Claude API for orchestration and hard judgment. Placement put "local model on the PC" and "local model on the VPS" below the Haiku rung, and Phase 2a's exit gate was a benchmark on the owner's PC that would pick the default cheap model.

The owner's PC runs Ollama models too slowly for routine work. The benchmark (task 17) was therefore never going to select a usable local default, and the whole plan was waiting on a measurement that could not change the answer.

OpenRouter was raised as an alternative route to cheap models and considered as part of this decision.

## Decision

1. **The default cheap model is Claude Haiku, called through the direct Anthropic API.** Specialists' `runner: model` tasks use it unless a spec names something else.
2. **Escalation is unchanged:** the single step-up is Sonnet at low effort (`escalation_model` in the spec), and the orchestrator model is as designed in ADR-001 item 9.
3. **Local Ollama stays in the code as an optional provider, off by default.** The provider, `scripts/benchmark_ollama.py` and their tests are kept. Enabling a local model is explicit owner config (an endpoint plus `ollama_enabled`), exactly as the paid provider needs `claude_enabled`.
4. **No silent provider fallback (ADR-001 item 5, R11) is unchanged.** A failing or capped provider still means stop and alert. Nothing switches between Claude and a local model on its own, in either direction.
5. **Placement ladder becomes:** kernel-run (no model) → Haiku → Sonnet at low effort → orchestrator tier. A local model on the PC or on the VPS is an optional rung the owner may insert below Haiku once a machine can run one fast enough; it is not part of the default ladder.
6. **Zero-token-first is unchanged** and now carries more weight, because the cheap rung is no longer free: recipes, `runner: kernel` tasks, watchers, rules-before-models and the result cache all come before any model call. The Phase 3 backup agent remains `provider: none` and calls no model at all.
7. **OpenRouter is rejected for now** and parked. Reasons: it needs a new provider adapter; its upstream routing and fallback would have to be disabled to satisfy the no-silent-fallback rule, which removes most of its appeal; shop and email data would pass through a third party in addition to the model vendor; and none of this was checked against OpenRouter's current documentation.

## Consequences

- **Privacy:** routine work is no longer local. Shop order data and, in Phase 6, email text leave the PC and go to Anthropic. This is an accepted trade-off, recorded here and in MEMORY.md, and it must be weighed again before the inbox agent is pointed at a real mailbox. An owner who wants a job kept on the machine must enable the optional local provider for that agent.
- **Cost:** the cheapest model rung now costs money, so routine work cannot be assumed free. `[caps]`, the price table and the pre-call gate (docs/TOKEN_ECONOMY.md, docs/COST_SETUP.md) are required before any specialist runs, not just before orchestration. Phase 3b's token measurements become the first real cost signal.
- **Availability:** with no local model enabled, there is no degraded local-only mode for `runner: model` work. Kernel-run tasks, recipes and watchers still work offline; V2-NFR06 now reads as "T1 kernel work continues with the cloud provider disabled".
- **Phase 2a's gate** (benchmark picks the default cheap model) becomes optional; it is satisfied by this decision. The benchmark is kept as a tool for a future machine, not a blocker. See docs/PHASES.md for exactly what changed.
- **No finished task is edited.** Tasks 13, 17 and 25 stay as the dated record of what was built; corrective tasks 41 and 42 carry the change.

## Alternatives rejected

- **Keep waiting for a local default** (the owner's hardware does not support it).
- **OpenRouter or another aggregator** (see decision 7).
- **Remove the Ollama provider** (it costs nothing to keep, is already tested, and a future machine or VPS may make it the cheap rung again).
