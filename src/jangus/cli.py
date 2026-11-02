"""Command-line entry point (placeholder until later tasks add commands)."""

import sys

from jangus import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args in (["--version"], ["-V"]):
        print(f"jangus {__version__}")
        return 0
    print("jangus: no commands implemented yet (Phase 1 in progress)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
