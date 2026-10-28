# Phase 8: optional consultations and optimization experiments

Status: **provisional, disabled in the normal Jangus configuration**, not
deployed, not locally authenticated or evaluated on real tasks. No paid call
was made. Branch `integrations/08-consultation` is stacked on unmerged
Phase 7 PR #7.

## Supported integration facts reviewed October 23, 2026

The official Codex SDK documentation confirms a Python `openai-codex` SDK
and TypeScript `@openai/codex-sdk`, and notes Codex can read files/run
commands even in some read-only sandbox modes. This repository intentionally
**does not** install, spawn or grant those tools to a consultant. The current
Codex bridge is a **manual operator-supplied JSON review** passed to the
bounded broker, with no network, Code Interpreter, shell or editor access.
Automated Codex SDK integration requires separate authenticated desktop
sandbox and capability testing before connecting it.

The OpenAI API currently lists **`gpt-6-astra`**, standard API input
**$10 per million tokens**, output **$50 per million tokens**. The optional
`AstraTextOnly` prototype constructs a Responses API request with no tools,
`tool_choice: none`, `store: false`, a fixed model ID and a small output
bound. This code is **not** called by the normal Jangus runtime, and no
credentials belong in this repository. No subscription includes guaranteed
API billing access; an owner must verify access and explicitly approve an
external request and its cost before testing.

See original current references:
- https://learn.chatgpt.com/docs/codex-sdk
- https://developers.openai.com/api/docs/models
- https://developers.openai.com/api/reference/resources/responses/methods/create

## Advice-only protocol

`AdvicePolicy` defaults to **both providers disabled**, zero calls and zero
token/cost budgets. It is deliberately separate from
`JangusConfig.features`, which continues rejecting enabled paid features
in the installed local-MVP runtime. No controller, worker or recovery path
automatically invokes Phase 8.

A trusted operator must provide *all* of these to `AdviceBroker.consult`:

1. local recovery exhausted; explicit authorization for this particular call from a separate **trusted authorizer callback**, bound to the selected provider, immutable session, exact approved evidence-packet SHA and hashed allowed-action scope; an untrusted boolean alone is insufficient;
2. a selected, enabled provider (`codex` or `astra`), with Astra permitted
   only as a separate final choice after Codex was unresolved;
3. 1–6 source-revision-bound evidence packets, each explicitly marked
   `external-ok` and `redacted` by the trusted owner; 4-KiB packet limit;
4. a nonempty set of at most 20 **already owner-authorized action IDs**;
5. a private SQLite consultation session with fixed revision and concrete
   max calls, estimated token budget, micro-USD reservation ceiling and
   call timeout.

The broker reserves calls, input-token estimate, worst-case output bound and
a conservative cost *before* provider invocation. Reservations are not
refunded after failure or interruption. Records contain session/provider,
evidence digest, response digest and status, **not raw content**. A changed
policy revision is refused on resume. The ledger also maintains a separate private identity/generation anchor (schema v2); missing or rolled-back state fails closed. Its identity and generation must be preserved during backups, and it does not withstand an attacker replacing both the database and anchor. Earlier experimental schema v0 databases need explicit reviewed migration; never delete state to bypass protection. This limit scheme is intentionally
conservative, and does not claim to reflect actual invoices. Codex reserves
an equal portion of the operator-configured financial ceiling for each
permitted call, rather than assuming a subscription call is free. Provider token counts are **not measured**.

Codex `ManualCodexReview` is a practical safe bridge: it supplies the reviewed
JSON without programmatically contacting the external app. The Astra
prototype is available only by constructing it explicitly with a separately
stored credential and a reviewed network environment. A transport must honor
the passed timeout and response cap. The broker cannot forcibly terminate a
malicious or hung injected transport; true OS process isolation, anti-rollback
resistance against a fully privileged local attacker, and live financial
metering are not implemented. The trusted authorization callback is supplied by
the owner-controlled application layer, **not authenticated by the broker itself**.

