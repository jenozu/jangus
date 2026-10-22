"""Exercise the installed CLI, environment selection, and sanitized errors."""

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "jangus", *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_version_and_schema():
    assert cli("--version").stdout.strip() == "jangus 0.1.0"
    result = cli("config", "schema")
    assert result.returncode == 0
    schema = json.loads(result.stdout)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["paths"]["$ref"] == "#/$defs/PathsConfig"


def test_cli_requires_configuration():
    env = os.environ.copy()
    env.pop("JANGUS_CONFIG", None)
    result = cli("config", "validate", env=env)
    assert result.returncode == 2
    assert "pass --config or set JANGUS_CONFIG" in result.stderr


def test_explicit_path_overrides_environment():
    env = os.environ.copy()
    env["JANGUS_CONFIG"] = str(ROOT / "nonexistent.yaml")
    result = cli(
        "config", "validate", "--config", str(ROOT / "config/jangus.example.yaml"), env=env
    )
    assert result.returncode == 0
    assert "No directories created or services contacted" in result.stdout


def test_environment_path_and_secret_errors(tmp_path):
    config = tmp_path / "private.yaml"
    config.write_text(
        yaml.safe_dump({"api_key": "synthetic-canary-do-not-print"}), encoding="utf-8"
    )
    env = os.environ.copy()
    env["JANGUS_CONFIG"] = str(config)
    result = cli("config", "validate", env=env)
    assert result.returncode == 2
    assert "schema violation" in result.stderr
    assert "synthetic-canary-do-not-print" not in result.stderr + result.stdout
    assert "Traceback" not in result.stderr
