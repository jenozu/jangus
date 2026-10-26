# Jangus

Jangus is a planned self-hosted agent system for coordinating bounded tasks across a Windows desktop and an Ubuntu VPS. A local model plans and delegates; ordinary controller code manages tools, permissions, durable state, verification, and recovery.

## Current status

**Eight phases are implemented as stacked, unmerged draft PRs.** Phases 1–4 passed their stated software checks. Phases 5–8 include synthetic end-to-end tests and developer-side hardening, **not** real Windows Ollama, live Obsidian or VPS acceptance. Taskmaster task 12 is in progress and tasks 13–27 depend on owner hardware/access/acceptance; optional tasks 28–30 remain deferred. This release-readiness checkpoint adds cross-phase rehearsal and read-only handoff checks. No paid provider or live infrastructure was accessed.

| Capability | Status |
|---|---|
| Product requirements, MVP acceptance criteria, architecture, working rules | Documented |
| Package, dependency lock, configuration validation, setup guides, CI workflow | Implemented: Phase 2; verification recorded on the phase PR |
| Taskmaster MCP development workflow | Implemented: Phase 3; four offline tools and reviewed backlog |
| Logging, deterministic tools, reliable execution | Implemented: Phase 4; synthetic workflow and A01–A08 checks |
| Local agents, skills, context handoffs | Phase 5: role coordinator CI-tested; real Windows Ollama benchmark/acceptance pending |
| Approved Obsidian memory | Phase 6: review CLI and approved-memory retrieval CI-tested; live Obsidian approval pending |
| Durable VPS jobs | Phase 7: hardened durable private worker CI-tested; live VPS approval pending |
| Optional consultation and optimization | Phase 8: bounded default-off advice and synthetic baselines; external provider access not accepted |

## How it will work

1. Receive a task with a defined workspace, expected result, permissions, and budgets.
2. Ask a self-hosted model for a structured plan.
3. Give workers focused assignments in separate contexts.
4. Execute registered tools through controller permission checks.
5. Verify actual outputs and persist evidence.
6. Recover within the original limits, or stop with useful diagnostics.
7. Optionally retrieve approved knowledge and propose lessons for human review.

Multiple roles can share one model. Start with one active text inference at a time; choose models and desktop/VPS placement through benchmarks. Vision is an on-demand later capability.

Taskmaster MCP manages the **development backlog**. The Jangus controller manages **live executions**. Skills provide reviewed procedures; Obsidian provides curated knowledge. Neither grants execution authority.

SSH is the planned administration channel, with a verified SSH tunnel to a private loopback-bound VPS job service once deployment is explicitly authorized. GitHub Actions will serve tests and later deployment. Codex consultation is optional and advice-only; a final Astra API fallback remains disabled by default and conditional on a supported integration.

## Start here

With Python 3.12 or 3.13 and uv 0.12.23 available, run from the checkout:

```console
uv sync --locked
uv run --locked jangus --version
uv run --locked jangus config validate --config config/jangus.example.yaml
```

Validation creates no runtime directories and contacts no service. Use the OS guide for installation and private configuration:

- [Windows PowerShell setup](docs/setup/WINDOWS.md)
- [Linux/Ubuntu setup](docs/setup/LINUX.md)
- [Configuration contract](config/README.md)
- [Development checks and dependency updates](docs/development/TESTING.md)
- [Taskmaster installation, MCP configuration, and workflow](docs/development/TASKMASTER.md)
- [Run the synthetic file workflow, recover, and export diagnostics](docs/development/EXECUTION.md)
- [Phase 5 local agent integration and limits](docs/development/LOCAL_AGENTS.md)
- [Target-machine Ollama benchmark](docs/development/LOCAL_MODEL_BENCHMARK.md)

Project specifications:

- [Product requirements](docs/product/PRD.md)
- [MVP and acceptance scenarios](docs/product/MVP.md)
- [System architecture and intended folder structure](docs/architecture/SYSTEM.md)
- [Architecture decisions](docs/architecture/decisions/README.md)
- [Phased delivery plan](docs/planning/PHASES.md)
- [Shared project rules](rules.md)
- [Coding-agent entry point](AGENTS.md)
- [Contribution and verification workflow](docs/development/CONTRIBUTING.md)
- [Phase 1 review](docs/reviews/PHASE_1.md)
- [Phase 2 review](docs/reviews/PHASE_2.md)
- [Phase 3 review](docs/reviews/PHASE_3.md)
- [Phase 4 review](docs/reviews/PHASE_4.md)
- [Phase 5 provisional review](docs/reviews/PHASE_5.md)
- [Phase 6 Obsidian memory design, limitations, and synthetic testing](docs/development/MEMORY.md)
- [Phase 6 provisional verification record](docs/reviews/PHASE_6.md)
- [Phase 7 private remote worker protocol and setup](docs/development/REMOTE_WORKER.md)
- [Phase 7 provisional verification record](docs/reviews/PHASE_7.md)
- [Phase 8 optional advice, Codex/Astra restrictions and optimization baselines](docs/development/OPTIONAL_INTEGRATIONS.md)
- [Phase 8 provisional review](docs/reviews/PHASE_8.md)
- [Owner acceptance and release handoff](docs/development/ACCEPTANCE_HANDOFF.md)
- [Cross-phase readiness checkpoint review](docs/reviews/RELEASE_READINESS.md)

Use `uv run --locked python scripts/check_release_readiness.py` to read (not change) the Taskmaster gate states. It does not replace real owner testing. The implemented execution foundation is a synthetic file workflow with durable logs and independent verification. The full local MVP adds a local model, context handoffs, reviewed skills, and approved memory through Phase 6. VPS execution is a subsequent milestone.

## Data and scope

Source, documentation, sanitized configuration examples, and templates belong in Git. Credentials, live vault notes, operational databases, logs, task inputs, downloads, model weights, and diagnostic exports belong outside the checkout.

The public repository must contain synthetic examples only. No license is selected in this phase; license choice is an owner decision before reuse or distribution terms are published.
