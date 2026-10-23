# Taskmaster development integration

Phase 3 pins `task-master-ai` 0.43.1 and MCP SDK 1.32.1. The canonical reviewed
backlog is [tasks.json](../../.taskmaster/tasks/tasks.json). Its 30 tasks trace
the PRD and MVP and separate the eight delivery phases. Runtime job state is
not stored here. Task statuses describe verified implementation, not PR merges.

## Install and connect

Install Node 24.19.0 and npm 11.9.0, then run from the checkout:

```console
npm ci --ignore-scripts --no-audit --no-fund
npm run tasks:check
npm run test:taskmaster
```

Use [mcp.example.json](../../config/mcp.example.json) in an MCP client's server
configuration. Replace the absolute script path with this checkout's path;
Windows accepts `C:/path/to/jangus/scripts/taskmaster/server.mjs`. The client
must resolve `node` from PATH, or use its absolute executable path. Start the
script directly, because npm banners are not MCP JSON messages. No credentials
or model installation are required. The script infers its project from its own
location; tests use an explicit absolute `--project-root` for disposable data.

Only one writable MCP session per checkout is supported. The adapter serializes
calls within a session; it does not provide a cross-process transaction manager.
Stop the session before manually editing reviewed tasks or pulling changes.
The example is portable client configuration, not an installed desktop client.

## Exposed contract

| Tool | Arguments | Result / policy |
|---|---|---|
| `get_tasks` | Optional `status` | Compact task list and statistics |
| `get_task` | Positive integer `id` | One full task and acceptance instructions |
| `next_task` | None | Upstream dependency-based suggestion; phase authorization still applies |
| `set_task_status` | Positive integer `id`, `status` | Persist progress; gate start/completion on dependencies and completion on evidence |

Supported statuses: `pending`, `in-progress`, `review`, `done`, `blocked`,
`deferred`, `cancelled`. IDs are top-level only. Unknown arguments, project/file
overrides, alternate tags, and unlisted tools are rejected. The adapter fixes
the project and `master` tag. It checks the actual installed upstream tool list
at connection time and refuses an expanded or incomplete list. Upstream stores
some IDs as strings; the adapter presents numeric IDs without a competing store.

Read the assignment, check dependencies, use the
[brief](../../templates/task-brief.md), implement only authorized scope, run its
verification, and prepare the [result](../../templates/task-result.md). `done`
requires all dependencies/subtasks done and a local evidence record. Reopening
a completed prerequisite is rejected while a direct dependent is active/done.
Review dependent work before such a change. This profile does not expose task
creation or AI expansion; backlog changes are reviewed Git edits.

## Completion evidence

Create `.taskmaster/evidence/ID.json`. Replace the example revision with the
40-character SHA of the tested commit and use actual passed checks:

```json
{
  "taskId": 3,
  "revision": "REPLACE_WITH_TESTED_COMMIT_SHA",
  "checks": [
    {
      "name": "Actual integration checks",
      "result": "passed",
      "artifact": "docs/reviews/PHASE_3.md"
    }
  ]
}
```

Each check requires a name, `passed` result, and an existing repository file or
an `https://github.com/jenozu/jangus/` URL. Paths are resolved to reject escapes
and symlink escapes. The gate validates declarations and references; it does
not run tests, fetch URLs, prove their contents, or replace reviewer judgment.
Evidence for a code commit may precede a bookkeeping-only status commit.
Failure or missing evidence leaves the status unchanged. The checked backlog
must remain a dependency DAG; completed records must keep valid evidence.

## Local-only profile and limits

[The config](../../.taskmaster/config.json) explicitly selects local Ollama for
main, research, and fallback roles, with a deliberately unconfigured model name.
Telemetry and proxying are disabled. The launcher strips provider keys and
tool-selection overrides from the child environment, refuses automatic root
`.env`/legacy configuration, and pins the four upstream tools. A preload blocks
Node network APIs in the upstream process. It is defense in depth, not an OS
security sandbox or protection from a malicious dependency/native subprocess.

AI planning, research, PRD parsing, autopilot, and cloud fallback are unavailable
through this adapter. The four task operations have been exercised against the
real installed MCP server without a provider key or model call. They work with
the local provider profile, but this is not an Ollama inference benchmark.
Task 12 in Phase 5 requires actual desktop model checks. Enabling Taskmaster
model planning later needs an explicit architecture change, dependency review,
local-only failure tests, and measured context/performance evidence.

The four exposed tool definitions total 1,114 UTF-8 bytes; bytes/4 gives a rough
279-token estimate. This includes names/descriptions/schemas but excludes protocol framing, results, and tokenizer
differences. It is not measured model token consumption or an upstream comparison.

The npm lock pins the complete upstream dependency graph. `simple-git` 4.0.2
and older-major `undici` 6.29.1 are explicit advisory-remediation overrides.
Installation scripts stay disabled. See the [phase review](../reviews/PHASE_3.md)
for the dated audit result and remaining upstream advisories. Do not enable
unused CLI, download, or AI features merely because the package includes them.
For upgrades, update exact pins deliberately, regenerate the lock, inspect its
registry/integrity entries, rerun both OS integration jobs, and review upstream
schemas, config defaults, telemetry, and advisories before adopting the change.

## Verification and implementation

The Node adapter lives in `scripts/taskmaster/`; no Python runtime integration
or controller has been implemented. Integration tests use MCP over stdio through
both the adapter and real Taskmaster, persist/reopen synthetic tasks, check
dependency/evidence rejection, and confirm the real backlog is unchanged.
Policy tests cover configuration, credentials, invalid graphs, path escapes,
and representative blocked network APIs.

Sources: [Taskmaster source](https://github.com/eyaltoledano/claude-task-master),
[npm release](https://www.npmjs.com/package/task-master-ai/v/0.43.1), and
[MCP TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk).
The pinned installed source and executable tests define this profile's behavior.
