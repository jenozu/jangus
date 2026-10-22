# Phase 1 design review

Date: 2026-10-22. Scope: documentation foundation only.

## Inputs and precedence

The supplied Repo-Setup-Template and the latest project discussion define this phase. Earlier Astra-centered plans are background; the later self-hosted orchestration decision supersedes them. The prior Obsidian design's exact-revision approval, privacy, conflict handling, and diagnostic requirements are preserved without importing private source documents.

The remote repository was inspected and found empty. A minimal README bootstrap establishes `main`; the phase changes live on `foundation/01-specifications`.

## Design comparison

| Agreed requirement | Documented location | Review result |
|---|---|---|
| Lowercase `rules.md` with explicit agent entry point | Root rules and AGENTS | Included |
| Local orchestration; roles can share a model | SYSTEM and ADR-001 | Included |
| No cloud dependency; Astra disabled by default | PRD, MVP, ADR-001 | Included |
| Taskmaster MCP owns only development backlog | SYSTEM and ADR-002 | Included; setup deferred to Phase 3 |
| Isolated worker assignments without Roo dependency | SYSTEM and ADR-002 | Included |
| Explicit skill loading and revision tracking | Rules, SYSTEM, ADR-003 | Included; implementation deferred |
| Logging before autonomous effects | Rules, SYSTEM, ADR-004 | Included |
| Independent verification and uncertain-action reconciliation | MVP and SYSTEM | Included |
| Retry budgets survive sessions/restarts/revisions | Rules and MVP A04/A12 | Included |
| External vault with numbered template folders | SYSTEM | Included; templates deferred to Phase 6 |
| Exact-hash approval, review, and edit invalidation | SYSTEM and MVP A14–A17 | Included |
| SSH administration plus durable private jobs | SYSTEM and ADR-004 | Included |
| GitHub Actions separate from runtime communication | SYSTEM and ADR-004 | Included |
| Graphify/Headroom optional and measured | PRD, MVP, ADR-002 | Included |
| Actual machine checks distinguished from simulation | README, MVP, CONTRIBUTING | Included |
| No competing manually maintained task list | PRD and PHASES | Included |

## Boundary reconciliation

Earlier outlines used “MVP” for both a small execution demo and the fuller memory-enabled system. The documents now name the Phase 4 execution foundation separately from the local agent MVP through Phase 6. VPS execution and optional consultation are later gates.

Root `skills/` and the loader under `src/jangus/skills/` have separate purposes. The detailed numbered Obsidian layout takes precedence over the earlier simplified folder sketch. The lowercase rules filename is consistent throughout.

## Verification record

The submission checks include relative-link and case validation, whitespace validation, full-document/diff review, and inspection for credentials and personal/runtime data. The PR records the actual check results for the submitted revision.

No runtime tests, package installation, CI run, MCP handshake, model benchmark, Windows demonstration, or VPS validation is claimed. Those remain pending their implementation phases. No live vault, source planning attachments, credential material, or operational logs are part of this change.
