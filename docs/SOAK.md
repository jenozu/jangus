# Seven-day soak (task 21, owner task)

Gate B15: seven consecutive days of the nightly backup running with **zero manual fixes** and **zero unresolved effects**. Nothing in this file has been run on a real machine yet; the first honest result is yours.

## Before day 1
1. Merge the open PRs (see MEMORY.md) and pull `main`. Run `uv sync --locked` and `uv run --locked pytest`; send back any failure.
2. Copy `agents/drafts/backup-maintainer.yaml` and `agents/drafts/backup-maintainer.directive.json` into your specs folder (default `<data_dir>\specs`). **Edit `required_paths` in the spec and the two `src_root`/`dst_root` values in the directive to your real folders, identically.** The file tools can touch only the spec's `required_paths`.
3. `jangus spec approve <path to the spec>` and type `yes`. Any later edit to the spec makes it inactive until re-approved.
4. Run `jangus tick` by hand once (it only records a baseline), then check `jangus status`.
5. Register the schedule: `.\scripts\install_task.ps1 -Config C:\path\to\jangus.toml`. The script is untested; read it first.
6. Make sure the backup drive is connected at night. If it is not, the run is refused with an alert and runs on the next tick once it is back.

## Each day (2 minutes)
- `jangus status`: approved specs, recent alerts, halted directives.
- `jangus report`: yesterday's totals. The nightly backup should show **0 model calls and 0 tokens**.
- Check the newest file in `<data_dir>\reports\`.

## Log every manual fix
A manual fix is anything you had to do to keep it running (re-approving a spec, plugging in a drive after an alert, deleting a lock file, restarting). Write one line per fix: date, what happened, what you did. **One entry resets the seven days** only if the system could not have recovered by itself; say which.

| Day | Date | Backup completed? | Unresolved effects | Manual fix (or none) |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |
| 6 | | | | |
| 7 | | | | |

## When done
Send the table, the output of `jangus report` for each day, and any alert text. The agent records the result in `docs/reviews/PHASE_3.md`; the gate passes only on seven clean days.
