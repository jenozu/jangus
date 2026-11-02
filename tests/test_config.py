from pathlib import Path
from typing import Any

import pytest

from jangus import config as config_module
from jangus.config import load_config, parse_config
from jangus.errors import FaultCode, RuntimeFault


@pytest.fixture
def valid(tmp_path: Path) -> dict[str, Any]:
    return {
        "data_dir": str(tmp_path / "data"),
        "workspace_dir": str(tmp_path / "work"),
        "model": {"url": "http://127.0.0.1:11434", "name": "test-model"},
    }


def fault_code(data: dict[str, Any]) -> FaultCode:
    with pytest.raises(RuntimeFault) as info:
        parse_config(data)
    return info.value.code


def test_valid_config_loads(valid: dict[str, Any]) -> None:
    cfg = parse_config(valid)
    assert cfg.model.name == "test-model"
    assert cfg.data_dir.is_absolute()


def test_ipv6_loopback_accepted(valid: dict[str, Any]) -> None:
    valid["model"]["url"] = "http://[::1]:11434"
    assert parse_config(valid).model.url == "http://[::1]:11434"


def test_extra_top_level_field_rejected(valid: dict[str, Any]) -> None:
    valid["surprise"] = 1
    assert fault_code(valid) is FaultCode.CONFIG_INVALID


def test_extra_nested_field_rejected(valid: dict[str, Any]) -> None:
    valid["model"]["api_key"] = "x"
    assert fault_code(valid) is FaultCode.CONFIG_INVALID


def test_wrong_type_not_coerced(valid: dict[str, Any]) -> None:
    valid["model"]["name"] = 5
    assert fault_code(valid) is FaultCode.CONFIG_INVALID


def test_missing_field_rejected(valid: dict[str, Any]) -> None:
    del valid["model"]
    assert fault_code(valid) is FaultCode.CONFIG_INVALID


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://10.0.0.5:11434",
        "http://example.com:11434",
        "http://127.0.0.2:11434",
        "https://127.0.0.1:11434",
        "http://127.0.0.1",
        "http://user:pw@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
        "http://0.0.0.0:11434",
    ],
)
def test_non_loopback_endpoint_rejected(valid: dict[str, Any], url: str) -> None:
    valid["model"]["url"] = url
    assert fault_code(valid) is FaultCode.ENDPOINT_NOT_LOOPBACK


def test_relative_path_rejected(valid: dict[str, Any]) -> None:
    valid["data_dir"] = "relative/data"
    assert fault_code(valid) is FaultCode.PATH_INVALID


def test_path_inside_checkout_rejected(valid: dict[str, Any], tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    valid["workspace_dir"] = str(checkout / "ws")
    original = config_module.checkout_roots
    config_module.checkout_roots = lambda: (checkout,)  # type: ignore[assignment]
    try:
        assert fault_code(valid) is FaultCode.PATH_INVALID
    finally:
        config_module.checkout_roots = original  # type: ignore[assignment]


def test_real_checkout_is_excluded(valid: dict[str, Any]) -> None:
    valid["data_dir"] = str(config_module.checkout_roots()[0] / "data")
    assert fault_code(valid) is FaultCode.PATH_INVALID


def test_overlapping_dirs_rejected(valid: dict[str, Any], tmp_path: Path) -> None:
    valid["workspace_dir"] = str(tmp_path / "data" / "inner")
    assert fault_code(valid) is FaultCode.PATH_INVALID


def test_error_does_not_echo_values(valid: dict[str, Any]) -> None:
    valid["model"]["name"] = 12345678
    with pytest.raises(RuntimeFault) as info:
        parse_config(valid)
    assert "12345678" not in str(info.value)


def test_load_config_from_toml(valid: dict[str, Any], tmp_path: Path) -> None:
    def q(p: str) -> str:
        return p.replace("\\", "/")

    text = (
        f'data_dir = "{q(valid["data_dir"])}"\n'
        f'workspace_dir = "{q(valid["workspace_dir"])}"\n'
        '[model]\nurl = "http://127.0.0.1:11434"\nname = "m"\n'
    )
    path = tmp_path / "jangus.toml"
    path.write_text(text, encoding="utf-8")
    assert load_config(path).model.name == "m"


def test_load_config_missing_and_bad_toml(tmp_path: Path) -> None:
    with pytest.raises(RuntimeFault) as missing:
        load_config(tmp_path / "nope.toml")
    assert missing.value.code is FaultCode.CONFIG_UNREADABLE
    bad = tmp_path / "bad.toml"
    bad.write_text("not = = toml", encoding="utf-8")
    with pytest.raises(RuntimeFault) as invalid:
        load_config(bad)
    assert invalid.value.code is FaultCode.CONFIG_INVALID
