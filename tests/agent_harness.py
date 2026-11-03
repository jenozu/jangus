"""Shared test harness for the agent runtime: a scripted fake model and a wired-up runner.

Everything here is fake: no network, no real model. Results prove the kernel plumbing only.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jangus.agents.providers.base import (
    ModelRequest,
    ModelResponse,
    ProviderHub,
    Usage,
)
from jangus.agents.runner import Runner
from jangus.agents.spec import LoadedSpec, parse_spec
from jangus.kernel.alerts import AlertQueue
from jangus.kernel.budgets import BudgetBook
from jangus.kernel.executor import Executor
from jangus.kernel.ledger import Ledger
from jangus.kernel.state import StateStore
from tests.fakes import FakeWorld, build_registry

SPEC_YAML = """\
schema_version: 1
name: file-agent
role: "You keep files tidy."
model: {{provider: ollama, name: "qwen3:8b", max_tokens: 512}}
tools: [file.read, file.write, file.delete]
max_tier: T2
budget: {{calls: {calls}, tokens: {tokens}, wall_seconds: 600, spend_usd: 1.0}}
escalation_model: {{provider: claude, name: haiku-tag, effort: low}}
attempts: {{default_model: 2, step_up: 1, max_model_calls: {max_calls}, max_proposals: {max_props}}}
"""


def make_spec(
    calls: int = 20, tokens: int = 100_000, max_calls: int = 6, max_props: int = 12
) -> LoadedSpec:
    text = SPEC_YAML.format(calls=calls, tokens=tokens, max_calls=max_calls, max_props=max_props)
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
    ) -> None:
        self.world = FakeWorld()
        self.registry = build_registry(self.world)
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
        self.executor = Executor(self.registry, self.state, self.ledger)
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
        )
        self.runner.ensure_budgets()

    def events(self, kind: str) -> list[dict[str, Any]]:
        return [e.event for e in self.ledger.entries() if e.event.get("kind") == kind]
