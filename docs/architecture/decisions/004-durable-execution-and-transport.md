# ADR-004: Durable execution and private remote jobs

Status: accepted design. Date: 2026-10-22.

## Context

Connections and processes can fail after an action has happened but before its result is recorded. Repeated chat-driven terminal interactions add model cost and do not solve this ambiguity.

## Decision

Implement durable state and logging before model-driven actions. Persist intent before effects, verify real outcomes, and reconcile uncertain actions before retry. Keep live state, logs, artifacts, and credentials outside Git.

Use SSH for administration. For everyday remote jobs, use a private worker service over an SSH tunnel. The worker owns durable execution; the desktop persists a stable job identity before submission. Repeated submissions with the same identity and payload reconcile to existing work; mismatched payloads are rejected.

GitHub Actions handles checks and optional later deployment. Playwright is a potential worker tool, not a transport or deployment requirement.

## Alternatives

An unrestricted remote terminal as the primary agent interface makes scope enforcement and reconciliation difficult. Keeping job lifetime tied to an SSH session loses progress on disconnect. GitHub Actions as a runtime message bus confuses deployment with job execution.

## Consequences and verification

Phase 4 tests crashes around effects, cancellation, persistence failure, redaction, and persistent budgets. Phase 7 adds lost acknowledgements, service restart, reconnect, duplicate suppression, and actual VPS tests. Do not promise general exactly-once execution or automatic rollback. Ordinary submit/status/reconnect operations must run without LLM inference.
