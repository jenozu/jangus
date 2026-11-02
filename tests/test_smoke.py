import jangus
from jangus.cli import main


def test_version_string() -> None:
    assert jangus.__version__ == "0.0.0"


def test_cli_version(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "jangus 0.0.0"


def test_cli_without_commands_fails_closed() -> None:
    assert main([]) == 2
