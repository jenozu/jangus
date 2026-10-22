# Contributing

Read [AGENTS.md](../../AGENTS.md) and [rules.md](../../rules.md) first. Use the [phase map](../planning/PHASES.md) to keep changes within the authorized scope.

## Current development state

Phase 2 provides an installable Python package, configuration CLI, locked dependencies, tests, and CI. Follow [TESTING.md](TESTING.md) for the verified commands. Taskmaster MCP and the development backlog arrive in Phase 3; live agent execution remains planned.

Do not report an installation, model workflow, or remote job as tested based on a document review.

## Change workflow

1. Inspect existing instructions, branch state, and pending changes.
2. Identify the authorized phase or, after Phase 3, the Taskmaster assignment and acceptance criteria.
3. Read applicable requirements, decisions, and reviewed skills when present.
4. Make a focused branch and scoped changes. Preserve other contributors' work.
5. Run checks that address the changed behavior; record the environment and limitations.
6. Review the full diff and update documentation that would otherwise become inaccurate.
7. Push and prepare a PR describing the problem, changes, verification, pending checks, and base.
8. Mark a development task complete only after its criteria have evidence. Keep PR merge status separate.

Use small commits and synthetic fixtures. Do not add an arbitrary license. Keep secrets, task data, live notes, logs, database files, machine identifiers, and private configuration outside the checkout. Stage explicit files and inspect what is staged; an ignore rule is not evidence that all sensitive content is excluded.

## Documentation-only verification

For documentation changes:

- Check Markdown relative links against tracked paths, including filename case.
- Confirm `AGENTS.md` references lowercase `rules.md`.
- Review requirements, MVP gates, architecture, and decision records together.
- Confirm planned directories and commands are not described as installed functionality.
- Inspect the full diff for secret-like strings, personal content, unsupported guarantees, and stale Astra-first assumptions.
- Run `git diff --check` on the proposed changes.

Markdown paths in layout examples may intentionally refer to future files; label them planned and do not link them as if they exist. External project API details must be checked against the pinned version when implementing an integration. This phase does not certify any third-party API or advertised performance claim.

## Later implementation verification

Unit tests should exercise validation and state transitions. Integration tests should exercise persistence and component boundaries. Acceptance tests should prove user outcomes and meaningful failures from [MVP.md](../product/MVP.md), especially ambiguous effects and retained budgets.

Use deterministic provider fixtures for repeatable failure injection, but separately test a real local model on Windows before accepting the local MVP. Mark Ubuntu/VPS checks pending until run on the actual target. State measured and estimated resource usage separately.

## Dependencies and integrations

Python dependencies are locked in Phase 2; follow [TESTING.md](TESTING.md) for updates. Pin Taskmaster tooling in Phase 3 and verify actual MCP schemas, minimal tool-selection support, and local-model compatibility then. Do not silently add a paid-provider credential or fallback to make a test pass.

Add tools through typed contracts, policy checks, observable intent/results, and independent verification. Add skills only when their procedure can be reviewed and its limits stated. Significant architecture changes require a decision record and corresponding requirement updates.
