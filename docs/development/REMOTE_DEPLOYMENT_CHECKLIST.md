# Private VPS worker — operator acceptance checklist

This document is a **proposed owner-operated** procedure, not evidence that a
VPS was touched. No deployment tool or CI job may enable the worker or copy
credentials automatically. All real tests require the owner to choose a host,
confirm an authorized maintenance window, and inspect expected versus actual
evidence. Keep logs and credentials outside the Git repository.

## 1. Review the installation offline

- Review the pinned wheel, lockfile and branch SHA. Only install an explicitly
  accepted artifact, never unverified downloaded code.
- Verify an Ubuntu 22.04+ account named `jangus-worker` exists with **no
  sudo/root privileges**. The unit requires exactly that user and group.
- Verify `/var/lib/jangus` is a private directory owned by this user
  (mode 0700) and the wheel is installed in `/opt/jangus/.venv`.
- A unique per-host `JANGUS_REMOTE_TOKEN` belongs in
  `/etc/jangus/worker.env`, owned by root with mode 0600. Never paste or print
  the token in chat, CI, diagnostics, or shell history.
- Review `deploy/systemd/jangus-worker.service` before installing it as
  `/etc/systemd/system/jangus-worker.service`. Do not replace an existing
  unit without separate rollback approval.

The new state format includes an independent **ledger identity** file,
`ledger-identity.json`, bound to the private SQLite `meta` record.
**State schema v2 is not an automatic migration of any prior v1 database.**
If an old state directory exists, stop: preserve the SQLite file, WAL, SHM,
artifacts and associated metadata, and plan a separate audited migration.
Never delete files to bypass a mismatch. A lost identity, missing database or
orphaned artifact **fails closed**. The identity is not a tamper-proof
anti-rollback counter: privileged attackers able to replace both files
remain outside the current threat model.

## 2. Verify local host permissions and unit policy, without writing

After the owner has provisioned (but **before enabling the service**), run
on the chosen Ubuntu host with the required read access:

```console
sudo /opt/jangus/.venv/bin/python /opt/jangus/scripts/linux/check_remote_host.py
```

This performs bounded unit-content/privilege checks, confirms the
`jangus-worker` account is non-root, verifies the private state directory is
owned by that account and not group/world accessible, and verifies the
root-owned token file has no group/world permissions. It **never reads token
contents** or edits systemd, sshd, firewall, service files or state.
The output is a minimal status or a stable error code; any failed check blocks
activation. The command path is illustrative: use the actual installed,
reviewed release path and don't execute a git checkout as a service.

Separate operator checks (not safely automated by this script): SSH server
security, host fingerprint verified out of band, firewall/ingress with **no
public port 8765**, outbound restrictions, systemd privilege/drop-in changes,
resource/disk budgets and backup/restore policy.

## 3. Review systemd and SSH tunnel

On the actual host, inspect the installed service:

```console
sudo systemctl cat jangus-worker.service
sudo systemd-analyze verify /etc/systemd/system/jangus-worker.service
```

The reviewed unit uses a dedicated account, `ProtectSystem=strict`,
`NoNewPrivileges=true`, `IPAddressDeny=any` with localhost allowance,
and launches a worker bound **only to 127.0.0.1:8765**. If the real host
cannot support a systemd restriction, do **not** silently relax it.

From the Windows desktop, after manually verifying the SSH host key and
configuring a trusted OpenSSH host alias:

```powershell
./scripts/windows/open-remote-tunnel.ps1 -HostAlias YOUR_VERIFIED_HOST_ALIAS
```

This requires strict host-key checking, batch mode, the local
`127.0.0.1:8765` bind, and successful port forwarding before continuing.
It does **not** auto-enroll unknown host keys. Owner approval is required
before any real connection.

## 4. Owner-directed live acceptance (not run by CI)

Using **synthetic-only text**, in a private workstation journal:

1. Confirm authenticated `health` through the tunnel and reject access
   without a token. Independently verify port 8765 is **not exposed publicly**.
2. Run `prepare` then `reconcile`; record the immutable job ID and request
   digest and eventually the content/result digests.
3. Simulate a lost ACK by closing the client before recording a submit
   response, then re-run `reconcile`. Verify exactly one registered job and
   exactly one matching artifact under the original ID.
4. Break only the SSH tunnel, then reconnect to the **same verified host**.
   Verify the job still exists without changing its idempotency key.
5. With an approved maintenance window, interrupt the worker process before
   and after a synthetic file effect (including a forced kill), then restart
   under systemd. Confirm the persisted attempt budget and event history,
   no overwrite of an uncertain partial artifact, and exact digest matching.
6. Cancel queued, intent and uncertain-output jobs, confirming that an
   uncertain output is **not reported as a verified success**. A cancellation
   after a verified effect must retain the verified digest; it does not undo it.
7. Observe a deliberately stopped worker health endpoint returning failure,
   and confirm the watchdog exits so `Restart=on-failure` can restart it.
8. Review audit records privately, e.g.:

```console
sudo -u jangus-worker /opt/jangus/.venv/bin/python /opt/jangus/scripts/linux/inspect_remote_job.py --state-dir /var/lib/jangus/remote JOB_UUID
```

The inspection script returns only sanitized job metadata, digests, state and
transition events, **not source text or authentication tokens**. Never export
a full SQLite database or raw logs as acceptance evidence without review.

## 5. Release evidence and stop conditions

Record branch/wheel SHA, OS details, service identity and policy, independent
host-key verification outcome, port exposure result, synthetic job IDs,
expected/actual attempts, cancellation, result hashes, event counts, and
owner sign-off in private records. Do **not** treat CI localhost tests as VPS
operation. On any missing identity, artifact mismatch, unexpected public port,
unexpected permission, unknown service drop-in, or protocol disagreement,
**stop without resetting or deleting state**.

A prior rollback of both the SQLite database and identity file is not
independently detected; a separately controlled backup/attestation boundary is
required for a stronger production guarantee. No generic file worker, model,
shell executor or paid consulting service is added by this phase.
