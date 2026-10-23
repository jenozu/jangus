"""Isolated model roles over the authoritative Phase 4 file controller.

Role messages are deliberately non-authoritative. Only Controller executes tools,
records attempts, verifies effects, and decides terminal run state.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jangus.agents.context import ContextFailure, HandoffStore, estimate_tokens
from jangus.agents.provider import Message, ModelFailure, ModelReply
from jangus.agents.skills import SkillRegistry, load_rules
from jangus.agents.workflow import PlanningFailure
from jangus.execution.contracts import Manifest, compile_plan
from jangus.execution.controller import RULES_HASH, Controller

Role = Literal["planner", "worker", "verifier", "diagnostician"]
MAX_MODEL_CALLS = 4
MAX_ROLE_ACTIONS = 40


@dataclass(frozen=True)
class RoleReceipt:
    role: Role
    decision: str
    action_ids: tuple[str, ...]
    estimated_prompt_tokens: int
    reported_prompt_tokens: int | None
    reported_output_tokens: int | None
    usage_source: str
    assignment_sha256: str


@dataclass(frozen=True)
class CoordinatedOutcome:
    run_id: str
    controller_state: str
    independent_verification: bool
    review_status: str
    diagnosis_status: str
    roles: tuple[RoleReceipt, ...]
    rules_sha256: str
    skill_sha256: dict[str, str]
    model_calls: int
    handoff_sha256: str


def _safe_status(status: dict) -> dict:
    """Reduce state to approved identifiers, fixed error codes, and artifact hashes."""
    actions = []
    for action in status["actions"]:
        evidence = action["evidence"]
        actions.append(
            {
                "id": action["id"],
                "state": action["state"],
                "attempts": action["attempts"],
                "remaining_attempts": action["remaining_attempts"],
                "error": action["error"],
                "sha256": evidence.get("sha256") if isinstance(evidence, dict) else None,
                "bytes": evidence.get("bytes") if isinstance(evidence, dict) else None,
            }
        )
    return {
        "run_id": status["run_id"],
        "state": status["state"],
        "cancel_requested": status["cancel_requested"],
        "revision": status["revision"],
        "dispatches": status["dispatches"],
        "max_dispatches": status["max_dispatches"],
        "actions": actions,
    }


def _role_result(reply: ModelReply, role: Role, expected: list[str], fingerprint: str) -> str:
    """Every role returns a strict envelope bound to its own assignment revision."""
    value = reply.value
    if set(value) != {"role", "decision", "action_ids", "assignment_sha256"}:
        raise PlanningFailure("role_schema_invalid")
    if value["role"] != role or value["assignment_sha256"] != fingerprint:
        raise PlanningFailure("role_revision_changed")
    if (
        not isinstance(value["action_ids"], list)
        or any(type(item) is not str for item in value["action_ids"])
        or value["action_ids"] != expected
    ):
        raise PlanningFailure("role_action_mismatch")
    decisions = {
        "planner": {"ready"},
        "worker": {"ready", "reject"},
        "verifier": {"verified", "disagree"},
        "diagnostician": {"hold", "stop"},
    }
    decision = value["decision"]
    if type(decision) is not str or decision not in decisions[role]:
        raise PlanningFailure("role_decision_invalid")
    return decision


def _assignment(
    role: Role,
    status: dict,
    plan,
    allowed: set[str],
    skill_hashes: dict[str, str],
    rules_sha: str,
) -> dict:
    known = [
        {
            "id": a.id,
            "tool": a.tool,
            "requires": a.dependencies,
            "expected_sha256": a.expected_sha256,
        }
        for a in plan.actions
    ]
    if role == "diagnostician":
        focus = [a["id"] for a in status["actions"] if a["state"] != "verified"]
    else:
        focus = [a["id"] for a in known]
    return {
        "role": role,
        "objective": {
            "planner": "Validate only the owner-compiled dependencies and action order.",
            "worker": "Independently review allowed tools and action dependencies before dispatch.",
            "verifier": "Review independently verified receipts; never assert effects yourself.",
            "diagnostician": "Explain safe halt after failure or uncertainty; no new actions.",
        }[role],
        "run_id": status["run_id"],
        "plan_revision": status["revision"],
        "allowed_tools": sorted(allowed),
        "scope": "one_owner_manifest_workspace",
        "acceptance": "Controller independently verifies original manifest hashes.",
        "remaining_budget": {
            "dispatches": status["dispatches"],
            "remaining_dispatches": max(0, status["max_dispatches"] - status["dispatches"]),
            "remaining_attempts": {
                action["id"]: action["remaining_attempts"] for action in status["actions"]
            },
            "deadline_epoch": status["deadline"],
        },
        "actions": known,
        "focus_action_ids": focus,
        "skill_sha256": skill_hashes,
        "rules_sha256": rules_sha,
        "status": _safe_status(status) if role in {"verifier", "diagnostician"} else None,
    }


def _call(
    controller: Controller,
    provider,
    run_id: str,
    role: Role,
    plan,
    rules: str,
    rules_sha: str,
    skills: list,
    allowed: set[str],
) -> RoleReceipt:
    status = controller.status(run_id)
    hashes = {item.name: item.sha256 for item in skills}
    assignment = _assignment(role, status, plan, allowed, hashes, rules_sha)
    raw = json.dumps(assignment, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(raw.encode()).hexdigest()
    system = (
        "Binding shared rules:\n"
        + rules
        + "\nReviewed role procedures:\n"
        + "\n".join(s.text for s in skills)
        + "\nYou are a single isolated advisory role. Never execute tools, "
        "grant permissions, restore budgets, or claim task success. "
        "Return exactly JSON with role, decision, action_ids, assignment_sha256; "
        "echo the full focus_action_ids list unchanged and the exact assignment hash. "
        "Your role must match the assignment. Planner/worker use ready; "
        "worker may reject. Verifier uses verified or disagree. "
        "Diagnostician uses hold or stop."
    )
    user = raw + "\nassignment_sha256=" + fingerprint
    estimated = estimate_tokens(system) + estimate_tokens(user)
    usable = controller.config.limits.context_tokens - controller.config.limits.response_tokens
    if estimated > usable:
        raise ContextFailure("prompt_budget_exhausted")
    reply = provider.generate(
        [Message(role="system", content=system), Message(role="user", content=user)],
        timeout=min(controller.config.limits.run_timeout_seconds, 300),
        cancelled=lambda: bool(controller.status(run_id)["cancel_requested"]),
    )
    decision = _role_result(reply, role, assignment["focus_action_ids"], fingerprint)
    return RoleReceipt(
        role=role,
        decision=decision,
        action_ids=tuple(assignment["focus_action_ids"]),
        estimated_prompt_tokens=estimated,
        reported_prompt_tokens=reply.reported_prompt_tokens,
        reported_output_tokens=reply.reported_output_tokens,
        usage_source=reply.usage_source,
        assignment_sha256=fingerprint,
    )


def run_coordinated_workflow(
    controller: Controller,
    provider,
    workspace: Path,
    manifest: Manifest,
    review_root: Path,
    *,
    selected_skills: tuple[str, ...] = ("file-workflow",),
) -> CoordinatedOutcome:
    """Four possible advisory roles; no more than three are called in a run.

    Only the existing owner manifest may specify actual file effects. Each role
    gets a fresh isolated context and a bounded response budget. Recovery stays
    inside Controller: no advisor is allowed to restart terminal actions.
    """
    if not selected_skills or len(set(selected_skills)) != len(selected_skills):
        raise PlanningFailure("invalid_skill_selection")
    rules, rules_sha = load_rules(review_root, RULES_HASH)
    allowed = set(controller.config.permissions.allowed_tools)
    skills = [SkillRegistry(review_root).load(name, allowed) for name in selected_skills]
    hashes = {item.name: item.sha256 for item in skills}
    plan = compile_plan(manifest)
    if len(plan.actions) > MAX_ROLE_ACTIONS:
        raise PlanningFailure("too_many_actions")
    run_id = controller.submit(workspace, manifest)
    receipts: list[RoleReceipt] = []
    attempted_calls = 0
    handoffs = HandoffStore(Path(controller.config.paths.data_dir).expanduser() / "handoffs")

    try:
        for role in ("planner", "worker"):
            attempted_calls += 1
            receipt = _call(
                controller, provider, run_id, role, plan, rules, rules_sha, skills, allowed
            )
            receipts.append(receipt)
            if receipt.decision != "ready":
                raise PlanningFailure("worker_rejected")
    except (ModelFailure, PlanningFailure, ContextFailure):
        controller.store.cancel(run_id)
        controller.execute(run_id)
        handoffs.save(controller, run_id, hashes)
        raise

    # Phase 4 alone owns retry, effect reconciliation, status and verification.
    status = controller.execute(run_id)
    snapshot = handoffs.save(controller, run_id, hashes)
    review_status = "not_run"
    diagnosis_status = "not_run"
    independent = status["state"] == "succeeded" and all(
        a["state"] == "verified" and a["evidence"] for a in status["actions"]
    )
    if status["state"] == "succeeded":
        try:
            attempted_calls += 1
            reviewer = _call(
                controller, provider, run_id, "verifier", plan, rules, rules_sha, skills, allowed
            )
            receipts.append(reviewer)
            review_status = reviewer.decision
        except (ModelFailure, PlanningFailure, ContextFailure):
            review_status = "unavailable"
    else:
        try:
            attempted_calls += 1
            diagnosis = _call(
                controller,
                provider,
                run_id,
                "diagnostician",
                plan,
                rules,
                rules_sha,
                skills,
                allowed,
            )
            receipts.append(diagnosis)
            diagnosis_status = diagnosis.decision
        except (ModelFailure, PlanningFailure, ContextFailure):
            diagnosis_status = "unavailable"
    return CoordinatedOutcome(
        run_id=run_id,
        controller_state=status["state"],
        independent_verification=bool(independent),
        review_status=review_status,
        diagnosis_status=diagnosis_status,
        roles=tuple(receipts),
        rules_sha256=rules_sha,
        skill_sha256=hashes,
        model_calls=attempted_calls,
        handoff_sha256=hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
