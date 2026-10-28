# Phase 7 — private Ubuntu VPS worker (provisional)

The code is prepared for the **separate remote milestone** on branch
`remote/07-vps-worker`, stacked on the open Phase 6 PR. No VPS connection,
remote deployment, secret provisioning or real SSH tunnel test has occurred.
Tasks 23–26 depend on Taskmaster task 22 (still pending), so they are also
pending despite the source and synthetic CI tests.

## Scope and protocol

Phase 7 registers exactly **one** job: `synthetic.hash`. It accepts at most
2 KiB of owner-supplied synthetic UTF-8 text and writes a private bounded JSON
result with the content hash/byte count. There is **no** remote shell, Python
evaluation, generic file-copy endpoint, browser, private document ingestion,
LLM inference, or tool-registration API. Additional job types require future
scope approval, explicit schema, policy, tests and acceptance.

The client and service use `/v1/jobs`, `/v1/jobs/{job_id}`,
`/v1/jobs/{job_id}/cancel` and `/v1/health`, all with a bearer token, exact
JSON and version 1. The server binds **127.0.0.1 only**, and the client refuses
non-loopback destinations and HTTP redirects. The SSH tunnel provides encrypted
transport and verified server host identity; a bearer token is a separate
application authorization boundary. No public port or reverse proxy is
supported. Only localhost HTTP is supported *inside the tunnel*.

The client persists a canonical UUID job ID, UUID idempotency key and SHA-256
digest of the canonical request in private SQLite **before** its first POST.
After an uncertain acknowledgement it first GETs that same ID; only a genuine
404 triggers an identical resubmission. A reused job ID or key with different
content is a conflict, not a new job. The server owns its own SQLite WAL/FULL
state independently of the initiating SSH connection. Status and result
receipts must match job ID, key, protocol, request and result hashes.

The worker persists a charged attempt and intent **before** an exclusive-create
result file. On restart, matching bytes are adopted without a second effect,
a missing result may be retried within its original attempt/deadline budgets,
and a partial or mismatched artifact enters `waiting` without overwrite.
Cancellation now permits an uncertain `waiting` job to transition to `cancelled` while preserving its `output_uncertain` error and never overwriting the partial artifact. Cancellation prevents new effects when observed, but an already written and
verified effect is reported with `cancelled` and the result digest. Terminals
are never silently retried. Test limits: 500 queued/terminal records per state
directory, 2 charged attempts/job, 15-minute deadline from initial submission;
no automated pruning in this phase. Manual maintenance needs a separate plan.
The private SQLite server ledger now includes a separate immutable identity anchor, version-2 schema and sanitized persisted transition events, rejecting missing/replaced state and orphaned artifacts rather than silently issuing a new job on lost state. **This is not complete rollback protection** against restoration of both the database and identity file; older v1 databases require a separate reviewed migration, not file deletion. The client prevents terminal receipts from regressing, and a failed worker liveness check returns unhealthy while the supervisor exits nonzero for systemd restart. The private paths are assumed exclusively controlled by a dedicated worker
account; this is not a hostile-administrator sandbox.

## Reproducible synthetic developer checks

```console
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest tests/remote
uv run --locked pytest
```

Tests also cover the read-only deployment preflight, verified SSH tunnel helper, ledger identity, transition audit, terminal client receipt rollback, uncertain-output cancellation, worker health failure and actual service/client object restart. Tests exercise actual loopback HTTP, authentication, conflicts, a lost accepted
submit acknowledgement, status reconciliation, cancellation, simulated
power loss immediately before and after a file effect, restart, corrupt output,
immutable identity and digest checks. They do **not** prove live VPS connectivity
or systemd recovery on your actual host. See [the phase review](../reviews/PHASE_7.md).

For the detailed read-only operator preflight, manual SSH tunnel, actual host acceptance and stop conditions, see [the deployment checklist](REMOTE_DEPLOYMENT_CHECKLIST.md). No installer is run or enabled by CI.

## Future Ubuntu operator setup — not executed

Do not start this on an Internet-reachable interface. Review firewall, user
identity, storage and existing service collisions before any actual deployment.

1. Install Python 3.12/3.13 and the reviewed Jangus wheel in a dedicated
   `/opt/jangus/.venv`. Verify the installed wheel and locked dependencies.
2. Create a **dedicated** `jangus-worker` account (no privileged access) and
   an exclusively controlled `/var/lib/jangus` state directory, owned by it.
   The server writes its private ledger and artifacts under
   `/var/lib/jangus/remote`. Ensure private filesystem permissions.
3. Create a unique random 32+ character token in
   `/etc/jangus/worker.env`, readable only as required by systemd, never
   committed. Example structure: [worker.env.example](../../deploy/systemd/worker.env.example).
4. Review [jangus-worker.service](../../deploy/systemd/jangus-worker.service)
   (dedicated user, restrictive sandbox, 127.0.0.1 binding, local-only IP
   allowance) before installing/enabling it. The unit is a **template**;
   do not assume that this repo has installed anything.
5. Use SSH host-key verification. From PowerShell, with your **reviewed**
   SSH target and no password prompts disabled:

```powershell
./scripts/windows/open-remote-tunnel.ps1 -HostAlias YOUR_VERIFIED_SSH_HOST
```

6. Supply the same private application token to the owner-operated client
   process via `JANGUS_REMOTE_TOKEN` in its environment, without pasting
   the real value in shell history. Use a private desktop state directory
   **outside** all Git checkouts.

```powershell
$stateDir = Join-Path $env:USERPROFILE "jangus-remote-state"
uv run --locked python scripts/remote_client.py --state-dir $stateDir prepare --synthetic-text "synthetic-only"
# Copy job_id from the output; the prepare step makes no network call.
uv run --locked python scripts/remote_client.py --state-dir $stateDir reconcile JOB_ID
# Re-run reconcile after network loss using exactly the same JOB_ID.
uv run --locked python scripts/remote_client.py --state-dir $stateDir cancel JOB_ID
```

Cancellation is a persisted request, not proof that a verified prior effect
was undone. The service continues work after the SSH connection drops.
No GitHub Actions job is the runtime job bus.

## Real acceptance still required

On the actual Ubuntu VPS, independently demonstrate: authenticated tunnel
access and rejected public-network access; one real synthetic result; loss of
the POST acknowledgement followed by the same persisted ID and no duplicated
effect; disconnect/reconnect; actual systemd service interruption immediately
before and after effect; resumed verification and attempt accounting; a
partial output that stays uncertain; cancellation; audit/backup, storage
permissions and artifact integrity. Record machine, installed version,
service identity, exact installed branch SHA, run/job IDs, ledger identity preservation, sanitized transition audit, expected/actual evidence, and
owner approval before claiming task 26 complete.

No real machine, host address, SSH key or token belongs in the repository.
No Phase 8 consultants or external-model integration is enabled.

## Quota, retention and timeouts (audit batch F)

At most 500 jobs may be non-terminal (queued, intent, waiting) at once, and at
most 5000 are retained in total. Both limits fail with `job_quota`. Reclaim
space with `python -m jangus.remote.service --state-dir DIR --prune-older-than-days N`,
which deletes only terminal jobs (with their artifacts and events) and exits. A
pruned job's idempotency key is forgotten, so replaying the same submission
afterwards is accepted as new work. Each HTTP connection has a 10-second socket
timeout, and the health check takes the ledger's thread lock like every other
operation.
