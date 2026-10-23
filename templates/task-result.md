# Task result

- Taskmaster ID and title:
- Tested commit:
- Result: passed / failed / blocked / partial
- Outputs and exact paths:
- Changes and requirement coverage:
- Verification commands, environment, and observed results:
- Evidence references (sanitized files or repository CI URLs):
- Failures, recovery attempts, and unresolved issues:
- Measured resource usage (label estimates separately):
- Actual target-machine checks versus simulated checks:
- Remaining work and handoff context:
- PR URL and merge state:

For completion, prepare `.taskmaster/evidence/ID.json` using the schema in
[the integration guide](../docs/development/TASKMASTER.md), then request
`set_task_status` with `done`. A report or successful model message alone is
not verification. Keep development completion separate from PR merge state.
