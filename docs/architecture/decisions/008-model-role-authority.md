# ADR-008: Authority and response contract of advisory model roles

Status: accepted (decided under delegated authority on 2026-10-28; the repository owner may revisit). Refines ADR-003 and the Phase 5 role design.

## Context

The controller compiles every plan deterministically from the owner manifest.
Model roles (planner, worker, verifier, diagnostician) can only return a
decision label and echo identifiers. A planner or worker that does not return
`ready`, or any malformed reply, cancels the run before dispatch. Each reply
must also echo a 64-hex assignment hash. That requirement tests whether a small
local model can copy a hash; it does not add safety, because every call is its
own request/response pair and the controller independently verifies all effects
(audit finding A4).

## Decision

1. Roles remain **advisory and veto-only**. They never execute tools, grant
   permissions, restore budgets, or assert task success. The controller is the
   sole authority over effects and terminal state.
2. The assignment hash is **bound by the harness**, not echoed by the model.
   The harness records the hash of exactly what it sent with every receipt.
   Model replies carry only `role`, `decision` and `action_ids`.
3. Pre-dispatch roles (planner, worker) stay **fail-closed**: an explicit
   `reject`, an unavailable provider or an invalid reply stops the run before
   any effect. Post-run roles (verifier, diagnostician) stay non-blocking.
4. If roles ever gain capabilities (for example proposing a subset or ordering),
   each proposal must be validated by the controller against owner-approved
   limits before dispatch and needs a new decision record.

## Alternatives and consequences

Removing the roles entirely would simplify the system but discard the Phase 5
isolation, context and handoff work that later phases depend on. Giving roles
real planning power now would widen the failure surface before any real-model
benchmark exists. The chosen approach keeps today's safety properties, removes
a brittle copy requirement, and defers capability to measured evidence.

## Verification

Tests cover: a reply without a hash is accepted; a reply with unexpected
fields is rejected; `reject` cancels without dispatch; the receipt records the
harness-computed assignment hash. A real-model benchmark (Taskmaster task 12
and owner acceptance) remains required before any role is relied upon.