The documented Python Codex SDK supports untrusted `ExternalMessage` and a
`Sandbox.read_only` preset, but `read_only` still allows file reading and the
SDK's local agent may execute commands. It is therefore **not attached** to
Jangus automatically or offered an agent-controlled invocation path. A separate
OS-restricted ephemeral workspace, explicit approval model and actual SDK
verification are prerequisites for safely replacing the manual bridge.


Valid advice is exactly `{"summary": "...", "action_ids": ["existing_id"]}`.
Unknown fields, additional IDs, invalid JSON and too-large text are rejected.
The broker **never calls a tool, executes advice, edits a task, changes a
budget or promotes an approval**. Existing controller verification/policy
remains responsible for every effect. A provider outage does not trigger
a different model automatically. A requested Astra consultation additionally needs an exact matching persisted **failed or unresolved Codex attempt** for the same session, evidence and authorized action scope. The `codex_unresolved` flag alone can never trigger Astra. A fresh, separate trusted per-call approval is mandatory. A Codex recommendation with no allowable action IDs is recorded as unresolved.

Redaction is an explicit owner-controlled admission boundary, not a guarantee
that marked text is safe. Do not send live logs, vault data, secrets, customer
records or private source to outside providers merely by setting a flag.

## Experimental Graphify-style navigation

`jangus.integrations.graphify` is a **stdlib AST baseline**, not the
third-party Graphify product and not an integration with any installed
Graphify service. It extracts Python declaration/import/unqualified-call
syntax with the provenance label `ast-extracted` and an exact source hash.
It does not infer dynamic calls, types, meaning or true execution dependencies.
A stale source revision prevents navigation. Compare it to ordinary literal
search before considering use in real tasks.

## Experimental Headroom-style compaction

`jangus.integrations.headroom` is a **local line-preserving trial**, not
the third-party Headroom product. It keeps complete lines containing explicit
required strings, recognized MUST/NEVER/ERROR markers, and common hash/path/ID
patterns, then fills remaining
bytes in original order. It returns the full source SHA-256 and evidence
reference; the original is never overwritten. If critical lines exceed the
budget it fails closed instead of shortening paths, limits or IDs. Token
numbers are UTF-8 bytes/4 **estimates**, not a real tokenizer measurement.
Non-adjacent lines may be omitted; the caller must fetch the original by
reference for precise reconstruction.

Use only synthetic evidence when benchmarking:

```console
uv run --locked pytest tests/integrations
uv run --locked python scripts/benchmark_optional_integrations.py
uv run --locked pytest
```

That script compares a fixed set of synthetic Python source fixtures, including a
literal-search false positive, stale-revision refusal, and several bounded context
fixtures. It reports median local timings over repeated runs, protected-line
retention, approximate token counts, and **original-plus-retrieval** estimates
where the original must also be fetched. It is
**not** evidence of correctness, latency, total token savings, cost, or memory
improvement in production. To adopt either optimization, define a real fixed
baseline and compare success/accuracy, full tokens including retries and
retrieval, end-to-end latency, process peak memory, stale-index behavior
and allowed disclosure against unchanged ordinary search/full context.

## Pending acceptance

Taskmaster 26 remains pending because the real VPS gate is untested; thus
task 27 (Codex) is pending and optional tasks 28–30 remain deferred.
Real desktop Codex authentication, bounded SDK sandbox, Astra account
access/cost, real permitted-evidence review, measured tokens, and useful
Graphify/Headroom comparative studies remain pending. Do not mark this
phase fully released, wire these transports to automatic runtime decisions,
merge PRs, or authorize a paid test based only on synthetic CI.

## Disclosure scanner and approval packet

`pack_evidence` no longer trusts the caller-supplied `redacted` flag alone. Each
item's text and reference also pass a deterministic scan
(`jangus.integrations.advice.scanner`) for private keys, common token shapes,
credential assignments, URL credentials, home-directory paths and long hex
strings. Any hit fails with `evidence_unsafe`; the error never echoes content.
This is a deny-list, not proof of safety. `ApprovalRequest.packet` carries the
exact text that will be sent, so the approval UI must show it verbatim before
the owner approves.
