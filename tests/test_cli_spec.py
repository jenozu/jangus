from pathlib import Path

import pytest

from jangus.cli import main
from tests.test_agent_specs import GOOD


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    data = tmp_path / "data"
    cfg = tmp_path / "jangus.toml"
    cfg.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'workspace_dir = "{(tmp_path / "work").as_posix()}"\n'
        '[model]\nurl = "http://127.0.0.1:11434"\nname = "m"\n',
        encoding="utf-8",
    )
    spec = tmp_path / "spec.yaml"
    spec.write_text(GOOD, encoding="utf-8", newline="\n")
    return {"cfg": cfg, "spec": spec, "data": data}


def run(env: dict[str, Path], *args: str, answer: str = "yes") -> int:
    return main(["--config", str(env["cfg"]), "spec", *args], confirm=lambda _: answer)


def test_approve_requires_typed_yes_then_shows_approved(env, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(env, "approve", str(env["spec"]), answer="no") == 1
    assert run(env, "show", str(env["spec"])) == 0
    assert "NOT approved" in capsys.readouterr().out

    assert run(env, "approve", str(env["spec"]), "--note", "ok") == 0
    capsys.readouterr()
    assert run(env, "show", str(env["spec"])) == 0
    out = capsys.readouterr().out
    assert "state:      approved" in out
    assert run(env, "list") == 0
    assert "backup-maintainer" in capsys.readouterr().out
    assert (env["data"] / "ledger.jsonl").exists()


def test_edit_after_approval_shows_not_approved(env, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(env, "approve", str(env["spec"]), "--yes") == 0
    env["spec"].write_text(
        GOOD.replace("max_tier: T2", "max_tier: T3"), encoding="utf-8", newline="\n"
    )
    capsys.readouterr()
    run(env, "show", str(env["spec"]))
    assert "NOT approved" in capsys.readouterr().out


def test_revoke(env) -> None:  # type: ignore[no-untyped-def]
    run(env, "approve", str(env["spec"]), "--yes")
    assert run(env, "revoke", "backup-maintainer") == 0
    assert run(env, "revoke", "backup-maintainer") == 1


def test_invalid_spec_and_missing_config_fail_closed(env, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\n", encoding="utf-8")
    assert run(env, "approve", str(bad), "--yes") == 1
    monkeypatch.delenv("JANGUS_CONFIG", raising=False)
    assert main(["spec", "list"]) == 1
