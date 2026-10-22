# Coding-agent entry point

Read [rules.md](rules.md) before changing this repository. It is the canonical shared rule source; this file defines the repository workflow.

## Working sequence

1. Inspect the branch, existing changes, and any more-specific instructions. Preserve unrelated work.
2. Read the relevant [PRD](docs/product/PRD.md), [MVP](docs/product/MVP.md), [architecture](docs/architecture/SYSTEM.md), and [phase scope](docs/planning/PHASES.md).
3. Use the assigned Taskmaster task once Phase 3 is implemented. Before then, use the authorized phase and PR description as the assignment. Do not invent Taskmaster IDs or claim MCP is connected.
4. Load relevant reviewed procedures from root `skills/` when implemented. No skill files or discovery mechanism exist through Phase 2. Record unavailable prerequisites honestly.
5. Make scoped changes on a feature branch. Create source directories when concrete functionality or interfaces require them.
6. Verify the acceptance criteria using actual outputs. Follow [contribution guidance](docs/development/CONTRIBUTING.md) for documentation-only changes.
7. Review the full diff for regressions, secrets, personal data, broken links, and inaccurate capability claims.
8. Push the authorized branch and prepare a PR identifying its base, changes, evidence, and pending hardware checks. Do not merge or deploy without authorization.
9. Update Taskmaster status only after verification, once that integration exists. A push or model success message alone is not completion evidence.

## Where to look

- Runtime architecture and component ownership: `docs/architecture/SYSTEM.md`.
- Product requirements: `docs/product/PRD.md`.
- Release gates: `docs/product/MVP.md`.
- Reasons for significant choices: `docs/architecture/decisions/`.
- Future procedures: root `skills/`; future loader: `src/jangus/skills/`.
- Configuration examples: `config/`; private operational configuration stays outside Git.
- Package checks and dependency updates: `docs/development/TESTING.md`.

Follow the current authorized phase. Later phases in the plan are not permission to implement them now. If a design change is needed, explain its effects and update the affected specifications and decision record together.
