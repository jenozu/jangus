"""Read-only operator preflight for the private Ubuntu VPS worker."""

import argparse
import json
import sys
from pathlib import Path

from jangus.remote.preflight import verify_host
from jangus.remote.protocol import RemoteFault


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--unit", type=Path, default=Path("/etc/systemd/system/jangus-worker.service")
    )
    parser.add_argument("--state", type=Path, default=Path("/var/lib/jangus"))
    parser.add_argument("--environment", type=Path, default=Path("/etc/jangus/worker.env"))
    args = parser.parse_args()
    try:
        print(json.dumps(verify_host(args.unit, args.state, args.environment)))
        return 0
    except (RemoteFault, OSError) as exc:
        code = exc.code if isinstance(exc, RemoteFault) else "preflight_unavailable"
        print(json.dumps({"checked": False, "error": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
