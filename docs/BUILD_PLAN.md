# Build plan: what Claude can do vs what the owner must do

Status: DRAFT 2026-11-01. Claude works in this repo from the cloud sandbox (Linux). It can write code and tests, run them on Linux, and push branches and open PRs. It cannot run anything on the owner's Windows PC, reach local Ollama, or hold the owner's real credentials.

## Phase 0: accept the design
- Claude: draft docs, ADR, CLAUDE.md, MEMORY.md, orchestrator draft spec (done).
- Owner: read and accept or change ADR-001; choose a license; confirm package name; merge the docs.

## Phase 1: kernel (B01-B07)
- Claude, autonomously: repo scaffold (`pyproject.toml`, uv lock, ruff, mypy, pytest, CI); pydantic config; tool registry and tier policy; schema validation; SQLite state and idempotency keys; hash-chained audit ledger; approvals-queue storage; secret-resolution interface with a fake backend; fake tools; crash-boundary tests (kill at three points); ledger-failure test; canary-secret tests; PR with evidence.
- Owner: review and merge the PR; run the test suite once on Windows (`uv run pytest`) and report results, since Windows paths and Credential Manager behavior can differ; store nothing secret yet.

## Phase 2: agent runtime (B08-B12)
- Claude: spec schema and loader; approval registry (hash check); provider interface; Ollama and Claude providers (tested against mocks and recorded transcripts); budget and spend accounting that survives restart; propose-then-execute loop; scripted fake-model tests; benchmark script.
- Owner: create a Claude API key and put it in Windows Credential Manager (never in chat or files); set the monthly spend cap; pull a local model with `ollama pull` and run the benchmark script, then send the JSON results; approve each agent spec by running the approval command.

## Phase 3: file/backup agent and scheduler (B13-B15)
- Claude: file tool pack (`list/read/hash/copy_new/report`, plus T3 delete/overwrite); `jangus tick` with missed-tick coalescing; backup agent spec; a Task Scheduler setup script and instructions; daily report; soak-test checklist.
- Owner: nominate the real folders to back up and the destination; install the scheduled task on the PC; keep the PC on; run for 7 consecutive days; log any manual fixes; sign off.

## Phase 4: orchestrator, approvals, alerting (B16-B19, B23)
- Claude: orchestrator tools and loop; approval CLI (list/show/approve/reject); payload-hash binding and expiry; spec drafting; tier promotion/demotion; status and daily report; local-only degraded mode.
- Owner: use the approval CLI for real; decide first promotions T3 to T2; choose an alert channel if more than CLI/daily report.

## Phase 5: shop agent (B20, B21)
- Claude: Shopify and Printify tool packs against recorded API fixtures and mocks; read-only agent spec; injection test corpus; per-action reconcile rules.
- Owner: create least-privilege API tokens (read-only first) for Shopify and Printify and store them in Credential Manager; approve write actions one at a time; run a clean unattended week.

## Phase 6: inbox agent (B20, B22)
- Claude: email tool pack (read, draft; send as T3); injection corpus; spec.
- Owner: create an app password or OAuth app for the mailbox with minimal scope; decide which mailbox/labels; run a clean week.

## Phase 7: memory and optional VPS
- Claude: port the Obsidian memory registry; remote job pattern; deployment checklist.
- Owner: provide the test vault; provision and harden the VPS; provide SSH access details only through your own tools.

## Throughout
- Claude cannot: merge to protected branches on your behalf unless you ask, handle your real API keys, run the Windows scheduler, test against your live shop/mailbox, or judge whether an agent's output is good for your business.
- Owner always: final approval of specs, tier promotions, spending, and anything that sends, publishes or deletes.
