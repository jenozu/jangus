# Architecture decision records

These records capture accepted design direction for implementation. Accepted does not mean implemented.

| Record | Decision |
|---|---|
| [ADR-001](001-local-orchestration.md) | Self-hosted orchestration and bounded optional consultation |
| [ADR-002](002-taskmaster-and-context.md) | Taskmaster MCP for development and isolated runtime assignments |
| [ADR-003](003-rules-skills-memory.md) | Separate rules, reviewed skills, memory, and execution authority |
| [ADR-004](004-durable-execution-and-transport.md) | Durable execution, logging first, and private VPS jobs |
| [ADR-005](005-taskmaster-offline-profile.md) | Narrow offline Taskmaster profile and completion-evidence gate |
| [ADR-006](006-deterministic-execution.md) | Deterministic execution, conservative recovery, and diagnostic privacy |

New decisions should state context, decision, alternatives, consequences, and verification implications. Supersede earlier records explicitly instead of silently rewriting their historical decision.
