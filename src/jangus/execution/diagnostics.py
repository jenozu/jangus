"""Disclosure-minimized diagnostic bundles; raw task content is never exported."""

import json
import zipfile
from pathlib import Path

from jangus.execution.contracts import RuntimeFault, digest, encoded
from jangus.execution.files import no_links

MAX_EXPORT_BYTES = 2 * 1024 * 1024


def export(controller, run_id: str, target: Path):
    no_links(target)
    # Export only the structured allowlisted event fields, never database/config
    # snapshots, exception strings, filenames, or input/output contents.
    with controller.store.snapshot():
        summary = controller.status(run_id)
        events = controller.store.events(run_id)
    if len(events) > 4096:
        raise RuntimeFault("export_too_large")
    receipts = [
        {"action_id": a["id"], "state": a["state"], "evidence": a["evidence"]}
        for a in summary["actions"]
    ]
    members = {
        "summary.json": encoded(summary),
        "events.jsonl": b"".join(encoded(event) for event in events),
        "verification.json": encoded(receipts),
        "omissions.json": encoded(
            {
                "omitted": [
                    {"category": "raw_files", "reason": "Private source and output content"},
                    {
                        "category": "paths_and_manifest",
                        "reason": "Private task names and locations",
                    },
                    {"category": "state_and_configuration", "reason": "Private canonical state"},
                    {
                        "category": "free_text_logs",
                        "reason": "Allowlisted structured diagnostics only",
                    },
                ]
            }
        ),
    }
    members["checksums.json"] = encoded({name: digest(data) for name, data in members.items()})
    if sum(map(len, members.values())) > MAX_EXPORT_BYTES:
        raise RuntimeFault("export_too_large")
    with target.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in members.items():
                archive.writestr(name, data)
    return {"run_id": run_id, "members": len(members), "bytes": target.stat().st_size}


def validate_bundle(target: Path) -> bool:
    with zipfile.ZipFile(target) as archive:
        hashes = json.loads(archive.read("checksums.json"))
        return all(digest(archive.read(name)) == expected for name, expected in hashes.items())
