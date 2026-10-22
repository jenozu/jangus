# Jangus

Jangus is a planned self-hosted agent system for coordinating bounded tasks across a Windows desktop and an Ubuntu VPS. A local model plans and delegates; ordinary controller code manages tools, permissions, durable state, verification, and recovery.

## Current status

**Phase 1: specifications and repository rules only.** This branch contains documentation. There is no installable package, CLI, model integration, Taskmaster connection, memory service, or remote worker yet. No desktop or VPS runtime tests have been performed.

| Capability | Status |
|---|---|
| Product requirements, MVP acceptance criteria, architecture, working rules | Documented |
| Package, dependency locks, configuration, CI | Planned: Phase 2 |
| Taskmaster MCP development workflow | Planned: Phase 3 |
| Logging, deterministic tools, reliable execution | Planned: Phase 4 |
| Local agents, skills, context handoffs | Planned: Phase 5 |
| Approved Obsidian memory | Planned: Phase 6 |
| Durable VPS jobs | Planned: Phase 7 |
| Optional consultation and optimization | Planned: Phase 8 |

## How it will work

1. Receive a task with a defined workspace, expected result, permissions, and budgets.
2. Ask a self-hosted model for a structured plan.
3. Give workers focused assignments in separate contexts.
4. Execute registered tools through controller permission checks.
5. Verify actual outputs and persist evidence.
6. Recover within the original limits, or stop with useful diagnostics.
7. Optionally retrieve approved knowledge and propose lessons for human review.

Multiple roles can share one model. Start with one active text inference at a time; choose models and desktop/VPS placement through benchmarks. Vision is an on-demand later capability.

Taskmaster MCP will manage the **development backlog**. The Jangus controller will manage **live executions**. Skills provide reviewed procedures; Obsidian provides curated knowledge. Neither grants execution authority.

SSH will serve administration, with an SSH tunnel to a private VPS job service. GitHub Actions will serve tests and later deployment. Codex consultation is optional and advice-only; a final Astra API fallback remains disabled by default and conditional on a supported integration.

## Start here

There are no installation commands in this phase. Review these documents before implementation:

- [Product requirements](docs/product/PRD.md)
- [MVP and acceptance scenarios](docs/product/MVP.md)
- [System architecture and intended folder structure](docs/architecture/SYSTEM.md)
- [Architecture decisions](docs/architecture/decisions/README.md)
- [Phased delivery plan](docs/planning/PHASES.md)
- [Shared project rules](rules.md)
- [Coding-agent entry point](AGENTS.md)
- [Contribution and verification workflow](docs/development/CONTRIBUTING.md)
- [Phase 1 review](docs/reviews/PHASE_1.md)

The first execution milestone is a synthetic file workflow with durable logs and independent verification. The full local MVP adds a local model, context handoffs, reviewed skills, and approved memory through Phase 6. VPS execution is a subsequent milestone.

## Data and scope

Source, documentation, sanitized configuration examples, and templates belong in Git. Credentials, live vault notes, operational databases, logs, task inputs, downloads, model weights, and diagnostic exports belong outside the checkout.

The public repository must contain synthetic examples only. No license is selected in this phase; license choice is an owner decision before reuse or distribution terms are published.
