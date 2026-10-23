# Minimum viable product

Status: acceptance specification. Phase 4 A01–A08 implementation and executed checks are recorded in [the phase review](../reviews/PHASE_4.md). Later scenarios remain pending.

## Two milestones

**Execution foundation (Phase 4):** a deterministic synthetic file task can run, be verified, recover from interruption, and produce useful diagnostics. This is the first operational slice, not the complete local agent MVP.

**Local agent MVP (through Phase 6):** add local-model planning and recovery, isolated workers, reviewed skill loading, context handoffs, and approved Obsidian retrieval/proposals. It must operate with Codex and Astra disabled. VPS execution is a separate Phase 7 gate.

## Reference workload

Use only synthetic files under a dedicated workspace. A manifest specifies input names and expected hashes. The task copies selected text files to a new output directory and writes a manifest report through registered tools.

1. Validate the assignment, workspace, budgets, and expected outputs.
2. Plan locally (or use a deterministic fixture for the Phase 4 milestone).
3. Persist intent, then copy without overwriting unrelated files.
4. Independently compare destination bytes/hashes to the trusted input manifest.
5. Create and verify the report, recording artifact hashes and evidence IDs.
6. If an action fails, diagnose and retry only within persisted limits.
7. Inspect status and export redacted diagnostics.
8. For the full MVP, retrieve an approved procedure, roll over one session, and propose a lesson as a draft.

Tests use known synthetic expectations; the model cannot select a weaker definition of success. No real task data is required.

## Acceptance scenarios

| ID | Scenario | Pass condition | Phase |
|---|---|---|---|
| A01 | Normal synthetic workflow | Expected outputs and report verified; intent/result/verification events correlate | 4 |
| A02 | Invalid action or workspace escape | Unknown tools, traversal, and symlink/junction escapes rejected before effects | 4 |
| A03 | Incorrect result | Deliberately corrupted output fails verification; no completed status | 4 |
| A04 | Transient failure and exhausted recovery | Permitted transient failure recovers; persistent failure stops within total limits | 4 |
| A05 | Crash before/after side effect | Restart reconciles persisted intent with actual artifacts; no blind duplicate or overwrite | 4 |
| A06 | Cancellation and uncertain outcome | No new work after cancellation; in-flight effects reconciled or explicitly unresolved | 4 |
| A07 | Persistence unavailable | Logging/state failure blocks new side effects and exposes a useful error | 4 |
| A08 | Diagnostic privacy | Synthetic secrets excluded; summary, events, permitted artifacts, and omissions manifest remain useful | 4 |
| A09 | Local planning | Real local model produces a validated plan and completes the reference workload with consultants disabled | 5 |
| A10 | Isolated assignments | Child receives objective, relevant context, scope, skill refs, success checks, and budgets; returns evidence-linked result | 5 |
| A11 | Rules and skills | Relevant reviewed skill and mandatory rules loaded; exact revisions logged; attempted permission expansion rejected | 5 |
| A12 | Context rollover | Verified progress, failures, uncertain actions, scope, and all remaining budgets survive the handoff | 5 |
| A13 | Context accounting | Oversized tool output bounded; exact critical evidence retrievable; estimates are not labelled measurements | 5 |
| A14 | Approved memory | Applicable approved revision can be used; selected hash and evidence logged | 6 |
| A15 | Edited or ineligible memory | Changed, draft, archived, out-of-scope, or disallowed-sharing notes excluded, including linked notes | 6 |
| A16 | Proposals and conflicts | Repeated proposal does not duplicate; user edits preserved; no automatic approval | 6 |
| A17 | Memory unavailable | Missing/corrupt approval registry disables retrieval; run continues without optional memory or pauses if required | 6 |

A05 must cover termination before execution, after the effect but before result persistence, and after verification. A12 must include an exhausted retry budget so a fresh session cannot revive it. A15 must recheck the selected revision immediately before prompt inclusion.

## Later gates

Phase 7 must demonstrate the same remote job identity across a lost submission acknowledgement, disconnect, service restart, and status/result reconciliation. Reusing an idempotency key with different content must fail. Cancellation does not imply an already completed effect was undone.

Phase 8 must show that consultants cannot execute tools, bypass policy, reset budgets, or send disallowed content. Disabled integrations must produce no outbound requests. Graphify/Headroom trials must compare total tokens including retries/retrieval, verified accuracy, latency, and memory against the unmodified baseline.

## Evidence and release decision

For each scenario record the code revision, environment, configuration/rules/skill versions, run ID, expected result, actual result, evidence references, and pass/fail/pending status. Use simulated providers for deterministic fault coverage, and identify those results separately from actual local-model or VPS checks.

The repository owner accepts the local MVP only after all A01–A17 scenarios pass and the Windows local-model demonstration is performed. Hardware-dependent checks remain pending until actually executed. Phase 1 documentation review cannot satisfy runtime acceptance.
