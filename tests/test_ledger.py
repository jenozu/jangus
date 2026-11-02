import json
from pathlib import Path

import pytest

from jangus.errors import FaultCode, RuntimeFault
from jangus.kernel.ledger import GENESIS, Ledger


def fixed_clock() -> str:
    return "2026-01-01T00:00:00+00:00"


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger.open(tmp_path / "ledger.jsonl", clock=fixed_clock)


def fill(ledger: Ledger, n: int = 3) -> None:
    for i in range(n):
        ledger.append({"kind": "intent", "n": i})


def test_append_chains_entries(ledger: Ledger) -> None:
    first = ledger.append({"a": 1})
    second = ledger.append({"b": 2})
    assert first.prev == GENESIS
    assert second.prev == first.hash
    assert (first.seq, second.seq) == (1, 2)
    assert ledger.verify_chain() == 2
    assert [e.hash for e in ledger.entries()] == [first.hash, second.hash]


def test_reopen_continues_chain(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    a = Ledger.open(path, clock=fixed_clock)
    fill(a, 2)
    b = Ledger.open(path, clock=fixed_clock)
    entry = b.append({"x": 1})
    assert entry.seq == 3
    assert b.verify_chain() == 3


def test_tampered_event_fails_verify(ledger: Ledger, tmp_path: Path) -> None:
    fill(ledger)
    path = tmp_path / "ledger.jsonl"
    lines = path.read_bytes().splitlines()
    data = json.loads(lines[1])
    data["event"]["n"] = 99
    lines[1] = json.dumps(data).encode()
    path.write_bytes(b"\n".join(lines) + b"\n")
    with pytest.raises(RuntimeFault) as info:
        ledger.verify_chain()
    assert info.value.code is FaultCode.LEDGER_CORRUPT
    with pytest.raises(RuntimeFault):
        Ledger.open(path)


def test_removed_middle_entry_fails_verify(ledger: Ledger, tmp_path: Path) -> None:
    fill(ledger)
    path = tmp_path / "ledger.jsonl"
    lines = path.read_bytes().splitlines()
    path.write_bytes(b"\n".join([lines[0], lines[2]]) + b"\n")
    with pytest.raises(RuntimeFault) as info:
        ledger.verify_chain()
    assert info.value.code is FaultCode.LEDGER_CORRUPT


def test_torn_last_line_refused_on_open(ledger: Ledger, tmp_path: Path) -> None:
    fill(ledger)
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(path.read_bytes()[:-20])
    with pytest.raises(RuntimeFault) as info:
        Ledger.open(path)
    assert info.value.code is FaultCode.LEDGER_CORRUPT


def test_write_failure_raises_and_blocks_effects(tmp_path: Path) -> None:
    # A directory where the file should be makes every write fail (B06).
    path = tmp_path / "ledger.jsonl"
    path.mkdir()
    with pytest.raises(RuntimeFault):
        Ledger.open(path)  # reading a directory fails closed too
    good = Ledger.open(tmp_path / "ok.jsonl", clock=fixed_clock)
    fill(good, 1)
    (tmp_path / "ok.jsonl").unlink()
    (tmp_path / "ok.jsonl").mkdir()
    assert good.effects_allowed
    with pytest.raises(RuntimeFault) as first:
        good.append({"after": "failure"})
    assert first.value.code is FaultCode.LEDGER_UNAVAILABLE
    assert not good.effects_allowed
    (tmp_path / "ok.jsonl").rmdir()
    with pytest.raises(RuntimeFault) as second:
        good.append({"retry": True})  # stays blocked even if the disk recovers
    assert "blocked" in second.value.message


def test_second_writer_detected_and_blocks(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    a = Ledger.open(path, clock=fixed_clock)
    b = Ledger.open(path, clock=fixed_clock)
    a.append({"from": "a"})
    with pytest.raises(RuntimeFault) as info:
        b.append({"from": "b"})
    assert info.value.code is FaultCode.LEDGER_UNAVAILABLE
    assert not b.effects_allowed
    assert a.verify_chain() == 1


def test_non_json_event_rejected_without_blocking(ledger: Ledger) -> None:
    with pytest.raises(RuntimeFault) as info:
        ledger.append({"bad": object()})
    assert info.value.code is FaultCode.LEDGER_EVENT_INVALID
    with pytest.raises(RuntimeFault):
        ledger.append({"bad": float("nan")})
    assert ledger.effects_allowed
    assert ledger.append({"ok": True}).seq == 1
