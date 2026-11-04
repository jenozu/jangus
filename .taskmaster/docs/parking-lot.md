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

## Added 2026-11-04 (DeepSeek Harness review)
- Building on DeepSeek Harness (`dsh`, deepseek-ai/deepseek-harness). Reviewed in docs/reviews/DSH_HARNESS_REVIEW.md; recommendation: borrow ideas only, no dependency. Options (b) separate tool and (c) agent runtime conflict with rules 1, 7, 12 and need an ADR plus owner approval. Candidate ideas, each for its own phase: "model-visible means logged" as a tested invariant; read-only-by-default posture for file tools; keyless recorded-session replay tests for LLM agents; tool catalog generated from tool schemas. Owner to decide.
