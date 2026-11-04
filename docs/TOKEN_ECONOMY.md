# Token economy: measuring, limiting and avoiding model cost

Status: DRAFT specification. Nothing here is implemented. Token and spend use is the primary operating constraint of Jangus (V2-NFR08).

## Principles
1. The cheapest token is the one never spent: script it, cache it, or detect "nothing changed" without a model.
2. Every model call is metered, attributed to a role and a reason, and checked against a budget before it is sent.
3. Cost discipline is enforced by the kernel. No agent can raise a limit, pick a model, or hide a call.
4. Estimates are labelled as estimates; measured usage comes from provider responses.

## 1. Metering (every call, in the ledger)
The kernel wraps every provider call and writes one ledger record:

| Field | Meaning |
|---|---|
| `role` | `kernel-run` (always 0), `specialist`, `step-up`, `orchestrator` |
| `reason` | why the call happened: `task`, `step_up`, `new_goal`, `spec_draft`, `halt_escalation` |
| `run_id`, `directive_id`, `task_id`, `attempt` | correlation |
| `provider`, `model`, `effort` | what answered |
| `input_tokens`, `output_tokens`, `cached_tokens` | **measured**, from the provider's usage fields (Anthropic response usage; Ollama prompt/eval counts) |
| `cost_usd` | computed from the price table; 0 for local models |
| `est_input_tokens` | the pre-call estimate, kept to calibrate the estimator |
| `latency_ms`, `outcome` | for tuning |

A call with no usage data from the provider is recorded as `usage_unknown` and charged at the pre-call estimate (conservative), and is flagged in the report.

## 2. Price table
`config/prices.yaml` (owner-maintained, versioned in the private config directory): per model, price per million input, output and cached tokens, with an `as_of` date. The kernel warns if the table is older than 60 days or a used model is missing, and refuses calls to a model with no price entry (fail closed for paid providers).

## 3. Pre-call budget check
Before sending, the kernel estimates input tokens (bytes/3 until calibrated against measured values), adds `max_tokens`, prices the worst case, and refuses the call if it would exceed any of: the task limit, the directive budget, the agent's daily budget, the daily orchestrator cap, or the monthly cap. A refused call counts as a failed attempt and raises an alert; it is never silently downgraded to another model.

## 4. Spend caps and alerts
**Caps are percentages of the owner's plan.** The config `[caps]` table holds `plan_limit_usd` (what the plan or the provider-side limit allots per month) plus `monthly_percent` (default 80) and `daily_percent` (default 5), both of the plan limit and both at most 100; the daily cap cannot exceed the monthly one. Jangus therefore stops short of the limit, never at it. Without a `[caps]` table no paid call is allowed. The dedicated key's provider-side limit remains the independent backstop.

Thresholds on daily and monthly spend and on orchestrator calls: 50%, 80% and 100%. At 100% the provider is blocked until the owner acts. Also:
- **Big-call flag**: any single call over `big_call_tokens` (default 20,000) or `big_call_usd`.
- **Runaway flag**: a task using over 3x the median tokens of its type.
- **Step-up and escalation log**: every step-up and every orchestrator call listed with its reason.
- **Stale price table**, **usage unknown**, and **estimator drift** (estimate vs measured off by more than 30%).
Alerts appear in `jangus status` and the daily report. Push notifications are a later phase.

## 5. Reports
Daily report (generated from the ledger by the kernel, no model): calls, tokens and spend per role, per agent and per task type; orchestrator calls with reasons; step-ups; cache hits; recipe runs; estimated tokens saved (labelled estimate); top 5 costliest tasks. Monthly: the same plus reconciliation.

## 6. Reconciliation and external backstop
- Use a **dedicated API key** for Jangus, in its own workspace/project if the provider offers it, and set a spend limit there. This is a backstop independent of our code.
- Monthly, `jangus cost reconcile` compares the ledger total with the provider's usage export (owner supplies the file) and reports the difference. A gap above 5% is an alert.

## 7. Ways tokens are avoided (in order of effect)
1. **Recipes**: solved tasks compiled into approved scripts ([RECIPES.md](RECIPES.md)).
2. **Kernel-run tasks**: tasks written as scripted steps from the start.
3. **Watchers**: cheap, kernel-evaluated checks (new order since last cursor, new mail matching a rule, file changed by hash) decide whether a model runs at all. No change means zero calls.
4. **Rules before models**: deterministic filters and regex classify what they can; the model sees only the remainder.
5. **Result cache**: results keyed by (task type, input content hash, spec hash, prompt version). A hit costs no call. Entries expire by TTL and invalidate when the spec or prompt changes.
6. **Minimal context**: each task gets only the inputs it needs, with a per-task `max_input_tokens`. Large tool outputs are summarized by the kernel (counts, diffs, top items) before the model sees them. The orchestrator receives kernel-written summaries, never raw logs.
7. **Short structured output**: JSON proposals, tight `max_tokens`, no prose reasoning requested.
8. **Provider features where available**: put the stable role/spec prefix first so prompt caching applies; use batch endpoints for non-urgent work (typically cheaper). The provider interface exposes both; each is used only after measurement shows a saving.
9. **Local first**: local Ollama on the PC or VPS for routine judgment (see placement in ARCHITECTURE.md).
10. **Batching items**: classify many items in one call when latency does not matter.

## 8. What the kernel never does
Never calls a model to confirm completion, summarize a finished run, or write a report. Never retries beyond the attempt ladder. Never switches model or provider silently.

## Scenarios
See B37-B47 in [MVP.md](MVP.md).
