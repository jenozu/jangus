import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.executor import AgentContext, Executor
from jangus.kernel.ledger import Ledger
from jangus.kernel.registry import Reconciliation, Registry, Tier, ToolSpec
from jangus.kernel.state import StateStore
from tests.fakes import FakeWorld, PathArgs, build_registry

ALL = {"file.read", "file.write", "file.delete"}


class Crash(BaseException):
    """Simulated process death: not an Exception, so nothing in the kernel can swallow it."""


class Rig:
    def __init__(self, tmp_path: Path, registry_builder: Any = build_registry) -> None:
        self.tmp = tmp_path
        self.world = FakeWorld()
        self.registry: Registry = registry_builder(self.world)
        self.agent = AgentContext(allowed_tools=ALL, max_tier=Tier.T2, budget="calls")
        self.open()
        self.state.set_budget("calls", 100)
        self.run_id = self.state.create_run("job-1", "files").id

    def open(self, checkpoint: Any = None) -> None:
        self.state = StateStore.open(self.tmp / "state.db")
        self.ledger = Ledger.open(self.tmp / "ledger.jsonl", clock=lambda: "t")
        self.executor = Executor(self.registry, self.state, self.ledger, checkpoint=checkpoint)

    def restart(self) -> None:
        self.state.close()
        self.open()

    def kinds(self) -> list[str]:
        return [e.event["kind"] for e in self.ledger.entries()]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


# 8.1 happy path, rejections, queueing --------------------------------------------------


def test_t1_runs_and_is_logged(rig: Rig) -> None:
    r = rig.executor.submit(rig.run_id, rig.agent, "file.read", {"path": "/work/a"})
    assert r.status == "done"
    assert rig.world.calls == ["read:/work/a"]
    assert rig.kinds() == ["intent", "result"]


def test_t2_runs_verified_and_duplicate_key_does_not_rerun(rig: Rig) -> None:
    first = rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/a"}, key="k")
    again = rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/a"}, key="k")
    assert (first.status, again.status) == ("done", "already_done")
    assert rig.world.calls == ["write:/work/a"]
    assert rig.state.get_budget("calls").spent == 1


# B01
def test_unknown_tool_rejected_and_logged(rig: Rig) -> None:
    r = rig.executor.submit(rig.run_id, rig.agent, "shell.run", {"cmd": "rm -rf /"})
    assert r.status == "rejected"
    assert rig.world.calls == []
    assert rig.kinds() == ["rejected"]
    assert rig.ledger.entries()[0].event["reason"] == "unknown_tool"
    assert rig.state.get_budget("calls").spent == 0


