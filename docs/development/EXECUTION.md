# Deterministic file execution

Phase 4 implements the A01–A08 execution foundation. An owner-supplied manifest
names synthetic inputs, exact SHA-256 expectations, new destinations, and a
report. The controller compiles a fixed copy/report dependency plan. It does
not use a model, shell tool, network, memory vault, or remote worker.

## Run the example

Use the Python/uv setup from the OS guide. Choose a **new** directory outside
all checkouts. The fixture creator refuses an existing root and supplies a
private config, input file, manifest, and empty output directory.

Windows PowerShell:

```powershell
$demoRoot = Join-Path $env:USERPROFILE 'jangus-phase4-demo'
uv run --locked python examples/tasks/create_fixture.py --root $demoRoot
$configFile = Join-Path $demoRoot 'config.yaml'
$submitted = uv run --locked jangus run submit --config $configFile --workspace "$demoRoot/workspaces/demo" --manifest "$demoRoot/manifest.json" | ConvertFrom-Json
$runId = $submitted.run_id
uv run --locked jangus run execute $runId --config $configFile
uv run --locked jangus run status $runId --config $configFile
uv run --locked jangus run export $runId --config $configFile --output "$demoRoot/diagnostics.zip"
```

Linux/Ubuntu:

```bash
demo_root="$HOME/jangus-phase4-demo"
uv run --locked python examples/tasks/create_fixture.py --root "$demo_root"
uv run --locked jangus run submit --config "$demo_root/config.yaml" --workspace "$demo_root/workspaces/demo" --manifest "$demo_root/manifest.json"
# Set run_id to the run_id returned above.
run_id='PASTE_RETURNED_RUN_ID'
uv run --locked jangus run execute "$run_id" --config "$demo_root/config.yaml"
uv run --locked jangus run status "$run_id" --config "$demo_root/config.yaml"
uv run --locked jangus run export "$run_id" --config "$demo_root/config.yaml" --output "$demo_root/diagnostics.zip"
```

Check the copied bytes and `out/report.json`. Submit only queues work; execute
runs it synchronously. The command returns a JSON status. Exit 0 means success
(or an accepted query/request); 1 means failed, waiting, or cancelled execution;
2 means invalid input or an unavailable operation. No command overwrites an
existing output or export. Use a new fixture root for another demonstration.

## Contract and permission boundary

[The fixture creator](../../examples/tasks/create_fixture.py) demonstrates the
manifest schema. Manifest JSON is bounded to 64 KiB, with unique keys, no extra
fields, 1–40 files, portable relative paths, and lowercase SHA-256 hashes. Output
names are unique ignoring case and cannot overlap inputs or each other's parent
paths. Inputs must match the trusted manifest before submission and again before
copying. Input/output parent directories must already exist; creating them is an
explicit setup operation, not an unlogged runtime tool.

The private configuration allowlists tool names. The portable setup example
keeps that list empty; the fixture explicitly enables `file.copy` and
`file.report`. Also registered are bounded `file.list`, `file.read`, and
`file.hash`, exercised through their typed contracts. The deterministic CLI
compiler dispatches only copies and the report. It does not accept arbitrary
plans or expose a generic tool-call endpoint. Future model proposals need a
separate reviewed integration with these contracts.

Each run is restricted to one existing child of `paths.workspace_dir`. Data
and workspaces remain outside Git and do not overlap. Paths reject traversal,
absolute names, backslashes, device names, alternate streams, links, Windows
junctions/reparse points, and hard-linked input files. Files are bounded to
1 MiB; listings to 100 entries. Writes use exclusive creation, flush/fsync, and
independent re-reads. Native identity and containment checks run at execution.

Use local filesystems and a private workspace controlled exclusively by this
runtime/user while a run is active. The implementation is not an OS sandbox
against an administrator or a hostile concurrent directory-renaming process.
POSIX no-follow opens and handle identity checks provide additional protection;
Windows path checks do not claim a kernel-enforced sandbox. One executor per
state directory is enforced with an OS-released file lock. Do not point separate
state directories at the same workspace. Network filesystems are unsupported.

## Persistence and recovery

SQLite schema version 1 stores runs, immutable plans, actions, attempts, evidence,
and ordered events. Schema creation is transactional; unknown schema versions
are rejected. WAL mode with synchronous FULL provides the local persistence
boundary. An intent event, attempt increment, and dispatch increment commit
before a file effect. Result and independent verification events follow. Errors
are stable codes rather than raw exception strings.

Run states are `queued`, `running`, `waiting`, `succeeded`, `failed`, `cancelled`.
Actions are `pending`, `intent`, `verified`, `failed`, or `uncertain`. The
configuration hash, reviewed rules hash, policy version, plan revision, workspace
identity, total dispatch count, per-action attempt count, and absolute deadline
survive restart. Phase 4 accepts only plan revision 1. Changed policy/configuration
or a stale revision cannot silently authorize a resume.

