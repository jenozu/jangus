# Security policy

Jangus is a local-first system under active, phased development. It is not
yet released, and no deployment has been accepted for production use.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability.

Report it privately using GitHub's **private vulnerability reporting** for this
repository (Security tab, "Report a vulnerability"). If that option is not
available, contact the repository owner through the contact details on their
GitHub profile and include the affected branch or commit.

Useful details: the affected component, steps to reproduce using synthetic
data only, the expected and actual behaviour, and the commit SHA. Never send
real credentials, personal notes, vault contents, or private run data.

## Scope

In scope: code, configuration, deployment files and documentation in this
repository, including the execution controller, remote worker, memory approval
registry and optional consultation gates.

Out of scope: third-party services and models, vulnerabilities that require
write access to the private runtime state directories, and issues in
dependencies that have an upstream fix not yet adopted (report those upstream;
Dependabot tracks adoption here).

## Supported versions

Only the latest commit on `main` (once integration is complete, see
[ADR-007](docs/architecture/decisions/007-merge-strategy.md)) and the current
`release/acceptance-readiness` branch receive fixes.

## Response

This is a small project. Reports are acknowledged on a best-effort basis, and
fixes are developed on a private branch when practical. Known security
properties and limits are described in
[SYSTEM.md](docs/architecture/SYSTEM.md) and the phase review documents;
nothing here is a warranty.
