# Configuration contract

The repository provides one complete, validated [YAML example](jangus.example.yaml). Model, permissions, and logging sections live in the same file so there is no implicit merge order or conflicting configuration source. Separate files can be introduced with an explicit contract when needed.

Copy the example outside the checkout and choose private absolute paths or paths beginning with `~/`. Windows may use a single-quoted path such as `'C:\Users\YOUR_USER\JangusData'`; a Linux path might be `/home/YOUR_USER/jangus-data`. Environment variables inside YAML strings are not expanded.

Use:

```console
uv run --locked jangus config validate --config config/jangus.example.yaml
uv run --locked jangus config schema
```

The first command checks configuration and prints a confirmation without values. The second prints the structural JSON schema. The loader also checks cross-field constraints and native filesystem paths, which the schema alone cannot certify.

## Selection and validation

An explicit `--config` takes precedence over `JANGUS_CONFIG`. There is no automatic search or `.env` loading. A missing selection is an error. Files must be UTF-8 YAML mappings under 64 KiB. Unknown settings, duplicate/non-string keys, anchors, aliases, unsafe tags, malformed content, and wrong scalar types are rejected. Validation errors do not print supplied values.

Data and workspace paths must be distinct, non-overlapping directories outside detected Git checkouts, including symlink resolution. Existing files cannot be used as directories. Paths need not exist; validation creates nothing and makes no network calls. Path checks apply to the machine running the validator. They are setup checks. Phase 4 separately enforces tool/workspace policy during execution; see [the execution contract](../docs/development/EXECUTION.md) for its private-workspace boundary.

## Current settings

| Section | Meaning through Phase 4 |
|---|---|
| `schema_version` | Integer 1; future incompatible schemas need migration |
| `paths` | Required private data/workspace locations; tilde expansion supported |
| `model` | Planned Ollama loopback HTTP endpoint with explicit port; model name may be null; one inference at a time |
| `features` | Remote/Codex/Astra flags must remain false while integrations are absent |
| `permissions` | Explicit unique allowlist from `file.list`, `file.read`, `file.hash`, `file.copy`, `file.report`; empty by default; network disabled |
| `limits` | Persistent total dispatch attempts, per-action attempts, and deadline; context limits reserved for Phase 5 |
| `logging` | Explicit terminal-record retention and operational database size limit; structured disclosure filtering always enabled |

The `config validate` command remains read-only. Explicit `run` commands create runtime state and enforce permissions/budgets; `state prune` performs retention maintenance. No model is loaded. No credentials belong in this file. Model endpoint fields reject authentication, query strings, and external hosts.

The initial limits are starting values for synthetic tasks, not measured performance recommendations. Runs retain the configuration hash and enforced budgets; changed configuration cannot silently resume an older run.
