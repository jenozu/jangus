# Verified file workflow

**Trigger:** An approved synthetic input manifest requires bounded copy/report actions.
**Inputs:** Owner-supplied immutable manifest and workspace; allowlisted file tools; current limits.
**Platforms:** Windows and Ubuntu with private local filesystems.
**Required tools:** `file.copy`, `file.report`.

## Procedure
1. Read the current owner manifest and its verified expected hashes; never infer missing expected outputs.
2. Inspect the queued controller plan and verify the action IDs, dependencies, tool scope, and limits.
3. Ask the local model to return only the existing action IDs in their prescribed order.
4. Reject additional actions, changed arguments, expanded tools, or contradictory dependencies.
5. Let the controller record each intent before executing. Let the independent verifier judge bytes/hashes.
6. Surface persisted run status and evidence IDs; a model success message is not proof.

## Stop and escalation
Refuse unsafe paths, missing tools, stale policy revisions, exceeded budgets and uncertain writes. Never overwrite an uncertain output or silently request another provider.