# B02
def test_t3_is_queued_and_does_not_run(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    agent = AgentContext(allowed_tools=ALL, max_tier=Tier.T3, budget="calls")
    r = rig.executor.submit(rig.run_id, agent, "file.delete", {"path": "/work/a"})
    assert r.status == "queued"
    assert r.decision.payload_hash is not None
    assert rig.world.calls == []
    assert rig.state.list_open_actions() == []
    assert rig.kinds() == ["queued"]


# B03
def test_above_max_tier_is_rejected_not_queued(rig: Rig) -> None:
    r = rig.executor.submit(rig.run_id, rig.agent, "file.delete", {"path": "/work/a"})
    assert r.status == "rejected"
    assert rig.world.calls == []
    assert rig.kinds() == ["rejected"]


# B04
def test_schema_violation_rejected_without_effect(rig: Rig) -> None:
    r = rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": 5, "x": 1})
    assert r.status == "rejected"
    assert rig.world.calls == []


def test_ledger_holds_hashes_not_arguments(rig: Rig) -> None:
    rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/CANARY-7731"})
    text = (rig.tmp / "ledger.jsonl").read_text()
    assert "CANARY-7731" not in text


def test_failed_verify_is_unresolved_and_blocks_retry(tmp_path: Path) -> None:
    def builder(world: FakeWorld) -> Registry:
        reg = Registry()

        def write(args: PathArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
            world.calls.append("write")
            return {}

        reg.register(
            ToolSpec(
                "file.write",
                1,
                PathArgs,
                Tier.T2,
                True,
                write,
                verify=lambda a, r: False,
                reconcile=lambda a: Reconciliation("unknown"),
            )
        )
        return reg

    rig = Rig(tmp_path, builder)
    r = rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/a"}, key="k")
    assert r.status == "unresolved"
    retry = rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/a"}, key="k")
    assert retry.status == "unresolved"
    assert rig.world.calls == ["write"] and rig.world.calls.count("write") == 1


def test_tool_exception_is_unresolved(tmp_path: Path) -> None:
    def builder(world: FakeWorld) -> Registry:
        reg = Registry()

        def boom(args: PathArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
            raise OSError("disk exploded")

        reg.register(ToolSpec("file.read", 1, PathArgs, Tier.T1, True, boom))
        return reg

    rig = Rig(tmp_path, builder)
    r = rig.executor.submit(rig.run_id, rig.agent, "file.read", {"path": "/work/a"})
    assert r.status == "unresolved"
    assert "disk exploded" not in (rig.tmp / "ledger.jsonl").read_text()


# 8.2 / 8.3 B05 crash boundaries ------------------------------------------------------------

POINTS = [
    "after_intent_state",
    "after_intent_ledger",
    "after_effect",
    "after_verify",
    "after_result_ledger",
]


@pytest.mark.parametrize("point", POINTS)
def test_crash_then_restart_no_duplicate_effect(tmp_path: Path, point: str) -> None:
    rig = Rig(tmp_path)

    def die(name: str) -> None:
        if name == point:
            raise Crash

    rig.open(checkpoint=die)
    with pytest.raises(Crash):
        rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/a"}, key="k")
    rig.restart()
    settled = rig.executor.recover()
    assert [s.status for s in settled] == ["done"]
    assert rig.world.calls == ["write:/work/a"]  # exactly one effect, never two
    action = rig.state.get_action("k")
    assert action is not None and action.status == "done"
    again = rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/a"}, key="k")
    assert again.status == "already_done"
    assert rig.world.calls == ["write:/work/a"]
    assert rig.ledger.verify_chain() > 0
    assert rig.executor.recover() == []  # nothing left to settle


def test_unknown_reconcile_marks_unresolved_and_never_reruns(tmp_path: Path) -> None:
    def builder(world: FakeWorld) -> Registry:
        reg = Registry()

        def write(args: PathArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
            world.calls.append("write")
            return {}

        reg.register(
            ToolSpec(
                "file.write",
                1,
                PathArgs,
                Tier.T2,
                True,
                write,
                verify=lambda a, r: True,
                reconcile=lambda a: Reconciliation("unknown"),
            )
        )
        return reg

    rig = Rig(tmp_path, builder)

    def die(name: str) -> None:
        if name == "after_effect":
            raise Crash

    rig.open(checkpoint=die)
    with pytest.raises(Crash):
        rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/a"}, key="k")
    rig.restart()
    assert [s.status for s in rig.executor.recover()] == ["unresolved"]
    retry = rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/a"}, key="k")
    assert retry.status == "unresolved"
    assert rig.world.calls == ["write"]


def test_args_survive_restart_for_reconcile(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    def die(name: str) -> None:
        if name == "after_effect":
            raise Crash

    rig.open(checkpoint=die)
    with pytest.raises(Crash):
        rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/zzz"}, key="k")
    rig.restart()
    action = rig.state.list_open_actions()[0]
    assert json.loads(action.args_json) == {"path": "/work/zzz"}


# B06 -----------------------------------------------------------------------------------------


def _break(path: Path) -> None:
    path.unlink(missing_ok=True)
    path.mkdir()


def test_unwritable_ledger_blocks_new_effects(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.executor.submit(rig.run_id, rig.agent, "file.read", {"path": "/work/a"})
    before = list(rig.world.calls)
    _break(tmp_path / "ledger.jsonl")
    with pytest.raises(RuntimeFault):
        rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/b"})
    assert rig.world.calls == before  # the failed submit caused no effect
    with pytest.raises(RuntimeFault) as info:
        rig.executor.submit(rig.run_id, rig.agent, "file.read", {"path": "/work/c"})
    assert info.value.code is FaultCode.LEDGER_UNAVAILABLE
    assert rig.world.calls == before


def test_ledger_failing_between_intent_and_effect_prevents_effect(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    def sabotage(name: str) -> None:
        if name == "after_intent_state":
            _break(tmp_path / "ledger.jsonl")

    rig.open(checkpoint=sabotage)
    with pytest.raises(RuntimeFault):
        rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/a"}, key="k")
    assert rig.world.calls == []
    assert not rig.ledger.effects_allowed


def test_unwritable_state_blocks_new_effects(rig: Rig) -> None:
    rig.state.close()
    with pytest.raises(RuntimeFault):
        rig.executor.submit(rig.run_id, rig.agent, "file.write", {"path": "/work/a"})
    assert rig.world.calls == []
    with pytest.raises(RuntimeFault):
        rig.executor.submit(rig.run_id, rig.agent, "file.read", {"path": "/work/a"})
    assert rig.world.calls == []
