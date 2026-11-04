# Parking lot

Ideas that are not part of the current plan. Review at the end of each phase: promote, defer or drop.


- Ledger tail anchor: the hash chain detects edits but not deletion of the newest entries. Store the latest hash/seq outside the ledger file (e.g. in the state DB, or HMAC-signed) to detect truncation. Raised in task 5.

- Reconcile callables do not receive secrets yet; real Shopify/email tools (Phase 3) will need them, with the same allow-list check. Raised in task 9.

- Config `host` field: `check_host` takes the running host as a parameter; the config has no `host` key yet, so the scheduler (Phase 3) must supply it. Registry and ledger file names (`spec-approvals.json`, `ledger.jsonl` under `data_dir`) are chosen in cli.py; centralize them when the scheduler and runtime need the same paths. Raised in task 12.

- Provider batch mode (Anthropic Message Batches) is not implemented: nothing to measure yet and it cannot be tested without a real key. Add after task 17 shows what routine calls cost. Also: `effort` from specs is not sent to the Claude API yet; check the current API parameter before wiring it. Raised in task 13.
- Claude and Ollama request/response field names were written from knowledge and tested only against fakes. The owner-run benchmark (task 17) is the first real Ollama check; the first real Claude call needs the API key (Phase 2b). Raised in task 13.

- Benchmark and "thinking" models: some Ollama models spend `num_predict` tokens on hidden reasoning, which would show up as `truncated` and parse failures in the benchmark. If the owner's model does this, check Ollama's current option for turning thinking off before changing the request. Raised in task 17.

## Added 2026-11-03 (Phase 2b)
- A `jangus run` / `jangus status` / `jangus unblock` command set: nothing yet starts a run, wires `caps` and `prices.yaml` into the gate, or shows queued alerts (Phase 3 task 19 area).
- Per-task verifiers for model tasks (today a model task without one ends in `review`); first real ones come with the file/backup agent.
- Calibrate the bytes/3 token estimator against measured provider usage once real calls exist; the drift alert already flags a gap over 30%.
- Batch endpoints and measured prompt-cache savings (TOKEN_ECONOMY.md section 7.8) only after real usage data.
- Move alert delivery beyond ledger entries (push/email) with the Phase 4 alert channel.
- Cache hit rate and tokens saved in the daily report (the data is in `cache_hit` ledger records).

## Added 2026-11-03 (Phase 3)
- Cron uses local wall-clock time; a time-zone or DST change can shift or double a slot. Decide a policy before the soak if the PC travels.
- A halted directive waits for the owner and is never retried by later ticks; there is no `jangus resume` or `jangus unblock` command yet.
- `jangus tick` takes the folders from the spec's `required_paths`; a friendlier `jangus spec new` that writes and checks the two draft files together would reduce edit mistakes.
- Reconciliation assumes export column names; adjust after seeing a real file.
- Per-task verifiers for model tasks (carried over from Phase 2b) are still open.

## Added 2026-11-04 (Claude-direct default)
- **OpenRouter as a model route: considered and REJECTED for now** (owner decision 2026-11-04, ADR-002). It came up as a cheaper or more flexible way to reach small models when local models were dropped. Reasons for rejecting it:
  1. **It needs a new provider adapter.** Today there are exactly two providers behind one interface (`claude`, `ollama`); OpenRouter would be a third, with its own request/response and usage fields to map, its own errors, and its own tests.
  2. **Its routing and fallback fight rule 7.** OpenRouter's selling point is upstream routing between providers and automatic fallback. Jangus must never silently switch provider or model (PRD non-goal, ADR-001 item 5), so that behaviour would have to be pinned off, which removes most of the benefit while leaving the risk that a future default turns it back on.
  3. **An extra party sees the data.** Shop order data and, in Phase 6, email text would pass through OpenRouter in addition to the model vendor. That is a second trust decision on top of the one ADR-002 already accepts.
  4. **Nothing here was checked against OpenRouter's current documentation.** The three points above are reasoning about the design, not verified behaviour; anyone promoting this idea must read their current docs on routing, fallback, data handling and usage reporting first.
  Revisit only if the direct API's cost or availability becomes a real problem, and then with a spike that answers point 4 and shows fallback can be fully disabled.
- If a faster PC or a GPU VPS appears, the optional local provider is the obvious cheap rung again: `scripts/benchmark_ollama.py` and the provider are still in the repo, and `ollama_enabled` plus a loopback endpoint is all it takes.
- A per-agent "keep this local" rule, enforced by the kernel from the spec rather than by convention. Lower priority after 2026-11-04: the owner accepted the cloud trade-off for the mailbox too, so this is no longer a Phase 6 precondition. Still worth having if a later agent handles data that must not leave the machine.
