"""Validate reviewed Taskmaster dependencies without claiming real-world acceptance."""

from dataclasses import dataclass

STATUS = frozenset({"done", "pending", "in-progress", "deferred"})
GROUPS = {
    "foundation": tuple(range(1, 12)),
    "local_mvp": tuple(range(12, 23)),
    "private_vps": tuple(range(23, 27)),
    "optional": tuple(range(27, 31)),
}


class ReadinessFault(ValueError):
    """Stable, non-sensitive error for malformed or inconsistent backlog."""


@dataclass(frozen=True)
class Entry:
    id: int
    status: str
    dependencies: tuple[int, ...]


def _int_id(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ReadinessFault("task_id_invalid")
    try:
        number = int(value)
    except ValueError:
        raise ReadinessFault("task_id_invalid") from None
    if str(number) != str(value) or not 1 <= number <= 10000:
        raise ReadinessFault("task_id_invalid")
    return number


def parse_backlog(raw: dict) -> dict[int, Entry]:
    if not isinstance(raw, dict) or not isinstance(raw.get("master"), dict):
        raise ReadinessFault("backlog_invalid")
    rows = raw["master"].get("tasks")
    if not isinstance(rows, list) or len(rows) > 500:
        raise ReadinessFault("backlog_invalid")
    tasks: dict[int, Entry] = {}
    for item in rows:
        if not isinstance(item, dict):
            raise ReadinessFault("backlog_invalid")
        ident = _int_id(item.get("id"))
        deps = item.get("dependencies")
        state = item.get("status")
        if (
            ident in tasks
            or type(state) is not str
            or state not in STATUS
            or not isinstance(deps, list)
            or len(deps) > 100
        ):
            raise ReadinessFault("backlog_invalid")
        converted = tuple(_int_id(x) for x in deps)
        if ident in converted or len(set(converted)) != len(converted):
            raise ReadinessFault("dependency_invalid")
        tasks[ident] = Entry(ident, state, converted)
    for numbers in GROUPS.values():
        if any(ident not in tasks for ident in numbers):
            raise ReadinessFault("required_tasks_missing")
    for item in tasks.values():
        if any(dep not in tasks for dep in item.dependencies):
            raise ReadinessFault("dependency_missing")
        if item.status == "done" and any(
            tasks[dep].status != "done" for dep in item.dependencies
        ):
            raise ReadinessFault("completed_dependency_incomplete")

    seen: set[int] = set()
    visiting: set[int] = set()

    def visit(ident: int) -> None:
        if ident in visiting:
            raise ReadinessFault("dependency_cycle")
        if ident in seen:
            return
        visiting.add(ident)
        for dep in tasks[ident].dependencies:
            visit(dep)
        visiting.remove(ident)
        seen.add(ident)

    for ident in tasks:
        visit(ident)
    return tasks


def report(raw: dict) -> dict:
    tasks = parse_backlog(raw)
    gates: dict[str, dict] = {}
    for group, ids in GROUPS.items():
        undone = [ident for ident in ids if tasks[ident].status != "done"]
        gates[group] = {
            "done": len(ids) - len(undone),
            "total": len(ids),
            "taskmaster_complete": not undone,
            "outstanding_task_ids": undone,
        }
    ready_now = [
        item.id
        for item in tasks.values()
        if item.status != "done"
        and item.status != "deferred"
        and all(tasks[dep].status == "done" for dep in item.dependencies)
    ]
    return {
        "schema": 1,
        "source": "reviewed_taskmaster_backlog",
        "gates": gates,
        "next_unblocked_task_ids": sorted(ready_now),
        "owner_acceptance": "not_evaluated_by_backlog_check",
        "real_model_verified": "not_evaluated",
        "live_vault_verified": "not_evaluated",
        "live_vps_verified": "not_evaluated",
        "provider_access_verified": "not_evaluated",
        "deployment_or_merges_performed": False,
    }
