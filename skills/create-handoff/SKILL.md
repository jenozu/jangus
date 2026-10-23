# Create a durable session handoff

**Trigger:** A bounded agent context needs rollover or a worker returns partial results.
**Inputs:** Authoritative run ID, controller status, exact rules/skill revisions.
**Platforms:** Windows and Ubuntu.
**Required tools:** None (local handoff storage only).

## Procedure
1. Read progress, verified evidence, cancelled/uncertain actions and counters from SQLite through the controller.
2. Save a compact handoff under the private data directory using the atomic handoff writer.
3. Record relevant skill SHA-256 revisions and the reviewed rules revision.
4. On return, re-read the actual controller status and compare immutable revisions.
5. Use current state and remaining budgets, never counters from a serialized summary.

## Stop and escalation
Reject modified review revisions and incompatible run IDs. Do not revive terminal runs or remove a failed attempt while rotating contexts.
