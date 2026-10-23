"""Opt-in local Ollama workflow on an owner-supplied synthetic file fixture."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from jangus.agents.context import ContextFailure
from jangus.agents.orchestration import run_coordinated_workflow
from jangus.agents.provider import ModelFailure, OllamaProvider
from jangus.agents.skills import SkillFailure
from jangus.agents.workflow import PlanningFailure, run_verified_workflow
from jangus.config import ConfigurationError, load_config
from jangus.execution.contracts import RuntimeFault
from jangus.execution.controller import Controller, read_manifest
from jangus.memory.integration import MemoryContext
from jangus.memory.registry import ApprovalRegistry
from jangus.memory.schema import MemoryFault, Vault


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--coordinated",
        action="store_true",
        help="Use isolated planner, worker, and verifier/diagnostician roles.",
    )
    parser.add_argument("--review-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--memory-scope", default="project:demo")
    parser.add_argument("--memory-query", default="verified file copy report")
    parser.add_argument("--memory-required", action="store_true")
    parser.add_argument("--propose-lesson", action="store_true")
    args = parser.parse_args()
    if (
        (args.vault is None) != (args.registry is None)
        or (
            (args.vault is not None or args.memory_required or args.propose_lesson)
            and not args.coordinated
        )
        or ((args.memory_required or args.propose_lesson) and args.vault is None)
    ):
        parser.error("Memory requires --coordinated, --vault and --registry together.")
    try:
        config = load_config(args.config)
        provider = OllamaProvider(config.model)
        controller = Controller(config)
        registry = None
        try:
            memory = None
            if args.vault is not None:
                vault = Vault(args.vault)
                registry = ApprovalRegistry(args.registry, vault)
                memory = MemoryContext(
                    vault,
                    registry,
                    scope=args.memory_scope,
                    query=args.memory_query,
                    required=args.memory_required,
                    propose_on_success=args.propose_lesson,
                )
            workflow = run_coordinated_workflow if args.coordinated else run_verified_workflow
            outcome = workflow(
                controller,
                provider,
                args.workspace.expanduser().absolute(),
                read_manifest(args.manifest),
                args.review_root,
                **({"memory": memory} if args.coordinated else {}),
            )
            print(json.dumps(asdict(outcome), sort_keys=True))
            state = outcome.controller_state if args.coordinated else outcome.state
            return 0 if state == "succeeded" else 1
        finally:
            if registry is not None:
                registry.close()
            controller.close()
    except (
        ConfigurationError,
        ModelFailure,
        SkillFailure,
        ContextFailure,
        PlanningFailure,
        RuntimeFault,
        MemoryFault,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("error: local agent operation unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
