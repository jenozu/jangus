"""Directive models and validation (docs/DIRECTIVES.md).

A directive narrows what an approved spec already allows; it never grants. Task
status is set only by the kernel from verified evidence, never by model output.
"""

import hashlib
import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from jangus.config import StrictModel
from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.ledger import Ledger
from jangus.kernel.registry import Registry, Tier

TaskStatus = Literal["pending", "in-progress", "done", "review", "deferred"]
Priority = Literal["high", "medium", "low"]


class Budget(StrictModel):
    calls: int
    tokens: int
    wall_seconds: int
    spend_usd: float


class Step(StrictModel):
    tool: str
    args: dict[str, Any]


class Task(StrictModel):
    id: int
    title: str
    status: TaskStatus = "pending"
    priority: Priority = "medium"
    dependencies: tuple[int, ...] = ()
    details: str = ""
    testStrategy: str  # noqa: N815 - field name is part of the shared Taskmaster schema
    tools: tuple[str, ...] = ()
    runner: Literal["kernel", "model"] = "model"
    steps: tuple[Step, ...] = ()
    recipe: str | None = None
    task_type: str | None = None
    max_input_tokens: int | None = None
    max_model_calls: int | None = None
    max_proposals: int | None = None
    max_wall_seconds: int | None = None


class Directive(StrictModel):
    schema_version: Literal[1]
    id: str
    issued_by: str
    assignee: str
    spec_sha256: str
    goal: str
    non_goals: tuple[str, ...] = ()
    escalate: bool = False
    max_tier: Tier | None = None
    watcher: str | None = None
    supersedes: str | None = None
    budget: Budget
    tasks: tuple[Task, ...]


@dataclass(frozen=True)
class SpecConstraints:
    """The parts of an approved agent spec that bound a directive."""

    name: str
    sha256: str
    tools: frozenset[str]
    max_tier: Tier
    budget: Budget
    max_model_calls: int = 6
    max_proposals: int = 12
    max_wall_seconds: int | None = None
    max_input_tokens: int | None = None


@dataclass(frozen=True)
class Evidence:
    """Kernel-made proof for a status change. Models cannot construct a trusted one."""

    ledger_seq: int
    verified: bool


def _invalid(reason: str) -> RuntimeFault:
    return RuntimeFault(FaultCode.DIRECTIVE_INVALID, reason)


def parse_directive(
    raw: object, spec: SpecConstraints, registry: Registry | None = None
) -> Directive:
    """Parse untrusted directive data. Any supplied task status is ignored (set to pending)."""
    if not isinstance(raw, dict):
        raise _invalid("directive must be an object")
    data: dict[str, Any] = dict(raw)
    tasks = data.get("tasks")
    if isinstance(tasks, list):
        data["tasks"] = [
            {k: v for k, v in t.items() if k != "status"} if isinstance(t, dict) else t
            for t in tasks
        ]
    try:
        # JSON mode keeps strict typing (no "5" -> 5) while accepting arrays and enum strings.
        directive = Directive.model_validate_json(json.dumps(data, allow_nan=False))
    except (ValidationError, TypeError, ValueError) as exc:
        if not isinstance(exc, ValidationError):
            raise _invalid("directive is not plain JSON data") from None
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()
        )
        raise _invalid(problems) from None
    validate_directive(directive, spec, registry)
    return directive


def _check_limit(name: str, value: int | None, ceiling: int | None) -> None:
    if value is not None and ceiling is not None and value > ceiling:
        raise _invalid(f"{name} {value} is looser than the spec limit {ceiling}")


def validate_directive(
    directive: Directive, spec: SpecConstraints, registry: Registry | None = None
) -> None:
    if directive.assignee != spec.name or directive.spec_sha256 != spec.sha256:
        raise _invalid("directive does not match the approved spec (name or hash)")
    ceiling = directive.max_tier or spec.max_tier
    if ceiling.rank > spec.max_tier.rank:
        raise _invalid("tier ceiling is above the spec max_tier")
    for field in ("calls", "tokens", "wall_seconds", "spend_usd"):
        if getattr(directive.budget, field) > getattr(spec.budget, field):
            raise _invalid(f"budget {field} is looser than the spec budget")
        if getattr(directive.budget, field) < 0:
            raise _invalid(f"budget {field} is negative")
    if not directive.tasks:
        raise _invalid("directive has no tasks")

    seen: set[int] = set()
    for task in directive.tasks:
        if task.id < 1 or task.id in seen:
            raise _invalid(f"task id {task.id} is not unique and positive")
        for dep in task.dependencies:
            if dep >= task.id or dep not in seen:
                raise _invalid(f"task {task.id} depends on {dep}: only lower existing ids allowed")
        seen.add(task.id)
        _check_task(task, spec, ceiling, registry)


