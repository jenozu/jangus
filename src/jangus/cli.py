"""Command-line entry point.

Owner-facing commands only. Nothing here is reachable from model output: the
runtime never shells out, and approval commands read the owner's terminal.
"""

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

from jangus import __version__
from jangus.agents.spec import LoadedSpec, load_spec_file
from jangus.agents.spec_registry import SpecApprovalRegistry
from jangus.config import JangusConfig, load_config
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.ledger import Ledger

CONFIG_ENV = "JANGUS_CONFIG"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jangus")
    parser.add_argument("--version", "-V", action="store_true")
    parser.add_argument("--config", type=Path, help=f"config TOML (default: ${CONFIG_ENV})")
    sub = parser.add_subparsers(dest="command")
    spec = sub.add_parser("spec", help="agent spec approval (owner only)")
    spec_sub = spec.add_subparsers(dest="spec_command", required=True)
    show = spec_sub.add_parser("show", help="show a spec file's hash and approval state")
    show.add_argument("file", type=Path)
    approve = spec_sub.add_parser("approve", help="approve a spec file by its SHA-256")
    approve.add_argument("file", type=Path)
    approve.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    approve.add_argument("--note", default="", help="free-text note stored with the approval")
    spec_sub.add_parser("list", help="list approved specs")
    revoke = spec_sub.add_parser("revoke", help="remove a spec's approval")
    revoke.add_argument("name")
    return parser


def _load_cfg(path: Path | None) -> JangusConfig:
    chosen = path or (Path(os.environ[CONFIG_ENV]) if os.environ.get(CONFIG_ENV) else None)
    if chosen is None:
        raise RuntimeFault(FaultCode.CONFIG_UNREADABLE, f"no config: use --config or ${CONFIG_ENV}")
    return load_config(chosen)


def _registry(cfg: JangusConfig, with_ledger: bool) -> SpecApprovalRegistry:
    ledger = None
    if with_ledger:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        ledger = Ledger.open(cfg.data_dir / "ledger.jsonl")
    return SpecApprovalRegistry(cfg.data_dir / "spec-approvals.json", ledger)


def _summary(loaded: LoadedSpec) -> str:
    s = loaded.spec
    lines = [
        f"name:       {s.name}",
        f"sha256:     {loaded.sha256}",
        f"host:       {s.host}",
        f"model:      {s.model.provider}/{s.model.name}",
        f"max_tier:   {s.max_tier.value}",
        f"tools:      {', '.join(s.tools) or '(none)'}",
        f"secrets:    {', '.join(s.secrets) or '(none)'}",
        f"budget:     calls={s.budget.calls} tokens={s.budget.tokens} "
        f"wall_seconds={s.budget.wall_seconds} spend_usd={s.budget.spend_usd}",
    ]
    if s.escalation_model:
        lines.append(f"escalation: {s.escalation_model.provider}/{s.escalation_model.name}")
    return "\n".join(lines)


def _run(args: argparse.Namespace, confirm: Callable[[str], str]) -> int:
    cfg = _load_cfg(args.config)
    if args.spec_command == "show":
        loaded = load_spec_file(args.file)
        print(_summary(loaded))
        state = "approved" if _registry(cfg, False).is_approved(loaded) else "NOT approved"
        print(f"state:      {state}")
        return 0
    if args.spec_command == "list":
        for a in _registry(cfg, False).entries():
            print(f"{a.name}  {a.sha256}  {a.approved_at}  {a.note}".rstrip())
        return 0
    if args.spec_command == "revoke":
        removed = _registry(cfg, True).revoke(args.name)
        print("revoked" if removed else "no such approval")
        return 0 if removed else 1
    loaded = load_spec_file(args.file)
    print(_summary(loaded))
    if not args.yes and confirm("Approve exactly this spec? Type 'yes': ").strip() != "yes":
        print("not approved")
        return 1
    approval = _registry(cfg, True).approve(loaded, args.note)
    print(f"approved {approval.name} {approval.sha256[:12]}")
    return 0


def main(argv: list[str] | None = None, confirm: Callable[[str], str] = input) -> int:
    parser = _build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(f"jangus {__version__}")
        return 0
    if args.command != "spec":
        print(
            "jangus: use `jangus spec ...` (more commands arrive in later phases)", file=sys.stderr
        )
        return 2
    try:
        return _run(args, confirm)
    except RuntimeFault as fault:
        print(f"jangus: {fault}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
