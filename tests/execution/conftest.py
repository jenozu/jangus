import hashlib
from pathlib import Path

import pytest
import yaml

from jangus.config import load_config
from jangus.execution.contracts import Manifest
from jangus.execution.controller import Controller


@pytest.fixture
def workflow(tmp_path):
    workspace = tmp_path / "workspaces" / "synthetic"
    (workspace / "input").mkdir(parents=True)
    (workspace / "out").mkdir()
    content = b"alpha synthetic text\n"
    (workspace / "input/a.txt").write_bytes(content)
    manifest = Manifest.model_validate(
        {
            "files": [
                {
                    "source": "input/a.txt",
                    "destination": "out/a.txt",
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ],
            "report": "out/report.json",
        }
    )
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "paths": {
                    "data_dir": str(tmp_path / "data"),
                    "workspace_dir": str(workspace.parent),
                },
                "permissions": {
                    "allowed_tools": [
                        "file.copy",
                        "file.report",
                        "file.read",
                        "file.hash",
                        "file.list",
                    ]
                },
                "limits": {"max_actions": 10, "max_attempts_per_task": 2},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(config_file)
    controller = Controller(config)
    yield controller, workspace, manifest, config_file
    controller.close()


def output(workspace: Path):
    return workspace / "out/a.txt"
