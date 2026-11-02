"""Fake tools for kernel tests. They touch only an in-memory dict."""

from collections.abc import Mapping
from typing import Any

from jangus.config import StrictModel
from jangus.kernel.registry import Reconciliation, Registry, Tier, ToolSpec


class PathArgs(StrictModel):
    path: str


class FakeWorld:
    """Stands in for the outside world: records every effect."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.calls: list[str] = []


def build_registry(world: FakeWorld) -> Registry:
    registry = Registry()

    def read(args: PathArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        world.calls.append(f"read:{args.path}")
        return {"content": world.files.get(args.path)}

    def write(args: PathArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        world.calls.append(f"write:{args.path}")
        world.files[args.path] = "data"
        return {"path": args.path}

    def verify_write(args: PathArgs, result: dict[str, Any]) -> bool:
        return world.files.get(args.path) == "data"

    def reconcile_write(args: PathArgs) -> Reconciliation:
        if world.files.get(args.path) == "data":
            return Reconciliation("applied", {"path": args.path})
        return Reconciliation("absent")

    def delete(args: PathArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        world.calls.append(f"delete:{args.path}")
        world.files.pop(args.path, None)
        return {"path": args.path}

    def verify_delete(args: PathArgs, result: dict[str, Any]) -> bool:
        return args.path not in world.files

    def reconcile_delete(args: PathArgs) -> Reconciliation:
        return Reconciliation("applied" if args.path not in world.files else "absent")

    def outside_workspace(args: PathArgs) -> Tier | None:
        return Tier.T3 if not args.path.startswith("/work/") else None

    registry.register(ToolSpec("file.read", 1, PathArgs, Tier.T1, True, read))
    registry.register(
        ToolSpec(
            "file.write",
            1,
            PathArgs,
            Tier.T2,
            True,
            write,
            verify=verify_write,
            reconcile=reconcile_write,
            elevation=outside_workspace,
        )
    )
    registry.register(
        ToolSpec(
            "file.delete",
            1,
            PathArgs,
            Tier.T3,
            False,
            delete,
            verify=verify_delete,
            reconcile=reconcile_delete,
        )
    )
    return registry
