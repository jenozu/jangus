"""Composition root: wire the kernel, tools, providers and runners for one approved agent.

This is the only place that decides which concrete pieces exist at runtime. It adds no
behaviour of its own; every rule lives in the modules it connects.

A scheduled run reads the agent's standing directive from `<specs_dir>/<name>.directive.json`,
fills in the fields only the kernel may set (id, assignee, spec hash), issues it (recorded in
the ledger), and runs it. The folders the file tools may touch are the spec's
`required_paths`, so the owner's approval of the spec hash also approves the folders.
"""

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jangus.agents.gate import CallGate
from jangus.agents.pricing import PriceTable, load_prices
from jangus.agents.providers.factory import build_hub
from jangus.agents.runner import Runner
from jangus.agents.spec import Host, LoadedSpec, check_host, check_tools_known, to_constraints
from jangus.config import JangusConfig
from jangus.directives.models import Directive, directive_hash, issue, parse_directive
from jangus.directives.runner import DirectiveReport, DirectiveRunner
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.alerts import AlertQueue
from jangus.kernel.approvals import ApprovalQueue
from jangus.kernel.budgets import BudgetBook
from jangus.kernel.cache import ResultCache
from jangus.kernel.executor import Executor
from jangus.kernel.ledger import Ledger
from jangus.kernel.registry import Registry
from jangus.kernel.secrets import EnvBackend, SecretBackend, WindowsCredentialBackend
from jangus.kernel.state import StateStore
from jangus.tools.files import FileWorld, register_file_tools

RUNNING_HOST: Host = "pc"
PRICES_FILE = "prices.yaml"


@dataclass
class Runtime:
    cfg: JangusConfig
    state: StateStore
    ledger: Ledger
    alerts: AlertQueue
    budgets: BudgetBook

    def close(self) -> None:
        self.state.close()


def specs_dir(cfg: JangusConfig) -> Path:
    return cfg.specs_dir if cfg.specs_dir is not None else cfg.data_dir / "specs"


def open_runtime(cfg: JangusConfig) -> Runtime:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore.open(cfg.data_dir / "state.db")
    ledger = Ledger.open(cfg.data_dir / "ledger.jsonl")
    alerts = AlertQueue(ledger, state)
    return Runtime(cfg, state, ledger, alerts, BudgetBook(state, ledger, alerts))


def default_secrets() -> SecretBackend:
    return WindowsCredentialBackend() if sys.platform == "win32" else EnvBackend()


def load_template(directory: Path, name: str) -> dict[str, Any]:
    path = directory / f"{name}.directive.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeFault(
            FaultCode.DIRECTIVE_INVALID, f"no readable standing directive {path.name}"
        ) from None
    if not isinstance(data, dict):
        raise RuntimeFault(FaultCode.DIRECTIVE_INVALID, f"{path.name} must be an object")
    return data


def instantiate(template: dict[str, Any], loaded: LoadedSpec, slot: datetime) -> dict[str, Any]:
    """Standing directive for one scheduled slot. The kernel sets id, assignee and spec hash;
    nothing in the template can claim a different agent or spec."""
    raw = dict(template)
    raw["schema_version"] = 1
    raw["id"] = f"{loaded.spec.name}-{slot.strftime('%Y%m%dT%H%M')}"
    raw["issued_by"] = "scheduler"
    raw["assignee"] = loaded.spec.name
    raw["spec_sha256"] = loaded.sha256
    return raw


def already_issued(ledger: Ledger, directive: Directive) -> bool:
    digest = directive_hash(directive)
    return any(
        e.event.get("kind") == "directive_issued"
        and e.event.get("id") == directive.id
        and e.event.get("hash") == digest
        for e in ledger.entries()
    )


def run_standing_directive(
    rt: Runtime,
    loaded: LoadedSpec,
    slot: datetime,
    *,
    secrets: SecretBackend | None = None,
) -> DirectiveReport:
    backend = secrets if secrets is not None else default_secrets()
    spec = loaded.spec
    check_host(
        spec,
        running_host=RUNNING_HOST,
        secret_available=lambda name: backend.get(name) is not None,
    )
    cfg = rt.cfg
    registry = Registry()
    register_file_tools(
        registry, FileWorld([Path(p) for p in spec.required_paths], cfg.data_dir / "reports")
    )
    check_tools_known(spec, registry.names())

    constraints = to_constraints(loaded)
    raw = instantiate(load_template(specs_dir(cfg), spec.name), loaded, slot)
    directive = parse_directive(raw, constraints, registry)
    if not already_issued(rt.ledger, directive):
        issue(directive, constraints, rt.ledger, registry)

    gate = CallGate(
        state=rt.state, ledger=rt.ledger, budgets=rt.budgets, alerts=rt.alerts, caps=cfg.caps
    )
    prices_path = cfg.data_dir / PRICES_FILE
    prices = load_prices(prices_path) if prices_path.exists() else PriceTable.local_only()
    hub = build_hub(cfg, backend, rt.alerts.as_fn(), blocked=gate.blocked)
    approvals = ApprovalQueue(rt.state, rt.ledger)
    executor = Executor(registry, rt.state, rt.ledger, secrets=backend, approvals=approvals)
    runner = Runner(
        spec=loaded,
        executor=executor,
        registry=registry,
        state=rt.state,
        ledger=rt.ledger,
        hub=hub,
        budgets=rt.budgets,
        alerts=rt.alerts,
        prices=prices,
        gate=gate,
        cache=ResultCache(rt.state),
    )
    runner.ensure_budgets()
    directives = DirectiveRunner(
        loaded=loaded,
        runner=runner,
        state=rt.state,
        ledger=rt.ledger,
        budgets=rt.budgets,
        alerts=rt.alerts,
        approvals=approvals,
    )
    return directives.run(directive)
