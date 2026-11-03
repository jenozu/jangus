from pathlib import Path

import pytest

from jangus.cli import main


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    path = tmp_path / "jangus.toml"
    path.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'workspace_dir = "{(tmp_path / "work").as_posix()}"\n'
        '[model]\nurl = "http://127.0.0.1:11434"\nname = "m"\n',
        encoding="utf-8",
    )
    return path


def test_tick_with_no_specs_is_quiet_and_writes_yesterdays_report(cfg: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--config", str(cfg), "tick"]) == 0
    reports = list((cfg.parent / "data" / "reports").glob("daily-*.txt"))
    assert len(reports) == 1 and "Daily report" in reports[0].read_text(encoding="utf-8")
    first = reports[0].read_text(encoding="utf-8")
    assert main(["--config", str(cfg), "tick"]) == 0  # a second tick never rewrites it
    assert reports[0].read_text(encoding="utf-8") == first


def test_overlapping_tick_exits_quietly(cfg: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from jangus.kernel.lock import RunLock

    other = RunLock(cfg.parent / "data" / "tick.lock")
    assert other.acquire()
    assert main(["--config", str(cfg), "tick"]) == 0
    assert "another tick is running" in capsys.readouterr().out
    other.release()


def test_status_and_report_commands(cfg: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--config", str(cfg), "status"]) == 0
    out = capsys.readouterr().out
    assert "approved specs: none" in out and "alerts: 0" in out
    assert main(["--config", str(cfg), "report"]) == 0
    assert "Daily report" in capsys.readouterr().out
    assert main(["--config", str(cfg), "report", "--day", "1999-01-01"]) == 0


def test_unknown_command_still_fails_closed(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main([]) == 2