def _check_task(
    task: Task, spec: SpecConstraints, ceiling: Tier, registry: Registry | None
) -> None:
    outside = set(task.tools) - spec.tools
    if outside:
        raise _invalid(f"task {task.id} asks for tools outside the spec: {sorted(outside)}")
    if task.runner == "kernel":
        if not task.steps:
            raise _invalid(f"task {task.id} is kernel-run but has no steps")
        stray = {s.tool for s in task.steps} - set(task.tools)
        if stray:
            raise _invalid(f"task {task.id} steps use tools not in its tools list: {sorted(stray)}")
    elif task.steps:
        raise _invalid(f"task {task.id} is model-run but has steps")
    if registry is not None:
        for name in task.tools:
            tool = registry.get(name)
            if tool is None:
                raise _invalid(f"task {task.id} names unknown tool {name!r}")
            if tool.tier.rank > ceiling.rank:
                raise _invalid(f"task {task.id} tool {name!r} is above the tier ceiling")
    _check_limit("max_model_calls", task.max_model_calls, spec.max_model_calls)
    _check_limit("max_proposals", task.max_proposals, spec.max_proposals)
    _check_limit("max_wall_seconds", task.max_wall_seconds, spec.max_wall_seconds)
    _check_limit("max_input_tokens", task.max_input_tokens, spec.max_input_tokens)
    for name, value in (
        ("max_model_calls", task.max_model_calls),
        ("max_proposals", task.max_proposals),
        ("max_wall_seconds", task.max_wall_seconds),
        ("max_input_tokens", task.max_input_tokens),
    ):
        if value is not None and value < 1:
            raise _invalid(f"task {task.id} {name} must be at least 1")


def directive_hash(directive: Directive) -> str:
    text = json.dumps(
        directive.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def issue(
    directive: Directive, spec: SpecConstraints, ledger: Ledger, registry: Registry | None = None
) -> str:
    """Validate, record the directive's hash in the ledger, and return the hash."""
    validate_directive(directive, spec, registry)
    if any(t.status != "pending" for t in directive.tasks):
        raise _invalid("a directive must be issued with every task pending")
    digest = directive_hash(directive)
    ledger.append(
        {
            "kind": "directive_issued",
            "id": directive.id,
            "hash": digest,
            "assignee": directive.assignee,
            "spec_sha256": directive.spec_sha256,
            "supersedes": directive.supersedes,
        }
    )
    return digest


_ALLOWED: dict[str, Collection[str]] = {
    "pending": {"in-progress", "deferred"},
    "in-progress": {"done", "review", "deferred"},
    "review": {"done", "deferred"},
    "done": set(),
    "deferred": set(),
}


def kernel_set_status(
    directive: Directive, task_id: int, new: TaskStatus, evidence: Evidence
) -> Directive:
    """The only status transition path. Returns a new directive; the old one is unchanged."""
    task = next((t for t in directive.tasks if t.id == task_id), None)
    if task is None:
        raise _invalid(f"no task {task_id}")
    if new not in _ALLOWED[task.status]:
        raise _invalid(f"task {task_id}: cannot go from {task.status} to {new}")
    if new == "done":
        if not evidence.verified:
            raise _invalid(f"task {task_id}: done needs verified evidence")
        by_id = {t.id: t for t in directive.tasks}
        if any(by_id[d].status != "done" for d in task.dependencies):
            raise _invalid(f"task {task_id}: dependencies are not done")
    tasks = tuple(
        t.model_copy(update={"status": new}) if t.id == task_id else t for t in directive.tasks
    )
    return directive.model_copy(update={"tasks": tasks})
