# Parking lot

Ideas that are not part of the current plan. Review at the end of each phase: promote, defer or drop.


- Ledger tail anchor: the hash chain detects edits but not deletion of the newest entries. Store the latest hash/seq outside the ledger file (e.g. in the state DB, or HMAC-signed) to detect truncation. Raised in task 5.

- Reconcile callables do not receive secrets yet; real Shopify/email tools (Phase 3) will need them, with the same allow-list check. Raised in task 9.
