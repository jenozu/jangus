import sqlite3
from pathlib import Path

import pytest

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.state import SCHEMA_VERSION, ActionRecord, StateStore


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    s = StateStore.open(tmp_path / "state.db")
    s.set_budget("calls", 3)
    return s


def intent(
    s: StateStore, run_id: int, key: str = "k1", payload: str = "h1", cost: int = 1
) -> tuple[ActionRecord, bool]:
    return s.record_intent(
        run_id=run_id,
        idempotency_key=key,
        tool="t",
        payload_hash=payload,
        budget="calls",
        cost=cost,
    )


def test_wal_and_full_sync(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    StateStore.open(path).close()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


def test_duplicate_run_key_returns_same_record(store: StateStore) -> None:
    a = store.create_run("job-1", "files")
    b = store.create_run("job-1", "files")
    assert a == b


def test_run_key_reused_for_other_kind_conflicts(store: StateStore) -> None:
    store.create_run("job-1", "files")
    with pytest.raises(RuntimeFault) as info:
        store.create_run("job-1", "shop")
    assert info.value.code is FaultCode.IDEMPOTENCY_CONFLICT


def test_duplicate_action_key_returns_same_record_without_second_charge(store: StateStore) -> None:
    run = store.create_run("job-1", "files")
    first, created1 = intent(store, run.id)
    second, created2 = intent(store, run.id)
    assert (created1, created2) == (True, False)
    assert first == second
    assert store.get_budget("calls").spent == 1


def test_same_key_different_payload_conflicts(store: StateStore) -> None:
    run = store.create_run("job-1", "files")
    intent(store, run.id)
    with pytest.raises(RuntimeFault) as info:
        intent(store, run.id, payload="other")
    assert info.value.code is FaultCode.IDEMPOTENCY_CONFLICT
    assert store.get_budget("calls").spent == 1


def test_over_budget_inserts_nothing(store: StateStore) -> None:
    run = store.create_run("job-1", "files")
    with pytest.raises(RuntimeFault) as info:
        intent(store, run.id, cost=4)
    assert info.value.code is FaultCode.BUDGET_EXCEEDED
    assert store.get_budget("calls").spent == 0
    assert store.get_action("k1") is None


def test_unknown_budget_fails_closed(store: StateStore) -> None:
    run = store.create_run("job-1", "files")
    with pytest.raises(RuntimeFault):
        store.record_intent(
            run_id=run.id, idempotency_key="x", tool="t", payload_hash="h", budget="nope", cost=1
        )
    assert store.get_action("x") is None


def test_result_recorded(store: StateStore) -> None:
    run = store.create_run("job-1", "files")
    intent(store, run.id)
    rec = store.record_result("k1", "done", "abc")
    assert (rec.status, rec.result_hash) == ("done", "abc")
    with pytest.raises(RuntimeFault):
        store.record_result("missing", "done", None)


def test_reopened_db_keeps_budgets_and_actions(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    s = StateStore.open(path)
    s.set_budget("calls", 3)
    run = s.create_run("job-1", "files")
    intent(s, run.id)
    s.close()
    again = StateStore.open(path)
    assert again.get_budget("calls").spent == 1
    assert again.get_action("k1") is not None
    assert again.create_run("job-1", "files").id == run.id
    # The limit still binds after restart: 1 spent + 3 > 3.
    with pytest.raises(RuntimeFault):
        intent(again, run.id, key="k2", cost=3)


def test_unreadable_db_cannot_open(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    path.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(RuntimeFault) as info:
        StateStore.open(path)
    assert info.value.code is FaultCode.STATE_UNAVAILABLE


def test_failed_write_blocks_all_further_writes(store: StateStore) -> None:
    run = store.create_run("job-1", "files")
    store.close()  # simulates the database becoming unusable under us
    with pytest.raises(RuntimeFault) as first:
        intent(store, run.id)
    assert first.value.code is FaultCode.STATE_UNAVAILABLE
    with pytest.raises(RuntimeFault) as second:
        store.create_run("job-2", "files")
    assert "blocked" in second.value.message


def test_schema_version_mismatch_refused(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    StateStore.open(path).close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION + 1),))
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeFault) as info:
        StateStore.open(path)
    assert info.value.code is FaultCode.STATE_SCHEMA_MISMATCH
