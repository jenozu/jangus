# ADR-002: Taskmaster MCP and isolated assignments

Status: accepted design. Date: 2026-10-22.

## Context

Development needs dependencies, small assignments, and evidence-based progress. Runtime jobs also need state, but their action attempts are different from development tasks.

## Decision

Use Taskmaster through MCP as the development backlog beginning in Phase 3. Pin a tested version and begin with a minimal supported tool selection. Verify the installed version's configuration and local provider compatibility before relying on it. Model-backed planning follows the same local-first and no-paid-fallback rules.

A narrow adapter exposes task retrieval, ready-work lookup, progress, and verified completion. Link development task IDs to run IDs. The controller remains the authority for live jobs and recovery.

Adopt explicit child assignments, isolated contexts, and evidence-linked return summaries inspired by Roo's Boomerang approach. Do not add Roo as a required runtime or assume context isolation eliminates misleading instructions.

## Alternatives

A separate hand-maintained backlog duplicates Taskmaster status. Loading all MCP tools by default may waste context. If measurements justify change, first reduce the tool set or use the CLI through the same adapter. A custom task store is a later decision, not a simultaneous second authority.

## Consequences and verification

Phase 1 uses the authorized phase/PR as its assignment because Taskmaster does not yet exist. Phase 3 imports reviewed requirements into one backlog and tests dependencies/status changes. Measure tool-schema overhead and planning usage. Completion requires evidence; an MCP status update alone cannot certify work.

Graphify remains an optional source-revision-aware code-navigation experiment. Headroom remains optional context compression tested against retained permitted evidence. Neither is required for the baseline or substitutes for verification.
