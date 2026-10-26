# Local-first MVP and remote-worker acceptance handoff

**Status: developer-only synthetic integration rehearsal.** This checklist is a
runbook for the repository owner, **not** a claim that their Windows desktop,
Ollama, Obsidian vault or VPS has been operated by CI. The original roadmap
ends at Phase 8; this is a cross-phase readiness checkpoint, not a new
Taskmaster delivery phase or an automatic release approval.

The feature branches/PRs remain stacked and **unmerged**. Use the current
reviewed `release/acceptance-readiness` branch while these checks are under
review. Do not expect `main` to contain the full system yet. Back up or
commit your own local work before changing Git branches.

## 1. Developer checks (safe without a real model or personal vault)

From the reviewed project checkout:

```powershell
uv sync --locked
uv run --locked pytest tests/release
uv run --locked python scripts/check_release_readiness.py
uv run --locked python scripts/check_release_readiness.py --check foundation
./scripts/windows/check-owner-readiness.ps1
```

The readiness script checks the *reviewed Taskmaster backlog* for required
task IDs, valid statuses, complete dependencies, and cycles; then prints
the unfinished tasks and next dependency-unblocked IDs. Its default mode
returns exit code 0 when the backlog is well-formed **even if tasks are
pending**. `--check foundation` requires tasks 1–11 marked done and exits
3 if that milestone is not complete. `--check local_mvp` (or
`private_vps` / `optional`) also checks **status only**, not real-world
acceptance. These commands never change Taskmaster task states.

The Windows PowerShell script reports only whether required command names,
supported Python, and a checkout are present. It does not call Ollama,
make an SSH connection, read credentials, write files, install anything or
collect model names. A green prereq check is **not** a model benchmark.

CI additionally runs a single synthetic integration rehearsal covering
a mocked local-role provider, exact-revision approved synthetic Obsidian
memory, independently verified file effects, draft-only lesson creation,
a separate loopback-only durable remote job, and default-disabled optional
consultation. All data is disposable, the remote HTTP listener binds
127.0.0.1 with an ephemeral local port, and no actual Codex/Astra call occurs.

## 2. Your Windows model benchmark — Taskmaster 12

Before any local model acceptance, use the
[model benchmark guide](LOCAL_MODEL_BENCHMARK.md) to record OS/CPU/free
memory, installed Ollama model tag, model quality and latency. Choose
the specific model from measurements on **your actual Windows PC**:

```powershell
ollama list
uv run --locked python scripts/benchmark_ollama.py --model "EXACT_LOCAL_MODEL_TAG" --output "$env:USERPROFILE/jangus-local-benchmark.json"
```

Keep the JSON and any resource measurements privately. Share only reviewed,
sanitized test summaries. Do not upload a full Windows environment dump,
private model logs, API keys or user documents to GitHub. **Do not mark
Taskmaster 12 done** until the real measurements and model placement are
reviewed.

## 3. Real coordinated local workflow — Phase 5 gates A09–A13

Create a **fresh synthetic** fixture in a private directory, outside the
repository; configure its generated private `config.yaml` with the
installed local Ollama model (not a cloud endpoint):

```powershell
$demoRoot = Join-Path $env:USERPROFILE "jangus-acceptance-demo"
uv run --locked python examples/tasks/create_fixture.py --root $demoRoot
uv run --locked python scripts/run_agent_fixture.py --coordinated --config "$demoRoot/config.yaml" --workspace "$demoRoot/workspaces/demo" --manifest "$demoRoot/manifest.json"
```

Keep the task data synthetic. Verify independently that the copied file and
generated report match the manifest, the run is persisted, the roles do not
expand tool permissions, and attempted/recovered work remains within
budgets. An Ollama model accepting a prompt is insufficient. Follow
[LOCAL_AGENTS.md](LOCAL_AGENTS.md) and record reviewed A09–A13 evidence.

## 4. Private Obsidian approval — Phase 6 gates A14–A17

Copy only the **sanitized Obsidian templates** to a separate personal vault.
Keep vault files, private SQLite approval state, and passphrase enrollment
separate from the Git checkout and from each other. First run a safe
synthetic note. The exact commands and review policy are in
[MEMORY.md](MEMORY.md), including `scripts/review_memory.py` operator
enrollment, exact-sha note inspection and interactive decision approval.

Run the local fixture again with `--coordinated`, `--vault`,
`--registry`, `--memory-scope`, `--memory-required` and optionally
`--propose-lesson`. Verify that an approved note appears only as
lower-trust local context, an edited/revoked/conflicted note cannot be
used, and new lessons go only to `70_Inbox_Agent` as unapproved drafts.
The private review CLI cannot be invoked non-interactively by Jangus
to approve a note.

Record actual A14–A17 evidence and give explicit owner sign-off on A01–A17
before Taskmaster 22 is marked done. **No synthetic CI run supplies your
consent or verifies a live vault.**

## 5. Private VPS — Phase 7 gates (only after local MVP acceptance)

Follow the [remote worker deployment checklist](REMOTE_DEPLOYMENT_CHECKLIST.md)
only during an approved maintenance window. Owner action is necessary to
verify the real SSH host key **out-of-band**, authorize a low-privilege
Ubuntu service account and token handling, confirm no public port 8765,
inspect systemd, and run live lost-ack, disconnect, process restart and
cancellation tests with synthetic jobs.

Do not reset mismatched SQLite state or identity anchors. A previous
schema version requires an explicitly reviewed migration. No tool in this
checkpoint connects to or deploys on the VPS.

## 6. Optional external AI — Phase 8 (not an MVP prerequisite)

The local system works without Codex SDK or paid Astra API. Consult
[OPTIONAL_INTEGRATIONS.md](OPTIONAL_INTEGRATIONS.md) before an explicit
separate owner decision on sandbox design, data disclosure, per-call
approval and a capped spending budget. Do not share personal vault
content to a consultant because a label or a test says `external-ok`.
Graphify/Headroom-style results are synthetic prototypes, not observed
production improvements. Do not automatically enable them after CI.

## 7. Merging and release review

Review [the phase roadmap](../planning/PHASES.md) and each stacked
pull request in order. Never merge all branches merely because their
tests passed: later PRs depend on earlier reviews, and Taskmaster
acceptance tasks 12–30 still carry real owner gates.

Before a release, review the complete diff, check no personal or runtime
files are staged, collect sanitized acceptance evidence, decide distribution
license/permissions, verify backup and recovery, then explicitly approve
each merge/release. The `check_release_readiness.py` script does
**not** sign off on any of these things.
