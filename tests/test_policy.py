from collections.abc import Mapping
from typing import Any

import pytest

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.policy import Outcome, RejectReason, decide, payload_hash
from jangus.kernel.registry import Registry, Tier, ToolSpec
from tests.fakes import FakeWorld, PathArgs, build_registry

ALL = {"file.read", "file.write", "file.delete"}


@pytest.fixture
def registry() -> Registry:
    return build_registry(FakeWorld())


def run(registry: Registry, name: str, args: object, max_tier: Tier = Tier.T3, allowed=ALL):  # type: ignore[no-untyped-def]
    return decide(registry, name, args, allowed_tools=allowed, agent_max_tier=max_tier)


# B01
def test_unknown_tool_rejected(registry: Registry) -> None:
    d = run(registry, "shell.run", {"cmd": "ls"})
    assert (d.outcome, d.reason) == (Outcome.REJECT, RejectReason.UNKNOWN_TOOL)


def test_tool_outside_agent_list_rejected(registry: Registry) -> None:
    d = run(registry, "file.delete", {"path": "/work/a"}, allowed={"file.read"})
    assert (d.outcome, d.reason) == (Outcome.REJECT, RejectReason.TOOL_NOT_ALLOWED)


# B02
def test_t1_runs_t2_runs_and_logs_t3_queues(registry: Registry) -> None:
    assert run(registry, "file.read", {"path": "/work/a"}).outcome is Outcome.RUN
    assert run(registry, "file.write", {"path": "/work/a"}).outcome is Outcome.RUN_LOG
    d = run(registry, "file.delete", {"path": "/work/a"})
    assert d.outcome is Outcome.QUEUE
    assert d.payload_hash is not None


# B03
def test_above_max_tier_rejected_not_queued(registry: Registry) -> None:
    d = run(registry, "file.delete", {"path": "/work/a"}, max_tier=Tier.T2)
    assert (d.outcome, d.reason) == (Outcome.REJECT, RejectReason.ABOVE_MAX_TIER)


def test_elevation_outside_workspace_raises_tier(registry: Registry) -> None:
    inside = run(registry, "file.write", {"path": "/work/a"}, max_tier=Tier.T2)
    assert inside.outcome is Outcome.RUN_LOG
    outside = run(registry, "file.write", {"path": "/etc/passwd"}, max_tier=Tier.T3)
    assert (outside.outcome, outside.tier) == (Outcome.QUEUE, Tier.T3)
    blocked = run(registry, "file.write", {"path": "/etc/passwd"}, max_tier=Tier.T2)
    assert (blocked.outcome, blocked.reason) == (Outcome.REJECT, RejectReason.ABOVE_MAX_TIER)


# B04
@pytest.mark.parametrize(
    "args",
    [{}, {"path": 5}, {"path": "/work/a", "extra": 1}, "path=/work/a", None, ["/work/a"]],
)
def test_malformed_args_rejected(registry: Registry, args: object) -> None:
    d = run(registry, "file.read", args)
    assert (d.outcome, d.reason) == (Outcome.REJECT, RejectReason.SCHEMA_VIOLATION)
    assert d.args is None


def test_payload_hash_is_stable_and_sensitive(registry: Registry) -> None:
    a = run(registry, "file.delete", {"path": "/work/a"})
    b = run(registry, "file.delete", {"path": "/work/a"})
    c = run(registry, "file.delete", {"path": "/work/b"})
    assert a.payload_hash == b.payload_hash != c.payload_hash
    tool = registry.get("file.delete")
    assert tool is not None
    assert payload_hash(tool, PathArgs(path="/work/a")) == a.payload_hash


# Registration rules
def _noop(args: Any, secrets: Mapping[str, str]) -> dict[str, Any]:
    return {}


def test_t2_t3_need_verify_and_reconcile() -> None:
    registry = Registry()
    for tier in (Tier.T2, Tier.T3):
        with pytest.raises(RuntimeFault) as info:
            registry.register(ToolSpec(f"x.{tier}", 1, PathArgs, tier, True, _noop))
        assert info.value.code is FaultCode.TOOL_REGISTRATION_INVALID
    registry.register(ToolSpec("x.read", 1, PathArgs, Tier.T1, True, _noop))


def test_duplicate_and_bad_args_model_refused() -> None:
    registry = Registry()
    registry.register(ToolSpec("x.read", 1, PathArgs, Tier.T1, True, _noop))
    with pytest.raises(RuntimeFault):
        registry.register(ToolSpec("x.read", 1, PathArgs, Tier.T1, True, _noop))
    with pytest.raises(RuntimeFault):
        registry.register(ToolSpec("y.read", 1, dict, Tier.T1, True, _noop))  # type: ignore[arg-type]
