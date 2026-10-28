# Development verification

Tests are grouped by phase:

- **Phase 2:** installation, configuration parsing, path boundaries, and the CLI.
- **Phase 3:** actual Taskmaster MCP tests on disposable development tasks.
- **Phase 4:** executable A01–A08 scenarios under `tests/execution/`.
- **Phase 5:** provisional simulated-provider, skill and handoff checks under `tests/agents/`. Real A09–A13 and integrated A14–A17 remain pending.
- **Phase 6:** synthetic approval, retrieval and proposal checks under `tests/memory/`.
- **Phase 7:** authenticated loopback transport, private SQLite job and restart-reconciliation checks under `tests/remote/`. Hardening tests additionally verify identity anchors, immutable terminal client receipts, durable audit, uncertain-output cancellation, read-only service preflight and actual loopback service/client object restart. No actual VPS testing is claimed.
- **Phase 8:** default-off advice-contract and synthetic AST/context comparison tests under `tests/integrations/`. They include revision-bound trusted reviewer gates, persistent session identity, no-reset cost/call budgets, exact matching Codex-to-Astra fallback evidence, and multiple offline baseline fixtures.

A post-Phase-8 release-readiness checkpoint adds `tests/release/` with a fully synthetic integration rehearsal and read-only Taskmaster dependency validator. No real owner-machine acceptance is asserted; see [ACCEPTANCE_HANDOFF.md](ACCEPTANCE_HANDOFF.md).

## Reproducible environment

Use uv 0.12.23 or a later 0.12.x release (CI pins 0.12.23; `pyproject.toml` accepts `>=0.12.23,<0.13`) and Python 3.12 or 3.13. `uv sync --locked` installs the checked-in resolution and fails if dependency declarations drift from it. The lock includes production and development dependencies from public PyPI; it contains no private index URL or credentials.

Hatchling is pinned in both the build requirements and development group. After syncing, `uv build --no-build-isolation` uses its locked dependencies. This is the verified build route; a separate generic pip build resolves isolated build dependencies independently.

Normal checks do not update the lock. Deliberate upgrades use `uv lock --upgrade-package NAME`, followed by diff review and the full matrix. Changing Python support or uv requires the same review.

## Commands

```console
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
uv run --locked python scripts/check_release_readiness.py --check foundation
uv run --locked python scripts/check_repository.py
uv run --locked jangus config validate --config config/jangus.example.yaml
uv build --no-build-isolation
uv run --locked python scripts/check_distribution.py
git diff --check
```

Repository checks use Git's tracked files. When adding a new linked document, stage it before running that check. Never use broad staging without reviewing the files.

The distribution script creates a temporary environment, exports production dependencies from the lock with hashes, installs them, installs the wheel without dependency resolution, then runs configuration validation and the verified synthetic copy/report workflow outside the source checkout. Remove obsolete Jangus wheels from `dist/` when intentionally changing the version; the script rejects ambiguous wheel selection.

## Covered cases

Tests reject unknown settings, string/boolean substitutions for strict scalar types, unsupported feature enablement, unregistered tools, invalid budgets, unsafe or ambiguous YAML, oversized files, and invalid/overlapping storage paths. CLI tests verify configuration selection and that synthetic secrets do not appear in errors. Symlink tests skip only when the host cannot create symlinks, and that skip is visible in pytest output.

Configuration checks are read-only. Execution tests separately exercise registered tools, permissions, real file verification, crash recovery, cancellation, SQLite failures, and privacy. These tests do not demonstrate a local model or hostile-user OS sandbox.

Run `uv run --locked pytest tests/execution` for the execution slice; see [EXECUTION.md](EXECUTION.md) for boundaries and [Phase 4 review](../reviews/PHASE_4.md) for evidence.

## Phase 5 provisional checks

Run `uv run --locked pytest tests/agents` for the mock-provider and reviewed-context integration tests. These tests never download or contact a model. See [LOCAL_AGENTS.md](LOCAL_AGENTS.md) for the separate Windows hardware gate and remaining limitations.

## Phase 6 synthetic memory checks

Run `uv run --locked pytest tests/memory`; these tests use private temporary vaults and registries, never a live Obsidian vault. See [MEMORY.md](MEMORY.md) for exact-byte approvals, audit and anti-rollback limits. Dependency Taskmaster statuses remain pending until Phase 5's required local hardware gate is completed.

## Phase 7 remote worker synthetic checks

Run `uv run --locked pytest tests/remote` for authenticated localhost HTTP, versioned job schemas, durable idempotency, lost acknowledgement, cancellation, restart, and result integrity. See [REMOTE_WORKER.md](REMOTE_WORKER.md) for the separate actual SSH/VPS acceptance gate. GitHub Actions is not the runtime job transport.

## Phase 8 default-off integration checks

Run `uv run --locked pytest tests/integrations` and `uv run --locked python scripts/benchmark_optional_integrations.py`. Synthetic mocked providers make no real Codex or paid API calls; generated token figures are estimates, not production benchmarks. See [OPTIONAL_INTEGRATIONS.md](OPTIONAL_INTEGRATIONS.md).

## CI

The [workflow](../../.github/workflows/ci.yml) runs for pull requests and for pushes to `main` (pull-request runs cover feature branches, so a branch push is not run twice). It has read-only repository permissions and no model, VPS, or deployment credentials. Third-party actions are pinned to reviewed commit SHAs.

The matrix covers Ubuntu 22.04 and Windows on Python 3.12 and 3.13, including locked install, lint/format, tests, document/ignore checks, example validation, build, isolated wheel verification, and OS health scripts. Record the actual workflow result on the phase PR. A configured workflow is not evidence of a successful run.

## Dependency audits

On Ubuntu with Python 3.13 the workflow also runs `pip-audit` against the locked Python dependencies, and the Taskmaster job runs `npm audit --audit-level=high` against the npm lockfile. Both steps are non-blocking (`continue-on-error`): they surface advisories in the job log without failing the build, because most npm advisories arrive through the development-only `task-master-ai` tree. Review the output when it changes; accepted risks belong in the pull request description.

## Taskmaster checks

Use Node 24.19.0 and npm 11.9.0:

```console
npm ci --ignore-scripts --no-audit --no-fund
npm run tasks:check
npm run test:taskmaster
```

The separate two-OS Taskmaster job verifies the exact four-tool MCP contract,
status persistence, dependencies, evidence rejection, safe configuration, and
unchanged canonical tasks. Together with the four Python jobs, CI has six jobs.
The tests make no model calls and need no provider credentials. See
[TASKMASTER.md](TASKMASTER.md) for the adapter contract and dependency updates.

## References

Implementation choices were checked against [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/), [uv GitHub Actions guidance](https://docs.astral.sh/uv/guides/integration/github/), [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/), and [PyYAML documentation](https://pyyaml.org/wiki/PyYAMLDocumentation). The repository's pinned lock and tests define the tested dependency set.
