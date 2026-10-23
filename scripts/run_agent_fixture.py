"""Opt-in local Ollama workflow on an owner-supplied synthetic file fixture."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from jangus.agents.context import ContextFailure
from jangus.agents.provider import ModelFailure, OllamaProvider
from jangus.agents.skills import SkillFailure
from jangus.agents.workflow import PlanningFailure, run_verified_workflow
from jangus.config import ConfigurationError, load_config
from jangus.execution.contracts import RuntimeFault
from jangus.execution.controller import Controller, read_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        provider = OllamaProvider(config.model)
        controller = Controller(config)
        try:
            outcome = run_verified_workflow(
                controller,
                provider,
                args.workspace.expanduser().absolute(),
                read_manifest(args.manifest),
                args.review_root,
            )
            print(json.dumps(asdict(outcome), sort_keys=True))
            return 0 if outcome.state == "succeeded" else 1
        finally:
            controller.close()
    except (ConfigurationError, ModelFailure, SkillFailure, ContextFailure,
            PlanningFailure, RuntimeFault) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("error: local agent operation unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
