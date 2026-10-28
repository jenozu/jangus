# Cross-phase acceptance-readiness checkpoint (draft)

This is **not** Phase 9. The approved development roadmap ends at Phase 8.
The release-readiness branch is stacked on draft Phase 8 PR #8. No prior
phase PR was merged, no application was deployed, and no credentials or paid
model access were used.

## Developer-only deliverables

- `jangus.release.readiness` parses the existing Taskmaster 30-task backlog,
  validates task identifiers, dependency closure and cycles, and reports
  the Foundation, Local MVP, VPS and Optional task-status gates. The read-only
  CLI `scripts/check_release_readiness.py` may assert task-status completion
  for one gate. It **cannot** attest desktop benchmarks, human decisions,
  real VPS access, or provider billing. No Taskmaster state was changed.
- `scripts/windows/check-owner-readiness.ps1` reports only locally present
  Git/Python/uv/Ollama/SSH commands and the presence of an apparent Jangus
  checkout. It does **not** run inference, contact a host, scan secrets,
  install software, or claim owner acceptance.
- `tests/release/test_cross_phase_rehearsal.py` covers approved *synthetic*
  memory -> isolated mock planner/worker/verifier -> verified file outputs ->
  unapproved draft-only memory -> separate registered private **loopback**
  HTTP job and durable client reconciliation -> default-disabled optional
  consultant with **no outbound calls**. This is one synthetic boundary
  rehearsal; the subsystems remain independent by design.
- `tests/release/test_readiness.py` validates malformed backlog rejection,
  task/dependency consistency, impossible completion and cycle handling,
  and demonstrates that even an artificially all-done backlog does not
  produce real-machine acceptance evidence.
- The Windows setup guide now checks out the provisional latest stacked
  branch rather than the obsolete Phase 2 branch. README and developer
  guides explicitly distinguish CI checks from real acceptance.
- [Owner acceptance handoff](../development/ACCEPTANCE_HANDOFF.md) records
  exact synthetic commands, owner-machine model and vault checks, review
  credentials privacy, VPS steps, and optional consultation decisions.

## Hosted verification

GitHub Actions results for commit `a40027329e12175b789cd2a930bdf010ee72c4df`
(`release/acceptance-readiness`, PR #9):

| Run | Event | Result |
|---|---|---|
| [40342885364](https://github.com/jenozu/jangus/actions/runs/40342885364) | push | all six jobs succeeded |
| [40342907740](https://github.com/jenozu/jangus/actions/runs/40342907740) | pull_request | all six jobs succeeded |

The six jobs are Ubuntu 22.04 and Windows on Python 3.12 and 3.13, plus the
Taskmaster MCP job on Ubuntu and Windows. This records hosted CI only. Per-job
test counts were not captured here; open the run for them. A green run does not
constitute release approval or owner-machine acceptance.

## Remaining owner gates

Taskmaster task 12 is still in progress (real local model measurement).
Tasks 13–27 remain pending in dependency order, with owner-machine A09–A17
and actual VPS acceptance not performed; optional 28–30 remain deferred.
No task is marked complete by a document, synthetic test, readiness script,
or CI result. The user/owner must explicitly approve credentials, host,
reviewer identity, private note sharing, optional API cost, and merges.
