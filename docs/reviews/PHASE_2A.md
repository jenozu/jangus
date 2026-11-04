# Phase 2a review: specs, providers, benchmark script (tasks 12, 13, 17)

Date: 2026-11-02/03. Scope: agent spec schema, loader and approval registry (task 12, PR #13); model providers (task 13, PR #15); local model benchmark script (task 17, this PR). Each merged to `main` only after CI was green on ubuntu-latest and windows-latest. Task 35 (README status fix, PR #12) was a docs-only corrective.

> **Later note (2026-11-04):** this is a dated record of what was built and is left as written. The model decision it assumed has since changed: Claude Haiku over the direct Anthropic API is now the default cheap model and the local Ollama provider is optional and off by default ([ADR-002](../decisions/002-claude-direct-default-model.md), and the gate change in [PHASES.md](../PHASES.md)). The Ollama benchmark described here is no longer required to continue.

## What is real and what is fake

| Piece | Evidence | Real hardware / service? |
|---|---|---|
| Spec loader, hash, approval registry, CLI | pytest, Linux and CI on Windows | Real files on a temp dir; **no** real owner registry yet |
| B35 host check | pytest with fake secret and path lookups | No real host, secret store or VPS |
| Ollama provider | pytest with a **fake transport**, plus the real urllib transport against a throwaway 127.0.0.1 server | **Not run against a real Ollama** |
| Claude provider | pytest with a **fake transport** | **Not run against the real API**; field names written from knowledge, docs fetch was not approved |
| Benchmark script | pytest end to end against a **fake Ollama HTTP server** | **Not run against a real model**; the owner runs it |
| Windows | CI `windows-latest`; owner reported the Phase 1 suite passing on the PC | Phase 2a suite not yet run on the PC |

## Commands (Linux, Python 3.12, merged state plus task 17 branch)

```
uv run --locked ruff check     -> All checks passed!
uv run --locked mypy           -> Success: no issues found in 24 source files   (strict on jangus.kernel)
uv run --locked pytest --cov=jangus
                               -> 239 passed; total coverage 95%
python3 tools/taskmaster/tasks.py validate -> OK (warnings only: later tasks not yet expanded)
```

Coverage of new modules: providers/base 100%, factory 100%, http 100%, ollama 100%, claude 95%, spec 96%, spec_registry 94%, cli 99%, benchmark 92%.

## Scenario results

| ID | Result | Tests |
|---|---|---|
| B09 Spec approval | PASS | `test_agent_specs`: unapproved spec cannot run; editing an approved spec (even adding a newline) makes it inactive; re-approval replaces the old hash (one active hash per name); corrupt registry fails closed; ledger event is written before the registry changes and an unwritable ledger means no approval |
| B35 Host check | PASS (simulated) | `test_b35_host_requirements`: wrong host, missing secret, missing path each refuse |
| B11 Provider rules | PASS (fake transports) | `test_providers`: a failing provider raises and alerts, the other provider is never called (both directions); a disabled provider is never called; Claude is off unless `claude_enabled`; enabled Claude with no stored key stops and does not use Ollama |
| B37 Usage fields | PARTIAL | Providers return measured input/output/cached/cache-write tokens; absent counts become `usage_unknown` (`Usage.known` False). The per-call **ledger record** with role, reason and cost is not built yet (budget and metering tasks) |

Also covered: YAML anchors/aliases, duplicate keys, unknown fields, string-for-int coercion, multiple documents and oversize specs are rejected; error text never echoes spec values or the API key; loopback-only Ollama (localhost, private IPs, https, userinfo and paths refused); redirects refused; proxy environment ignored for loopback (test checked with a deliberate mutant).

## What these results do not show

- No real model has produced a proposal. Whether a local model can emit valid proposals is exactly what task 17 measures, and that needs the owner's PC.
- The Anthropic and Ollama request/response field names are verified only against fakes.
- Provider calls are not yet metered, priced, or checked against budgets; nothing writes the per-call ledger record (B37 is partial, B38-B40 not started).
- `effort` and batch mode are not wired (parking lot).
- `check_host` needs the running host passed in; the config has no `host` key yet (parking lot).

## Owner steps for task 17 (on the PC)

1. Pull an exact model tag with `ollama pull`.
2. Run `scripts/benchmark_ollama.py --model <tag> --output <path outside the checkout>` and send back the JSON.
3. Optional: repeat for one or two other tags. Optional paid Haiku comparison needs the dedicated API key and `--allow-paid`.
4. Then choose the default cheap model from the results.
