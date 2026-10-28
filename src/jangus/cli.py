"""Configuration and deterministic local execution commands."""

import argparse
import json
import os
import sys
from pathlib import Path

from jangus import __version__
from jangus.config import ConfigurationError, JangusConfig, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jangus", description="Jangus local execution")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    config_parser = commands.add_parser("config", help="Inspect the configuration contract")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    validate = config_commands.add_parser("validate", help="Validate without writing or connecting")
    validate.add_argument("--config", type=Path, help="YAML file; overrides JANGUS_CONFIG")
    config_commands.add_parser("schema", help="Print the JSON schema (no values or credentials)")
    run_parser = commands.add_parser("run", help="Deterministic file workflows")
    run_commands = run_parser.add_subparsers(dest="run_command", required=True)
    for operation in ("submit", "execute", "resume", "status", "cancel", "export"):
        command = run_commands.add_parser(operation)
        command.add_argument("--config", type=Path)
        if operation == "submit":
            command.add_argument("--workspace", type=Path, required=True)
            command.add_argument("--manifest", type=Path, required=True)
        else:
            command.add_argument("run_id")
        if operation in {"execute", "resume"}:
            command.add_argument("--revision", type=int, default=1)
        if operation == "export":
            command.add_argument("--output", type=Path, required=True)
    state_parser = commands.add_parser("state", help="Private backup and retention")
    state_commands = state_parser.add_subparsers(dest="state_command", required=True)
    for operation in ("backup", "restore", "prune"):
        command = state_commands.add_parser(operation)
        command.add_argument("--config", type=Path)
        if operation == "backup":
            command.add_argument("--output", type=Path, required=True)
        if operation == "restore":
            command.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "config" and args.config_command == "schema":
        print(json.dumps(JangusConfig.model_json_schema(), indent=2))
        return 0
    selected = args.config or os.environ.get("JANGUS_CONFIG")
    if not selected:
        print("error: pass --config or set JANGUS_CONFIG.", file=sys.stderr)
        return 2
    try:
        config = load_config(Path(selected).expanduser())
        if args.command != "config":
            return runtime_command(args, config)
    except ConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("Configuration valid. No directories created or services contacted.")
    return 0


def runtime_command(args, config):
    import sqlite3

    from jangus.execution.contracts import RuntimeFault
    from jangus.execution.controller import Controller, read_manifest
    from jangus.execution.diagnostics import export
    from jangus.execution.files import no_links
    from jangus.execution.storage import restore
    from jangus.paths import checkout_roots

    controller = None
    try:
        # Explicit output destinations cannot overwrite files or disclose into Git.
        if getattr(args, "output", None):
            output = args.output.expanduser().absolute()
            no_links(output)
            if any(output.resolve().is_relative_to(r) for r in checkout_roots()):
                raise RuntimeFault("output_inside_checkout")
            workspace_root = Path(config.paths.workspace_dir).expanduser().resolve()
            if output.resolve().is_relative_to(workspace_root):
                raise RuntimeFault("output_inside_workspace")
            args.output = output
        if args.command == "state" and args.state_command == "restore":
            restore(
                args.backup.expanduser().absolute(),
                Path(config.paths.data_dir).expanduser().absolute(),
            )
            print(json.dumps({"restored": True}))
            return 0
        controller = Controller(config)
        if args.command == "state":
            if args.state_command == "backup":
                controller.store.backup(args.output)
                result = {"backed_up": True}
            else:
                result = {"pruned_runs": controller.store.prune(config.logging.retention_days)}
        elif args.run_command == "submit":
            result = {
                "run_id": controller.submit(
                    args.workspace.expanduser().absolute(), read_manifest(args.manifest)
                )
            }
        elif args.run_command in {"execute", "resume"}:
            result = controller.execute(args.run_id, revision=args.revision)
        elif args.run_command == "cancel":
            controller.store.cancel(args.run_id)
            result = controller.status(args.run_id)
        elif args.run_command == "status":
            result = controller.status(args.run_id)
        else:
            result = export(controller, args.run_id, args.output)
        print(json.dumps(result, sort_keys=True))
        return 1 if result.get("state") in {"failed", "waiting", "cancelled"} else 0
    except RuntimeFault as exc:
        print(f"error: {exc.code}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, ValueError):
        print(
            "error: runtime operation unavailable; inspect private state and configuration.",
            file=sys.stderr,
        )
        return 2
    finally:
        if controller is not None:
            controller.close()
