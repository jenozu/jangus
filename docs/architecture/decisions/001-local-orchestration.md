# ADR-001: Local orchestration first

Status: accepted design. Date: 2026-10-22.

## Context

Earlier planning centered on Astra supervising local workers. The later agreed direction prioritizes self-hosted orchestration, limited context, low recurring costs, and useful operation without cloud availability.

## Decision

A local model proposes plans and recovery. Deterministic controller code owns execution, permissions, durable state, and limits. Worker roles can share one model; begin with one text inference at a time. Choose model size and desktop/VPS hosting from actual benchmarks.

Local workers and the local orchestrator attempt bounded recovery before optional advice-only Codex consultation. A final Astra API fallback remains disabled by default and may only be implemented after supported access is confirmed. No unverified model identifier, subscription entitlement, or price is assumed.

## Alternatives

Astra-first planning was superseded by the later user decision. One loaded model per role is unnecessary and could exceed available resources. Removing all optional consultation would reduce future recovery choices.

## Consequences and verification

The local MVP must pass with consultants disabled. Cloud adapters need explicit enablement, permitted redacted evidence, bounded calls/tokens/cost/time, and no direct tool authority. Session changes and consultations inherit existing limits. Model quality and performance remain unverified until measured on the actual hardware.
