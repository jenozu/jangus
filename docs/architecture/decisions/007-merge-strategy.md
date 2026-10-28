# ADR-007: Integration of the stacked phase branches

Status: accepted (decided under delegated authority on 2026-10-28; the repository owner may revisit). Refines the branch workflow in `AGENTS.md`.

## Context

Phases 1-8 and the acceptance-readiness checkpoint were developed as stacked
branches with chained, still-open PRs (#1-#9). `main` holds only the initial README commit, so the default
branch misrepresents the project, no CI protects `main`, and the release
candidate is a single large diff
(`release/acceptance-readiness` versus `main`: 168 files). The longer the stack
stays open, the higher the integration and review cost (audit finding A1).

## Decision

Land the **existing chained pull requests bottom-up**: PR #1
(`foundation/01-specifications` into `main`), then #2 through #9, each with a
**merge commit** (not a squash). Every PR in the chain targets the previous
phase branch, so each shows only its own phase delta, and CI was green on the
acceptance-readiness tip (all six jobs, both push and pull-request runs, commit
`a400273`). After each merge, retarget the next PR to `main` if GitHub has not
already done so. Preserve history: phase review documents and Taskmaster
completion evidence reference individual commits, so squashing would break them.

Preconditions for the first merge (owner-controlled):

1. The hosted CI matrix is green for the PR being merged; record the run URL
   and commit SHA in `docs/reviews/RELEASE_READINESS.md`.
2. All optional capabilities remain default-off (they are today: remote,
   Codex and Astra flags are rejected by configuration validation).
3. The `main` README is updated (in PR #1 or a follow-up) to describe the
   integrated, provisional state and link to the acceptance handoff.

After the last merge:

- Protect `main` (required CI checks, no force pushes, review required).
- Delete the phase branch names once their commits are reachable from `main`;
  the commits themselves remain as evidence.
- Retarget open improvement PRs (the `release/improve-*` branches) to `main`;
  they are based on the release tip, so they rebase cleanly.
- New work uses short-lived branches from `main` and pull requests.

If the owner prefers fewer steps, a single merge commit of
`release/acceptance-readiness` into `main` is an acceptable alternative: it
contains all earlier commits. The earlier PRs should then be closed with a
comment pointing at the merge.

## Alternatives and consequences

A squash merge gives a tidy history but breaks commit-referenced evidence.
Leaving the stack open keeps integration risk growing and leaves `main`
showing an empty project. Merging bottom-up costs nine small merge operations
but gives the smallest reviewable diffs, which suits a safety-focused project.

## Verification

A merged `main` passes the full six-job CI workflow. Merging does not change
any Taskmaster task status and does not claim owner-machine acceptance.
