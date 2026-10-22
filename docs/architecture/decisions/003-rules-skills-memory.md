# ADR-003: Rules, skills, memory, and authority

Status: accepted design. Date: 2026-10-22.

## Context

Repository instructions, reusable procedures, and knowledge can improve agent behavior, but text is mutable and cannot be the enforcement boundary.

## Decision

Use lowercase root `rules.md` as the single shared rule source. `AGENTS.md` is the coding-agent entry point and references it. Root `skills/` holds reviewed procedures; `src/jangus/skills/` implements their explicit discovery, selective loading, and exact-revision tracking.

An external Obsidian-compatible vault holds curated knowledge. Ship only the numbered vault template structure defined in [SYSTEM.md](../SYSTEM.md). Store approval outside note text and bind it to the exact content hash, scope, reviewer, and evidence. Changing content invalidates the old approval for that new revision.

The controller/policy layer owns capabilities. Notes and skills can describe or request capabilities but never grant them. Proposals enter the agent inbox and require authorized review.

## Alternatives

Duplicating rules across agent files creates drift. Treating frontmatter as approval lets an ordinary edit assert trust. Loading the entire vault wastes context and risks unintended disclosure. Executable scripts stored as automatically trusted notes bypass tool registration.

## Consequences and verification

The future loader includes mandatory rules and relevant skills and records their revisions. Memory tests cover changed hashes, ineligible linked notes, conflicting edits, missing approval state, and restoration. Retrieval records distinguish inclusion, application, and actual verification; successful tasks do not automatically approve knowledge.
