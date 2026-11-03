"""Shared test harness for the agent runtime: a scripted fake model and a wired-up runner.

Everything here is fake: no network, no real model. Results prove the kernel plumbing only.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jangus.agents.pricing import PriceTable
from jangus.agents.providers.base import (
    ModelRequest,
    ModelResponse,
    ProviderHub,
    Usage,
)
from jangus.agents.runner import Runner
from jangus.agents.spec import LoadedSpec, parse_spec, to_constraints
from jangus.directives.models import Directive, issue, parse_directive
from jangus.directives.runner import DirectiveRunner
from jangus.kernel.alerts import AlertQueue
from jangus.kernel.approvals import ApprovalQueue
from jangus.kernel.budgets import BudgetBook
from jangus.kernel.executor import Executor
from jangus.kernel.ledger import Ledger
from jangus.kernel.registry import Reconciliation, Tier, ToolSpec
from jangus.kernel.state import StateStore
from tests.fakes import FakeWorld, PathArgs, build_registry

SPEC_YAML = """\
schema_version: 1
name: file-agent
role: "You keep files tidy."
model: {{provider: {provider}, name: "{model}", max_tokens: 512}}
tools: [{tools}]
max_tier: {max_tier}
budget: {{calls: {calls}, tokens: {tokens}, wall_seconds: 600, spend_usd: 1.0}}
{escalation}attempts: {{default_model: 2, step_up: 1, max_model_calls: {max_calls},
  max_proposals: {max_props}}}
"""


ESCALATION = "escalation_model: {provider: claude, name: haiku-tag, effort: low}\n"


def make_spec(
    calls: int = 20,
    tokens: int = 100_000,
    max_calls: int = 6,
    max_props: int = 12,
    max_tier: str = "T2",
    escalation: bool = True,
    extra_tools: tuple[str, ...] = (),
    provider: str = "ollama",
    model: str = "qwen3:8b",
) -> LoadedSpec:
    text = SPEC_YAML.format(
        calls=calls,
        tokens=tokens,
        max_calls=max_calls,
        max_props=max_props,
        max_tier=max_tier,
        tools=", ".join(("file.read", "file.write", "file.delete", *extra_tools)),
        escalation=ESCALATION if escalation else "",
        provider=provider,
        model=model,
    )
    return parse_spec(text.encode())


def reply(*proposals: tuple[str, dict[str, Any]], done: bool = False, **extra: Any) -> str:
    body = {"proposals": [{"tool": t, "args": a} for t, a in proposals], "done": done, **extra}
    return json.dumps(body)


Script = str | BaseException | Callable[[ModelRequest], str]


@dataclass
class ScriptedProvider:
    """Returns scripted replies in order and records every request it receives."""

    name: str = "ollama"
    script: list[Script] = field(default_factory=list)
    usage: Usage | None = field(default_factory=lambda: Usage(input_tokens=100, output_tokens=20))
    requests: list[ModelRequest] = field(default_factory=list)

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.script:
            raise AssertionError("scripted provider ran out of replies")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        text = item(request) if callable(item) else item
        return ModelResponse(
            text=text,
            usage=self.usage if self.usage is not None else Usage(),
            provider=self.name,
            model=request.model,
            latency_ms=10,
            stop_reason="end",
        )


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        spec: LoadedSpec | None = None,
        provider: ScriptedProvider | None = None,
        escalation: ScriptedProvider | None = None,
        with_approvals: bool = False,
        prices: PriceTable | None = None,
    ) -> None:
        self.world = FakeWorld()
        self.registry = build_registry(self.world)
        self.registry.register(_flaky_tool())
        self.state = StateStore.open(tmp_path / "state.db")
        self.ledger = Ledger.open(tmp_path / "ledger.jsonl")
        self.alerts = AlertQueue(self.ledger, self.state)
        self.budgets = BudgetBook(self.state, self.ledger, self.alerts)
        self.provider = provider or ScriptedProvider()
        self.escalation = escalation or ScriptedProvider(name="claude")
        self.hub = ProviderHub(
            {"ollama": self.provider, "claude": self.escalation},
            {"ollama", "claude"},
            self.alerts.as_fn(),
        )
        self.approvals = ApprovalQueue(self.state, self.ledger) if with_approvals else None
        self.executor = Executor(self.registry, self.state, self.ledger, approvals=self.approvals)
        self.spec = spec or make_spec()
        self.runner = Runner(
            spec=self.spec,
            executor=self.executor,
            registry=self.registry,
            state=self.state,
            ledger=self.ledger,
            hub=self.hub,
            budgets=self.budgets,
            alerts=self.alerts,
            prices=prices if prices is not None else default_prices(),
        )
        self.runner.ensure_budgets()
        self.directives = DirectiveRunner(
            loaded=self.spec,
            runner=self.runner,
            state=self.state,
            ledger=self.ledger,
            budgets=self.budgets,
            alerts=self.alerts,
            approvals=self.approvals,
        )

    def issue_directive(
        self, tasks: list[dict[str, Any]], directive_id: str = "d1", **over: Any
    ) -> Directive:
        raw: dict[str, Any] = {
            "schema_version": 1,
            "id": directive_id,
            "issued_by": "owner",
            "assignee": self.spec.spec.name,
            "spec_sha256": self.spec.sha256,
            "goal": "keep the files tidy",
            "budget": {"calls": 10, "tokens": 50_000, "wall_seconds": 300, "spend_usd": 0.5},
            "tasks": tasks,
            **over,
        }
        constraints = to_constraints(self.spec)
        directive = parse_directive(raw, constraints, self.registry)
        issue(directive, constraints, self.ledger, self.registry)
        return directive

    def events(self, kind: str) -> list[dict[str, Any]]:
        return [e.event for e in self.ledger.entries() if e.event.get("kind") == kind]


def _flaky_tool() -> ToolSpec:
    """A T2 tool whose effect is never confirmed: it always ends 'unresolved'."""

    def execute(args: PathArgs, secrets: object) -> dict[str, object]:
        raise OSError("disk went away")

    def reconcile(args: PathArgs) -> Reconciliation:
        return Reconciliation("unknown")

    return ToolSpec(
        "file.flaky",
        1,
        PathArgs,
        Tier.T2,
        True,
        execute,  # type: ignore[arg-type]
        verify=lambda a, r: True,
        reconcile=reconcile,
    )


def default_prices() -> PriceTable:
    """The harness's price table: the step-up model is priced; local models are free."""
    return PriceTable.parse(
        {
            "as_of": datetime.now(UTC).date(),
            "models": {
                "claude/haiku-tag": {"input_per_mtok": 1.0, "output_per_mtok": 5.0},
                "claude/opus-tag": {"input_per_mtok": 15.0, "output_per_mtok": 75.0},
            },
        }
    )
