# Phase 8 — optional advice and optimization trials (draft)

Date: October 23, 2026. Branch `integrations/08-consultation` is stacked
on unmerged `remote/07-vps-worker` (PR #7).

## Implemented provisionally

- Explicit owner-controlled consultation broker with fail-closed sharing flags,
  content hashes, strict advisory schema, allowed existing action IDs, and
  required local recovery exhaustion.
- Private SQLite reservation ledger with immutable session policy, call and
  estimated-token budgets, conservative micro-USD cost ceilings and evidence-
  only digests, not raw provider prompts or responses. Developer hardening adds
  a separate durable identity/generation anchor to reject missing/replaced or
  stale ledger state, and strictly per-call reviewer authorization bound to
  evidence and action scope. Astra additionally requires a matching persisted
  failed/unresolved Codex attempt, never a fabricated fallback flag.
- Manual Codex review adapter with **no SDK execution**; the current supported
  official Codex SDK was researched but not installed or activated.
- Separate Astra Responses API prototype pinned to current supported
  `gpt-6-astra`, with `tools: []`, `tool_choice: none`, `store: false`.
  The model is **not used** by the Jangus runtime and no API call occurred.
- Offline AST-based Graphify-style syntactic index and Headroom-style bounded
  critical-line selection experiment, both clearly distinct from the named
  third-party implementations. Developer hardening covers paths/IDs/hashes,
  rejection of malformed provider responses, and multi-fixture synthetic
  comparisons versus ordinary code search/full context, including estimated
  retrieval overhead. No live model performance claims.

Source contracts, unsupported capabilities and operator instructions are
documented in [OPTIONAL_INTEGRATIONS.md](../development/OPTIONAL_INTEGRATIONS.md).

## Verification and blockers

Final developer-only CI details are recorded in PR #8 after the hosted matrix completes. The code is default-off with no paid or SDK calls.

Taskmaster task 26 is pending until actual VPS operation is verified.
Consequently task 27 remains pending; optional tasks 28–30 remain deferred
and are not marked done. This is **not a completed Phase 8 release**.
The provider adapters are deliberately not connected to JangusConfig
features, the task controller or an automatic escalation path. Real account
access, paying for any request, optional Codex sandbox design, actual
comparative model tests and acceptance require separate owner authorization.

## External verification sources

- https://learn.chatgpt.com/docs/codex-sdk
- https://developers.openai.com/api/docs/models
- https://developers.openai.com/api/reference/resources/responses/methods/create
