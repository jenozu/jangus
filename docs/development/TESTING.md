# Development verification

Phase 2 tests cover installation, configuration parsing, path boundaries, and the CLI. Phase 3 adds actual Taskmaster MCP tests on disposable development tasks. They do not satisfy the runtime MVP scenarios.

## Reproducible environment

Use uv 0.12.23 and Python 3.12 or 3.13. `uv sync --locked` installs the checked-in resolution and fails if dependency declarations drift from it. The lock includes production and development dependencies from public PyPI; it contains no private index URL or credentials.

Hatchling is pinned in both the build requirements and development group. After syncing, `uv build --no-build-isolation` uses its locked dependencies. This is the verified build route; a separate generic pip build resolves isolated build dependencies independently.

Normal checks do not update the lock. Deliberate upgrades use `uv lock --upgrade-package NAME`, followed by diff review and the full matrix. Changing Python support or uv requires the same review.

## Commands

```console
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
uv run --locked python scripts/check_repository.py
uv run --locked jangus config validate --config config/jangus.example.yaml
uv build --no-build-isolation
uv run --locked python scripts/check_distribution.py
git diff --check
```

Repository checks use Git's tracked files. When adding a new linked document, stage it before running that check. Never use broad staging without reviewing the files.

The distribution script creates a temporary environment, exports production dependencies from the lock with hashes, installs them, installs the wheel without dependency resolution, then runs the CLI outside the source checkout. Remove obsolete Jangus wheels from `dist/` when intentionally changing the version; the script rejects ambiguous wheel selection.

## Covered cases

Tests reject unknown settings, string/boolean substitutions for strict scalar types, unsupported feature enablement, unregistered tools, invalid budgets, unsafe or ambiguous YAML, oversized files, and invalid/overlapping storage paths. CLI tests verify configuration selection and that synthetic secrets do not appear in errors. Symlink tests skip only when the host cannot create symlinks, and that skip is visible in pytest output.

Configuration checks are read-only. They do not prove that a future tool broker enforces permissions, that a path cannot change after validation, or that a local model is available.

## CI

The [workflow](../../.github/workflows/ci.yml) runs for pushes to project branches and pull requests. It has read-only repository permissions and no model, VPS, or deployment credentials. Third-party actions are pinned to reviewed commit SHAs.

The matrix covers Ubuntu 22.04 and Windows on Python 3.12 and 3.13, including locked install, lint/format, tests, document/ignore checks, example validation, build, isolated wheel verification, and OS health scripts. Record the actual workflow result on the phase PR. A configured workflow is not evidence of a successful run.

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
