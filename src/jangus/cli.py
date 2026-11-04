"""Command-line entry point.

Owner-facing commands only. Nothing here is reachable from model output: the
runtime never shells out, and approval commands read the owner's terminal.
"""

import argparse
import os
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from jangus import __version__
from jangus.agents.report import build_daily_report
from jangus.agents.spec import LoadedSpec, load_spec_file
from jangus.agents.spec_registry import SpecApprovalRegistry
from jangus.config import JangusConfig, load_config
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import read_alerts
from jangus.kernel.ledger import Ledger
from jangus.kernel.lock import RunLock
from jangus.runtime import Runtime, open_runtime, run_standing_directive, specs_dir
from jangus.scheduler import Scheduler

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
    sub.add_parser("tick", help="run the agents that are due now (called by Task Scheduler)")
    sub.add_parser("status", help="approved specs, recent alerts, halted directives")
    report = sub.add_parser("report", help="print the daily report written from the ledger")
    report.add_argument("--day", help="ISO date, default today (local)")
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


def _run_spec(args: argparse.Namespace, cfg: JangusConfig, confirm: Callable[[str], str]) -> int:
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


def _tick(cfg: JangusConfig) -> int:
    rt = open_runtime(cfg)
    try:
        scheduler = Scheduler(
            specs_dir=specs_dir(cfg),
            registry=SpecApprovalRegistry(cfg.data_dir / "spec-approvals.json", rt.ledger),
            state=rt.state,
            ledger=rt.ledger,
            alerts=rt.alerts,
            lock=RunLock(cfg.data_dir / "tick.lock"),
            run_agent=lambda loaded, slot: run_standing_directive(rt, loaded, slot),
        )
        now = datetime.now()  # noqa: DTZ005 - cron triggers are local wall-clock times
        result = scheduler.tick(now)
        write_daily_report_for(rt, (now - timedelta(days=1)).date().isoformat())
        if result.skipped_overlap:
            print("tick skipped: another tick is running")
            return 0
        for agent, slot in result.ran:
            print(f"ran {agent} for slot {slot}")
        for problem in result.problems:
            print(f"problem: {problem}")
        return 1 if result.problems else 0
    finally:
        rt.close()


def write_daily_report_for(rt: Runtime, day: str) -> None:
    """Write reports/daily-<day>.txt once (never replaces an existing report)."""
    path = rt.cfg.data_dir / "reports" / f"daily-{day}.txt"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(build_daily_report(rt.ledger, day).render() + "\n")
    except FileExistsError:
        return


def _status(cfg: JangusConfig) -> int:
    rt = open_runtime(cfg)
    try:
        approved = SpecApprovalRegistry(cfg.data_dir / "spec-approvals.json").entries()
        print("approved specs: " + (", ".join(a.name for a in approved) or "none"))
        alerts = read_alerts(rt.ledger)
        print(f"alerts: {len(alerts)}")
        for a in alerts[-10:]:
            print(f"  [{a.level}] {a.code}: {a.message}")
        halted = [e.event for e in rt.ledger.entries() if e.event.get("kind") == "directive_halted"]
        print(f"halted directives: {len(halted)}")
        for event in halted[-5:]:
            print(f"  {event.get('directive')}: {event.get('reason')}")
        return 0
    finally:
        rt.close()


def _report(cfg: JangusConfig, day: str | None) -> int:
    rt = open_runtime(cfg)
    try:
        chosen = day or datetime.now().date().isoformat()  # noqa: DTZ005 - local date
        print(build_daily_report(rt.ledger, chosen).render())
        return 0
    finally:
        rt.close()


def main(argv: list[str] | None = None, confirm: Callable[[str], str] = input) -> int:
    parser = _build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(f"jangus {__version__}")
        return 0
    if args.command not in ("spec", "tick", "status", "report"):
        print("jangus: use `spec`, `tick`, `status` or `report`", file=sys.stderr)
        return 2
    try:
        cfg = _load_cfg(args.config)
        if args.command == "spec":
            return _run_spec(args, cfg, confirm)
        if args.command == "tick":
            return _tick(cfg)
        if args.command == "status":
            return _status(cfg)
        return _report(cfg, args.day)
    except RuntimeFault as fault:
        print(f"jangus: {fault}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
