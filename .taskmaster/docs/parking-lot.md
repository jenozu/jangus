# Parking lot

Ideas that are not part of the current plan. Review at the end of each phase: promote, defer or drop.


- Ledger tail anchor: the hash chain detects edits but not deletion of the newest entries. Store the latest hash/seq outside the ledger file (e.g. in the state DB, or HMAC-signed) to detect truncation. Raised in task 5.

- Reconcile callables do not receive secrets yet; real Shopify/email tools (Phase 3) will need them, with the same allow-list check. Raised in task 9.

- Config `host` field: `check_host` takes the running host as a parameter; the config has no `host` key yet, so the scheduler (Phase 3) must supply it. Registry and ledger file names (`spec-approvals.json`, `ledger.jsonl` under `data_dir`) are chosen in cli.py; centralize them when the scheduler and runtime need the same paths. Raised in task 12.
