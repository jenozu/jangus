"""Small setup CLI. No run commands are advertised before implementation."""

import argparse
import json
import os
import sys
from pathlib import Path

from jangus import __version__
from jangus.config import ConfigurationError, JangusConfig, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jangus", description="Jangus setup and validation")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    config_parser = commands.add_parser("config", help="Inspect the configuration contract")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    validate = config_commands.add_parser("validate", help="Validate without writing or connecting")
    validate.add_argument("--config", type=Path, help="YAML file; overrides JANGUS_CONFIG")
    config_commands.add_parser("schema", help="Print the JSON schema (no values or credentials)")
    args = parser.parse_args(argv)
    if args.config_command == "schema":
        print(json.dumps(JangusConfig.model_json_schema(), indent=2))
        return 0
    selected = args.config or os.environ.get("JANGUS_CONFIG")
    if not selected:
        print("error: pass --config or set JANGUS_CONFIG.", file=sys.stderr)
        return 2
    try:
        load_config(Path(selected).expanduser())
    except ConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("Configuration valid. No directories created or services contacted.")
    return 0
