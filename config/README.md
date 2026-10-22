# Configuration contract

Phase 2 provides one complete, validated [YAML example](jangus.example.yaml). Model, permissions, and logging sections live in the same file so there is no implicit merge order or conflicting configuration source. Separate files can be introduced with an explicit contract when needed.

Copy the example outside the checkout and choose private absolute paths or paths beginning with `~/`. Windows may use a single-quoted path such as `'C:\Users\YOUR_USER\JangusData'`; a Linux path might be `/home/YOUR_USER/jangus-data`. Environment variables inside YAML strings are not expanded.

Use:

```console
uv run --locked jangus config validate --config config/jangus.example.yaml
uv run --locked jangus config schema
```

The first command checks configuration and prints a confirmation without values. The second prints the structural JSON schema. The loader also checks cross-field constraints and native filesystem paths, which the schema alone cannot certify.

## Selection and validation

An explicit `--config` takes precedence over `JANGUS_CONFIG`. There is no automatic search or `.env` loading. A missing selection is an error. Files must be UTF-8 YAML mappings under 64 KiB. Unknown settings, duplicate/non-string keys, anchors, aliases, unsafe tags, malformed content, and wrong scalar types are rejected. Validation errors do not print supplied values.

Data and workspace paths must be distinct, non-overlapping directories outside detected Git checkouts, including symlink resolution. Existing files cannot be used as directories. Paths need not exist; validation creates nothing and makes no network calls. Path checks apply to the machine running the validator. They are setup checks, not the race-safe tool authorization planned for Phase 4.

## Current settings

| Section | Meaning in Phase 2 |
|---|---|
| `schema_version` | Integer 1; future incompatible schemas need migration |
| `paths` | Required private data/workspace locations; tilde expansion supported |
| `model` | Planned Ollama loopback HTTP endpoint with explicit port; model name may be null; one inference at a time |
| `features` | Remote/Codex/Astra flags must remain false while integrations are absent |
| `permissions` | Empty tools and network disabled; no tool broker exists yet |
| `limits` | Proposed bounded run/context defaults; response allowance must be less than total context |
| `logging` | Proposed retention/size defaults; redaction must remain enabled |

Only validation is implemented. These settings do not start an agent, enforce a runtime budget, rotate logs, load a model, or create a database. No credentials belong in this file. Model endpoint fields reject authentication, query strings, and external hosts.

The initial limits are starting values for synthetic tasks, not measured performance recommendations. Changes require validation and, once a runtime exists, persistence in each run's configuration snapshot.
