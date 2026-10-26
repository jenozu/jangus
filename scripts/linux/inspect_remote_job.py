"""Private read-only audit of a registered VPS worker job."""

import argparse
import json
import sys
from pathlib import Path

from jangus.remote.ledger import JobLedger
from jangus.remote.protocol import RemoteFault


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("job_id")
    args = parser.parse_args()
    if not (args.state_dir / "jobs.sqlite3").exists():
        print('{"error":"ledger_missing"}', file=sys.stderr)
        return 2
    try:
        ledger = JobLedger(args.state_dir)
        try:
            status = ledger.status(args.job_id)
            # Deliberately do not emit original job text/payload.
            status.pop("result", None)
            print(json.dumps({"status": status, "events": ledger.audit(args.job_id)},
                             sort_keys=True))
            return 0
        finally:
            ledger.close()
    except (RemoteFault, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, RemoteFault) else "audit_unavailable"
        print(json.dumps({"error": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
