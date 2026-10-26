"""Read-only Taskmaster release-gate report. Does NOT accept live hardware or deploy."""

import argparse
import json
import sys
from pathlib import Path

from jangus.release.readiness import GROUPS, ReadinessFault, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backlog",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".taskmaster/tasks/tasks.json",
    )
    parser.add_argument(
        "--check",
        choices=tuple(GROUPS),
        help="Require task-status completion (not live acceptance)",
    )
    args = parser.parse_args()
    try:
        data = json.loads(args.backlog.read_text(encoding="utf-8"))
        result = report(data)
    except (OSError, UnicodeError, ValueError, RecursionError, ReadinessFault):
        print('{"error":"readiness_unavailable"}', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    if args.check and not result["gates"][args.check]["taskmaster_complete"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