```console
jangus run resume RUN_ID --revision 1 --config PRIVATE_CONFIG
jangus run cancel RUN_ID --config PRIVATE_CONFIG
```

After interruption, resume first checks artifacts belonging to persisted intents:

| Observation | Behavior |
|---|---|
| Matching output bytes | Adopt verified evidence without another write or attempt |
| No output | Return to pending; the interrupted attempt remains charged |
| Partial, different, unreadable, or unsafe output | Mark uncertain and wait; never delete or overwrite |
| Output present before a fresh intent | Reject as a conflict, even if its bytes match |

`max_actions` bounds total dispatch attempts, including retries; the plan must
also fit that count. `max_attempts_per_task` bounds each compiled action's total
attempts. Only an I/O failure with confirmed absent output can retry. Policy or
verification errors stop; uncertain effects wait. Deadlines include downtime
and stop new dispatch. Recovery can verify an already completed effect after a
deadline. A backwards clock before run creation fails closed. Machine clock and
filesystem integrity remain host responsibilities.

When model roles run before dispatch, the submitting workflow reserves their
time: `Controller.submit(..., deadline_extension_seconds=N)` adds a fixed,
validated allowance (0–900 s; two `model.call_timeout_seconds` for the
coordinated planner and worker, one for the single planner) to the persisted
deadline. The allowance is set once at submission and can never be renewed, so
slow inference cannot consume the budget for file effects and cannot extend it.

Cancellation is a persisted request that a second process may make during
execution. The current bounded action can finish; its effect is verified or
marked unresolved. No later action is dispatched. For an idle interrupted run,
resume performs the cancellation reconciliation. Cancelled/failed terminal runs
do not restart. Cancellation does not undo files. An unresolved cancelled run
retains its action evidence and is excluded from retention pruning.

A full database, failed transaction, or unavailable required event log prevents
new effects. If result persistence fails after a write, the previous intent
remains authoritative. Retry resume after repairing storage; do not invent a
new run to bypass counters. Status is historical evidence of the last check,
not a continuous guarantee that another program has not since edited a file.

## Diagnostics, retention, and backups

Exports contain `summary.json`, ordered `events.jsonl`, permitted
`verification.json` receipts, `omissions.json`, and `checksums.json`. They use a
consistent read snapshot. Allowlisted IDs, numeric counters, hashes, states, and
reason codes are retained. Raw files, filenames/paths, manifests, configuration,
free text, and database contents are omitted. Exported uncompressed members are
bounded to 2 MiB. This deliberate omission avoids pretending a regex can detect
every secret. The source database remains private and contains the paths and
manifest required for recovery; never share it as a diagnostic bundle.

The default operational database budget is 256 MiB. Page limits and a size check
before each transaction stop new effects when storage is full; one bounded
transaction/WAL allocation can temporarily exceed the threshold. There is no
promise of a hard filesystem quota. Default retention is 14 days, applied only
by the explicit prune command to terminal runs without unresolved intents.
Workspaces, exports, and private backups are managed separately and are never
automatically deleted. Status/maintenance can open a full existing database.

```console
jangus state backup --config PRIVATE_CONFIG --output NEW_PRIVATE_BACKUP.sqlite3
jangus state prune --config PRIVATE_CONFIG
jangus state restore --config PRIVATE_CONFIG --backup PRIVATE_BACKUP.sqlite3
```

Backup uses SQLite's snapshot backup API, not a copy of the database without its
WAL. Keep it private (POSIX files use mode 0600; Windows inherits directory ACLs).
Restore requires an empty state location and passes integrity/version checks.
Stop all clients, archive the old state directory yourself, then restore to the
same configured location. Never overwrite live state. Original paths, workspace
identity, policy hashes, attempts, and deadlines remain in force; restore is not
a budget reset. Tests restore a snapshot with an uncertain in-flight effect and
reconcile it without duplicating the write.

## Verification

Run `uv run --locked pytest tests/execution` for acceptance/fault coverage and
the full checks in [TESTING.md](TESTING.md). Tests kill real subprocesses after
intent, after effect, and after verification. They also cover partial output,
corruption, fabricated success, transient/persistent I/O, cancellation from a
second process, storage failures, path escapes, backup/restore, and secret canaries.
The isolated-wheel check runs the complete synthetic workflow outside the source
checkout. See [Phase 4 review](../reviews/PHASE_4.md) for actual outcomes.

API references: [Python sqlite3 backup/transactions](https://docs.python.org/3.12/library/sqlite3.html)
and [native path checks](https://docs.python.org/3.12/library/pathlib.html).
