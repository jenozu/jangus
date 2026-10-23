# Export safe diagnostics

**Trigger:** An owner requests a shareable failure or execution report.
**Inputs:** An existing run ID; a new destination outside Git and workspaces.
**Platforms:** Windows and Ubuntu.
**Required tools:** None (uses the existing owner-operated CLI).

## Procedure
1. Check the selected run identity and destination; never overwrite an existing export.
2. Use the controller's explicit `jangus run export` command with private configuration.
3. Verify that summary, ordered events, verification, checksums and omissions are present.
4. Check that raw filenames, input content, secrets and private state were omitted.
5. Supply a minimal evidence reference, not a raw SQLite backup.

## Stop and escalation
Do not transmit a raw backup or weaken the disclosure boundary to obtain more context. If export fails, report the error code and stop.
