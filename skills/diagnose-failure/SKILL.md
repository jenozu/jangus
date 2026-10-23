# Diagnose a failed or uncertain run

**Trigger:** A verified controller reports failed, waiting, or interrupted action status.
**Inputs:** Correlated run ID, sanitized status/event codes, verified evidence references.
**Platforms:** Windows and Ubuntu.
**Required tools:** None (read-only guidance).

## Procedure
1. Read the canonical run and action states and original remaining counters.
2. Distinguish a missing output from an output that may have been partially written.
3. Use existing verification receipts to establish what is known, not model speculation.
4. Recommend only recovery paths allowed by the controller's existing policy.
5. For unproven side effects, preserve uncertainty and request explicit human review.

## Stop and escalation
Never reset attempts, deadlines, permissions or run identity. Raw exception strings, private data and diagnostic exports are not consultation inputs.
