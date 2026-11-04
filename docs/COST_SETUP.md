# Cost setup: a dedicated key, a provider-side limit, and monthly reconciliation

Jangus meters and caps its own spending (TOKEN_ECONOMY.md), but its counters are only as good as its code. Two independent checks sit outside it. Do both before enabling Claude (`claude_enabled = true`).

**Since 2026-11-04 this page is required, not optional.** Claude Haiku over the direct Anthropic API is the default cheap model for routine specialist work ([ADR-002](decisions/002-claude-direct-default-model.md)); the local Ollama model is off by default. So the cheapest model rung costs money, and nothing with `runner: model` can run until steps 1.1 to 1.5 below are done. Kernel-run tasks, recipes and watchers still cost nothing and still come first.

## 1. A dedicated API key with a limit set at the provider
1. In your Anthropic Console, create a **separate key for Jangus** (and a separate workspace or project for it if the console offers one). Do not reuse a key you use elsewhere. Menu names change, so check the console's current wording.
2. Set a **monthly spend limit** on that workspace or key at the provider. This is the real backstop: it holds even if Jangus is wrong.
3. Store the key in Windows Credential Manager under the name `anthropic_api_key` (service `jangus`). Never put it in a file in the repository or in `jangus.toml`.
4. Put your plan's monthly limit in `jangus.toml` so Jangus stops *short* of it:

```toml
[caps]
plan_limit_usd = 50        # what your plan or the provider-side limit allows per month
monthly_percent = 80       # Jangus will not plan to spend more than 80% of it (default 80)
daily_percent = 5          # and no more than 5% of it in one day (default 5)
```

Without a `[caps]` table no paid call is allowed. The optional local (Ollama) provider is free and is never affected by these caps — but with the Claude-direct default, routine work is paid work, so pick `plan_limit_usd` with that in mind rather than assuming only escalations cost anything.

5. Write `prices.yaml` in your data folder from the provider's current price list. The format is documented in `src/jangus/agents/pricing.py`. A paid model with no price entry is refused, and a table older than 60 days raises an alert.

```yaml
as_of: 2026-11-04              # the day you copied these numbers
models:
  # PLACEHOLDER - FILL THESE IN FROM THE CONSOLE (task 43).
  # The agent deliberately did not write a model id or a price here: an id or a
  # price from memory would be a guess, and a wrong id fails at the first call
  # while a wrong price makes every cap and every report wrong. Read the current
  # Haiku model id and its per-million prices from your Anthropic Console, or
  # from https://docs.claude.com (Models / Pricing), and replace the line below.
  claude/<CURRENT-HAIKU-MODEL-ID>:
    input_per_mtok: <INPUT $ PER 1M TOKENS>
    output_per_mtok: <OUTPUT $ PER 1M TOKENS>
    cached_per_mtok: <CACHE-READ $ PER 1M TOKENS>
    cache_write_per_mtok: <CACHE-WRITE $ PER 1M TOKENS>
  # Add the Sonnet model you use for the single step-up, the same way.
  # ollama/<tag>: free        # only if you enable the optional local provider
```

Use the **same** model id string in the agent spec's `model.name`, so the price lookup (`provider/model`) matches. If the id and the price entry disagree, the call is refused with `PRICE_MISSING` rather than billed at a guess, which is the intended failure.

6. **The local provider stays off unless you want it.** A Claude-direct install needs no `[model]` table at all. To use a local Ollama model for some agent anyway, add the loopback endpoint and turn it on explicitly:

```toml
[model]
url = "http://127.0.0.1:11434"   # loopback only
name = "<your ollama tag>"

ollama_enabled = true            # without this the local provider is not enabled
```

Neither provider is enabled by default, and nothing switches between them on its own: a failing or capped provider stops and alerts.

## 2. Monthly reconciliation against the provider's own numbers
Once a month, download the usage export for the month from the console (CSV or JSON), then run:

```
jangus cost reconcile <export file> --month 2026-11
```

It compares the ledger's total for that month with the export and raises an alert when they differ by more than 5%. It exits with status 1 on an alert. A gap means one of two things: the ledger missed a call, or the provider billed something Jangus did not send (for example a leaked key). Treat a gap as urgent, and rotate the key if unsure.

The export needs a cost column named `cost_usd`, `cost`, `amount_usd` or `amount`, and ideally a `date` column starting with an ISO date so rows from other months are skipped. Months follow the ledger's UTC timestamps, so an entry near midnight at the month boundary can land on either side; allow for that when the gap is small.

## 3. Day to day
- `jangus cost report [--month YYYY-MM]`: calls, tokens and spend per role, run and agent, from the ledger.
- `jangus report`: the daily report. `jangus status`: recent alerts, including spend alerts at 50%, 80% and 100% of the daily and monthly caps.
- At 100% of a cap the paid provider is blocked until you act; nothing silently switches model.

Nothing in this file has been tried against a real provider export yet; the export columns above are the documented expectation, so tell the agent if your file looks different.

## What is not verified here
The Haiku model id and its prices are placeholders: the agent could not reach https://docs.claude.com in the session that wrote this page, and CLAUDE.md rule 14 forbids writing a model id or a price from memory. Nothing in the repo depends on those values until you fill them in, and an unpriced paid model is refused rather than guessed at. See task 43.
