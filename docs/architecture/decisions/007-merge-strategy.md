# ADR-007: Integration of the stacked phase branches

Status: accepted (decided under delegated authority on 2026-10-28; the repository owner may revisit). Refines the branch workflow in `AGENTS.md`.

## Context

Phases 1-8 and the acceptance-readiness checkpoint were developed as stacked,
unmerged branches. `main` holds only the initial README commit, so the default
branch misrepresents the project, CI never runs against the integrated system,
and the release candidate is a single large diff
(`release/acceptance-readiness` versus `main`: 168 files). The longer the stack
stays open, the higher the integration and review cost (audit finding A1).

## Decision

Integrate through one pull request from `release/acceptance-readiness` into
`main`, using a **merge commit** so the phase history is preserved. Do not
squash: the phase review documents reference individual commits, and
Taskmaster completion evidence records commit revisions.

Preconditions for that merge (owner-controlled):

1. The hosted CI matrix is green on the release branch, and the run URL and
   commit SHA are recorded in `docs/reviews/RELEASE_READINESS.md`.
2. All optional capabilities remain default-off (they are today: remote,
   Codex and Astra flags are rejected by configuration validation).
3. The `main` README is updated to describe the integrated, provisional state
   and links to the acceptance handoff.

After the merge:

- Protect `main` (required CI checks, no force pushes, review required).
- Retire the phase branches once their content is reachable from `main`.
  Keep the evidence commits; delete only the branch names.
- New work uses short-lived branches from `main` and pull requests.

## Alternatives and consequences

Merging eight phase PRs bottom-up gives smaller reviews, but the later
branches already contain merge-forwards of the earlier ones, so each step would
show mostly redundant diffs and eight CI cycles. A squash merge gives a tidy
history but breaks commit-referenced evidence. Leaving the stack open keeps
risk growing. The chosen approach accepts one large review, mitigated by the
existing per-phase review documents and by the audit and improvement
branches that follow this one.

## Verification

A merged `main` passes the full six-job CI workflow. Merging does not change
any Taskmaster task status and does not claim owner-machine acceptance.
