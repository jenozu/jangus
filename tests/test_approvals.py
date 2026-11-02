from pathlib import Path

import pytest

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.approvals import ApprovalQueue
from jangus.kernel.executor import AgentContext, Executor
from jangus.kernel.ledger import Ledger
from jangus.kernel.registry import Tier
from jangus.kernel.state import StateStore
from tests.fakes import FakeWorld, build_registry

ALL = {"file.read", "file.write", "file.delete"}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000

    def __call__(self) -> int:
        return self.now


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.clock = Clock()
        self.state = StateStore.open(tmp_path / "state.db")
        self.state.set_budget("calls", 10)
        self.run_id = self.state.create_run("job", "files").id
        self.ledger = Ledger.open(tmp_path / "ledger.jsonl", clock=lambda: "t")
        self.queue = ApprovalQueue(self.state, self.ledger, clock=self.clock)
        self.world = FakeWorld()
        self.executor = Executor(
            build_registry(self.world), self.state, self.ledger, approvals=self.queue
        )
        self.agent = AgentContext(ALL, Tier.T3, "calls")

    def delete(self, path: str = "/work/a") -> str:
        return self.executor.submit(self.run_id, self.agent, "file.delete", {"path": path}).status


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_t3_is_queued_not_run_then_runs_once_after_approval(rig: Rig) -> None:
    assert rig.delete() == "queued"
    assert rig.delete() == "queued"  # re-proposal does not create a second pending item
    assert rig.world.calls == []
    [pending] = rig.queue.list("pending")
    rig.queue.approve(pending.id, "jenozu")
    assert rig.delete() == "done"
    assert rig.world.calls == ["delete:/work/a"]
    assert rig.queue.list("used")[0].id == pending.id
    assert rig.delete() == "queued"  # the approval was single-use
    assert rig.world.calls == ["delete:/work/a"]


def test_changed_payload_invalidates_approval(rig: Rig) -> None:
    rig.delete("/work/a")
    rig.queue.approve(rig.queue.list("pending")[0].id, "jenozu")
    assert rig.delete("/work/b") == "queued"  # different payload: approval does not apply
    assert rig.world.calls == []
    assert len(rig.queue.list("pending")) == 1
    assert rig.queue.list("approved")[0].payload_hash != rig.queue.list("pending")[0].payload_hash


def test_approval_for_another_run_does_not_apply(rig: Rig) -> None:
    rig.delete()
    rig.queue.approve(rig.queue.list("pending")[0].id, "jenozu")
    other = rig.state.create_run("job-2", "files").id
    status = rig.executor.submit(other, rig.agent, "file.delete", {"path": "/work/a"}).status
    assert status == "queued"
    assert rig.world.calls == []


def test_expired_approval_refused(rig: Rig) -> None:
    rig.delete()
    approval = rig.queue.list("pending")[0]
    rig.queue.approve(approval.id, "jenozu")
    rig.clock.now = approval.expires_at  # exactly at expiry counts as expired
    assert rig.delete() == "queued"
    assert rig.world.calls == []
    assert rig.queue.expire_due() >= 1
    assert rig.queue.list("expired")


def test_cannot_approve_after_expiry_or_twice_or_after_reject(rig: Rig) -> None:
    rig.delete()
    a = rig.queue.list("pending")[0]
    rig.clock.now = a.expires_at + 1
    with pytest.raises(RuntimeFault) as info:
        rig.queue.approve(a.id, "jenozu")
    assert info.value.code is FaultCode.APPROVAL_INVALID

    rig.clock.now = 1_000
    rig.delete("/work/c")
    b = rig.queue.list("pending")[-1]
    rig.queue.reject(b.id, "jenozu")
    with pytest.raises(RuntimeFault):
        rig.queue.approve(b.id, "jenozu")
    assert rig.delete("/work/c") == "queued"
    assert rig.world.calls == []


def test_approve_records_owner_in_ledger_and_state(rig: Rig) -> None:
    rig.delete()
    a = rig.queue.list("pending")[0]
    done = rig.queue.approve(a.id, "jenozu")
    assert done.decided_by == "jenozu"
    event = next(e.event for e in rig.ledger.entries() if e.event["kind"] == "approval_decided")
    assert event["by"] == "jenozu"
    assert event["decision"] == "approved"
    assert event["payload_hash"] == a.payload_hash


def test_owner_id_required(rig: Rig) -> None:
    rig.delete()
    with pytest.raises(RuntimeFault):
        rig.queue.approve(rig.queue.list("pending")[0].id, "  ")
    assert rig.queue.list("approved") == []


def test_unwritable_ledger_means_no_approval(rig: Rig) -> None:
    rig.delete()
    a = rig.queue.list("pending")[0]
    path = rig.tmp / "ledger.jsonl"
    path.unlink()
    path.mkdir()
    with pytest.raises(RuntimeFault):
        rig.queue.approve(a.id, "jenozu")
    assert rig.queue.list("approved") == []  # decision was never recorded as authority


def test_approvals_survive_restart(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.delete()
    rig.queue.approve(rig.queue.list("pending")[0].id, "jenozu")
    rig.state.close()
    state = StateStore.open(tmp_path / "state.db")
    queue = ApprovalQueue(state, Ledger.open(tmp_path / "ledger.jsonl"), clock=rig.clock)
    assert [a.status for a in queue.list()] == ["approved"]
