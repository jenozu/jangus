"""Shared path-boundary helpers used by every durable store."""

from pathlib import Path


def checkout_roots() -> set[Path]:
    """Git checkouts (or source trees) around the CWD and this package.

    Private state, workspaces, exports and registries must never overlap these.
    """
    roots: set[Path] = set()
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for candidate in (start, *start.parents):
            if (candidate / ".git").exists() or (
                (candidate / "pyproject.toml").exists() and (candidate / "rules.md").exists()
            ):
                roots.add(candidate.resolve())
    return roots
