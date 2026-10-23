# Submit and reconcile a private VPS job

**Trigger:** An owner requests a bounded, registered synthetic remote verification.
**Inputs:** Explicit owner-accepted payload, stable remote job identity, private
client journal, SSH tunnel, server version and evidence references.
**Platform:** Windows desktop client, Ubuntu 22.04 VPS worker.
**Required tools:** None. This is an owner-operated CLI/network workflow, not
a grant of direct shell or network capability to a model.

## Procedure

1. Verify that the trusted SSH host identity is pinned and that a loopback-only
   tunnel is active. Never expose the server port on the public network.
2. Prepare a synthetic job with `scripts/remote_client.py prepare`; its UUID,
   idempotency key and payload digest commit to the private outbox **before**
   the first network request.
3. Use `reconcile JOB_ID` to GET status first, then submit the exact persisted
   request only if the server confirms that job ID is absent.
4. Record the server's version, job and idempotency identities, request digest,
   verified result digest, and explicit queued/intent/waiting/terminal state.
5. If the acknowledgement or transport is lost, reconnect using the **same ID**,
   never a new one. Inspect whether a persisted intent has a verified effect.
6. For cancellation, request `cancel JOB_ID` and reconcile again. A cancellation
   may coexist with a previously completed verified file effect.

## Stop and escalation

Reject unsupported job kinds, unexpected ID/digest/result, authentication failure,
broken SSH host verification, changed request body, exhausted attempts, a stale
protocol and uncertain output. Do not delete remote artifacts, overwrite a
partial file or call an unregistered tool. No model may approve new remote job
kinds, execute arbitrary shell commands or bypass the owner operator.
