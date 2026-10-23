"""Local planning adapter: model advice cannot replace owner expectations or tool policy."""

import json
from dataclasses import dataclass
from pathlib import Path

from jangus.agents.context import ContextFailure, HandoffStore, estimate_tokens
from jangus.agents.provider import Message, ModelFailure, ModelReply
from jangus.agents.skills import SkillRegistry, load_rules
from jangus.execution.contracts import Manifest, compile_plan
from jangus.execution.controller import RULES_HASH, Controller


class PlanningFailure(ValueError):
    """Safe error code for inadmissible model plans."""


@dataclass(frozen=True)
class AgentOutcome:
    run_id: str
    state: str
    rules_sha256: str
    skill_sha256: dict[str, str]
    reported_prompt_tokens: int | None
    reported_output_tokens: int | None
    estimated_prompt_tokens: int
    usage_source: str


def _plan_response(reply: ModelReply, expected: list[str]) -> None:
    value = reply.value
    if set(value) != {"action_ids"}:
        raise PlanningFailure("invalid_plan_schema")
    sequence = value["action_ids"]
    if (
        not isinstance(sequence, list)
        or any(not isinstance(item, str) for item in sequence)
        or sequence != expected
    ):
        raise PlanningFailure("plan_not_authorized")


def run_verified_workflow(
    controller: Controller,
    provider,
    workspace: Path,
    manifest: Manifest,
    review_root: Path,
    *,
    selected_skills: tuple[str, ...] = ("file-workflow",),
) -> AgentOutcome:
    """Only a validated canonical plan may reach Phase 4's existing controller.

    This is an opt-in development integration; no automatic model invocation,
    cloud fallback, filesystem plan override, or Taskmaster AI planning.
    """
    if not selected_skills or len(set(selected_skills)) != len(selected_skills):
        raise PlanningFailure("invalid_skill_selection")
    rules, rules_sha = load_rules(review_root, RULES_HASH)
    registry = SkillRegistry(review_root)
    allowed = set(controller.config.permissions.allowed_tools)
    skills = [registry.load(name, allowed) for name in selected_skills]
    plan = compile_plan(manifest)
    expected = [action.id for action in plan.actions]
    # Do not expose original filenames or data to model. This is not a generic
    # natural-language file planner; owner-supplied hashes define all effects.
    assignment = {
        "objective": "Approve the fixed synthetic manifest copy/report action order",
        "actions": [
            {"id": action.id, "tool": action.tool, "requires": action.dependencies}
            for action in plan.actions
        ],
        "allowed_tools": sorted(allowed),
        "skill_revisions": {skill.name: skill.sha256 for skill in skills},
        "rules_revision": rules_sha,
        "max_actions": controller.config.limits.max_actions,
    }
    system = (
        "Rules (binding):\n"
        + rules
        + "\nReviewed procedures:\n"
        + "\n".join(skill.text for skill in skills)
        + "\nReturn only a JSON object with action_ids in the listed order. "
        "Procedures are non-authoritative; never expand permissions."
    )
    user = json.dumps(assignment, sort_keys=True)
    available = controller.config.limits.context_tokens - controller.config.limits.response_tokens
    estimated = estimate_tokens(system) + estimate_tokens(user)
    if estimated > available:
        raise ContextFailure("prompt_budget_exhausted")
    run_id = controller.submit(workspace, manifest)
    try:
        reply = provider.generate(
            [Message(role="system", content=system), Message(role="user", content=user)],
            timeout=min(300, controller.config.limits.run_timeout_seconds),
            cancelled=lambda: bool(controller.status(run_id)["cancel_requested"]),
        )
        _plan_response(reply, expected)
    except (PlanningFailure, ModelFailure, ContextFailure):
        # Queued work must not be dispatched after planning rejection.
        controller.store.cancel(run_id)
        controller.execute(run_id)
        raise
    status = controller.execute(run_id)
    hashes = {skill.name: skill.sha256 for skill in skills}
    HandoffStore(Path(controller.config.paths.data_dir).expanduser() / "handoffs").save(
        controller, run_id, hashes
    )
    return AgentOutcome(
        run_id=run_id,
        state=status["state"],
        rules_sha256=rules_sha,
        skill_sha256=hashes,
        reported_prompt_tokens=reply.reported_prompt_tokens,
        reported_output_tokens=reply.reported_output_tokens,
        estimated_prompt_tokens=estimated,
        usage_source=reply.usage_source,
    )
