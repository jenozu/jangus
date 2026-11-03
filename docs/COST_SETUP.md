# Cost setup: a dedicated key, a provider-side limit, and monthly reconciliation

Jangus meters and caps its own spending (TOKEN_ECONOMY.md), but its counters are only as good as its code. Two independent checks sit outside it. Do both before enabling Claude (`claude_enabled = true`).

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

Without a `[caps]` table no paid call is allowed. Local (Ollama) models are free and are never affected by these caps.

5. Write `prices.yaml` in your data folder from the provider's current price list. The format is documented in `src/jangus/agents/pricing.py`. A paid model with no price entry is refused, and a table older than 60 days raises an alert.

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
