"""Owner-operated, SSH-tunnel-only remote client; synthetic jobs only."""

import argparse
import json
import os
import sys
from pathlib import Path

from jangus.remote.client import ClientJournal, RemoteClient
from jangus.remote.protocol import RemoteFault


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8765")
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--synthetic-text", required=True)
    for name in ("reconcile", "cancel"):
        action = sub.add_parser(name)
        action.add_argument("job_id")
    sub.add_parser("health")
    args = parser.parse_args()
    try:
        journal = ClientJournal(args.state_dir)
        try:
            if args.action == "prepare":
                spec = journal.prepare(args.synthetic_text)
                print(json.dumps({"job_id": spec.job_id, "prepared": True}))
                return 0
            client = RemoteClient(
                args.endpoint,
                os.environ.get("JANGUS_REMOTE_TOKEN", ""),
                journal,
            )
            if args.action == "health":
                result = client.request("GET", "/v1/health")
            elif args.action == "cancel":
                result = client.cancel(args.job_id)
            else:
                result = client.reconcile(args.job_id)
            print(json.dumps(result, sort_keys=True))
            return 0
        finally:
            journal.close()
    except (RemoteFault, OSError, ValueError):
        print("error: remote operation unavailable or rejected", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
