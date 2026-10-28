"""Create a new external synthetic workspace, manifest, and private configuration."""

import argparse
import hashlib
import json
from pathlib import Path

import yaml

from jangus.execution.files import no_links
from jangus.paths import checkout_roots


def create(root: Path):
    root = root.expanduser().absolute()
    no_links(root)
    if any(
        root.resolve().is_relative_to(repo) or repo.is_relative_to(root.resolve())
        for repo in checkout_roots()
    ):
        raise ValueError("Choose a new directory outside the repository")
    root.mkdir(mode=0o700)  # Existing directories are rejected, never reused.
    workspace = root / "workspaces" / "demo"
    (workspace / "input").mkdir(parents=True)
    (workspace / "out").mkdir()
    content = b"Jangus synthetic file workflow.\n"
    (workspace / "input/example.txt").write_bytes(content)
    manifest = {
        "schema_version": 1,
        "files": [
            {
                "source": "input/example.txt",
                "destination": "out/example.txt",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
        "report": "out/report.json",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    config = {
        "paths": {"data_dir": str(root / "state"), "workspace_dir": str(root / "workspaces")},
        "permissions": {"allowed_tools": ["file.copy", "file.report"]},
    }
    (root / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    print(
        json.dumps(
            {
                "config": str(root / "config.yaml"),
                "manifest": str(root / "manifest.json"),
                "workspace": str(workspace),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    create(parser.parse_args().root)
