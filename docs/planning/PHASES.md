# Phased repository delivery

This is the agreed delivery sequence, not a second development task backlog. Taskmaster owns detailed dependencies and task status from Phase 3. Current implementation: Phase 4 deterministic execution, provisional Phase 5 agent code, Phase 6 memory code and Phase 7 synthetic remote worker in stacked unmerged branches. Hardware, owner-machine and Taskmaster acceptance gates remain pending.

| Phase | Branch example | Deliverables | Verification before submission |
|---|---|---|---|
| 1 | `foundation/01-specifications` | README, AGENTS, lowercase rules, PRD, MVP, system architecture, decisions, contribution guidance | Design consistency, scope, relative links, content review |
| 2 | `foundation/02-development-setup` | Python package/lock, sanitized config, exclusions, OS setup guidance, CI | Install/import/config checks, private-data exclusion, actual CI outcome |
| 3 | `foundation/03-taskmaster` | Pinned Taskmaster MCP, minimal configuration, reviewed backlog, adapter contract, assignment/result templates | Installed-version config, dependencies, completion evidence, no paid fallback |
| 4 | `runtime/04-reliable-execution` | SQLite state/events, registered file tools, policy, verifier, recovery, cancellation, exports | Synthetic workflow and A01–A08 |
| 5 | `runtime/05-local-agents` | Local provider, roles, isolated contexts, skill loader/procedures, budgets and handoffs | A09–A13; distinguish mocks from actual machine evidence |
| 6 | `memory/06-obsidian` | Numbered vault templates, approval registry, retrieval, proposals | A14–A17, conflicts and restoration behavior |
| 7 | `remote/07-vps-worker` | Durable job client/service, SSH guidance, systemd, remote-job skill | Lost acknowledgement, reconnect/restart/cancellation, actual VPS validation |
| 8 | `integrations/08-consultation` | Optional Codex, supported disabled-by-default Astra, independent optimization experiments | Sharing and budget enforcement; comparative benchmark evidence |

Phase 8 may use separate PRs for consultation, Graphify, and Headroom. Browser automation, Windows control, vision, and a dashboard are later milestones.

## Submission process

Inspect the current repository and instructions, create a focused branch, implement only the authorized phase, verify, review the complete diff, push, and open a PR with its actual base and limitations. Do not merge automatically.

When a preceding phase is merged, branch from the updated default branch. If it remains open and dependent work is authorized, branch from that reviewed phase and target the predecessor explicitly; retarget after it merges. Avoid including unrelated changes or claiming that a submitted PR is already merged.

This repository began empty. A minimal README initialization commit supplies the `main` base needed for the Phase 1 branch and PR. All Phase 1 specifications are proposed on the feature branch.

## Release gates

The deterministic execution foundation ends at Phase 4. The complete local agent MVP spans through Phase 6. The separate VPS milestone follows in Phase 7. Optional paid consultation and optimization never block local MVP completion.

Create real modules and useful templates as their phase begins. Do not add empty source trees, nonfunctional commands, speculative dependency locks, or placeholder license terms to make the repository appear implemented.
