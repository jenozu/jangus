# ADR-005: Taskmaster offline development profile

Status: accepted and implemented in Phase 3. Refines ADR-002.

## Context

The development backlog needs actual MCP task reads, dependency ordering, and
verified status changes before runtime agents exist. Taskmaster 0.43.1 includes
AI tools and provider defaults broader than this phase. Its default core tool
set includes AI operations; an invalid custom tool list can fall back broadly.
Runtime controller state and development task status have different owners.

## Decision

Pin Taskmaster and MCP SDK in a separate development npm lock. Wrap the installed
server with a small Node MCP adapter exposing only four verified task operations.
Keep the reviewed backlog in Taskmaster's file format, fix project/tag, and gate
completion on dependencies, subtasks, and reviewable evidence. Validate the exact
upstream tool list. Disable telemetry/proxy, remove credential inheritance, and
block upstream network APIs. Configure every planning role to loopback Ollama,
but expose no planning calls before actual local-model validation in Phase 5.

## Alternatives and consequences

Directly exposing the default upstream server offers more features but lacks
this narrow scope and completion gate. A separate custom task database would
duplicate Taskmaster ownership. An early Python runtime adapter would invent
controller interfaces before Phase 4. The Node wrapper fits the installed SDK
and exercises the real server while keeping Python runtime work separate.

The upstream package remains a large transitive dependency set with advisories;
pinning and limiting features do not erase that risk. This profile is for one
trusted local development session per checkout, not hostile multi-tenant use.
The evidence gate cannot establish that declared tests actually ran. Git review
and actual output inspection remain necessary. Future AI enablement requires a
new reviewed change rather than silently expanding this adapter.

## Verification implications

Exercise the actual stdio MCP path on Windows and Ubuntu, status persistence,
invalid dependencies, missing/failed evidence, unsupported arguments, exact
tool discovery, no inherited credentials, and network blocking. Preserve the
real backlog during tests. Record schema size as an estimate basis and avoid
claiming hardware or model tests from deterministic task operations.

See [the integration contract](../../development/TASKMASTER.md) and
[Phase 3 evidence](../../reviews/PHASE_3.md).
