"""Behavioral checks for configuration boundaries before runtime execution exists."""

from pathlib import Path

import pytest
import yaml

from jangus.config import MAX_CONFIG_BYTES, ConfigurationError, load_config


@pytest.fixture
def valid_data(tmp_path):
    return {
        "schema_version": 1,
        "paths": {
            "data_dir": str(tmp_path / "data"),
            "workspace_dir": str(tmp_path / "work"),
        },
    }


def write_config(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_valid_configuration_is_read_only(tmp_path, valid_data):
    path = write_config(tmp_path, valid_data)
    config = load_config(path)
    assert config.features.codex_enabled is False
    assert config.features.astra_enabled is False
    assert config.permissions.allowed_tools == []
    assert config.model.max_concurrent_inference == 1
    assert not Path(config.paths.data_dir).exists()
    assert not Path(config.paths.workspace_dir).exists()


@pytest.mark.parametrize(
    "change",
    [
        {"unexpected": "canary-secret"},
        {"schema_version": True},
        {"schema_version": 2},
        {"features": {"astra_enabled": True}},
        {"features": {"codex_enabled": True}},
        {"features": {"remote_enabled": True}},
        {"features": {"astra_enabled": "false"}},
        {"permissions": {"allowed_tools": ["shell"]}},
        {"permissions": {"allow_network": True}},
        {"logging": {"redact_secrets": False}},
        {"logging": {"retention_days": 0}},
        {"limits": {"max_actions": 0}},
        {"limits": {"max_actions": "20"}},
        {"limits": {"max_attempts_per_task": True}},
        {"limits": {"context_tokens": 1024, "response_tokens": 1024}},
        {"model": {"max_concurrent_inference": 2}},
        {"model": {"provider": "cloud"}},
        {"model": {"endpoint": "http://example.com:11434"}},
        {"model": {"endpoint": "http://user:canary-secret@localhost:11434"}},
        {"model": {"endpoint": "http://localhost:11434/?key=canary-secret"}},
        {"model": {"endpoint": "http://localhost:invalid"}},
        {"model": {"api_key": "canary-secret"}},
    ],
)
def test_invalid_settings_fail_without_echoing_values(tmp_path, valid_data, change):
    valid_data.update(change)
    with pytest.raises(ConfigurationError) as error:
        load_config(write_config(tmp_path, valid_data))
    assert "canary-secret" not in str(error.value)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "schema_version: 1\nschema_version: 1",
        "paths:\n  data_dir: /a\n  data_dir: /b",
        "paths: &paths {}",
        "paths: *missing",
        "!!python/object/apply:os.system ['echo never']",
        "paths: [",
        "1: value",
        "paths: {}\n---\npaths: {}",
    ],
)
def test_ambiguous_or_unsafe_yaml_is_rejected(tmp_path, text):
    path = tmp_path / "invalid.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_oversized_and_non_utf8_files_are_rejected(tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_bytes(b"x" * (MAX_CONFIG_BYTES + 1))
    with pytest.raises(ConfigurationError, match="64 KiB"):
        load_config(path)
    path.write_bytes(b"\xff")
    with pytest.raises(ConfigurationError, match="UTF-8"):
        load_config(path)


def test_missing_file_is_reported_without_a_traceback(tmp_path):
    with pytest.raises(ConfigurationError, match="cannot be read"):
        load_config(tmp_path / "missing.yaml")


@pytest.mark.parametrize("relationship", ["inside", "ancestor", "same", "relative"])
def test_repository_overlap_and_relative_paths_are_rejected(tmp_path, valid_data, relationship):
    repo = tmp_path / "checkout"
    repo.mkdir()
    candidates = {
        "inside": str(repo / "runtime"),
        "ancestor": str(tmp_path),
        "same": str(repo),
        "relative": "runtime",
    }
    valid_data["paths"]["data_dir"] = candidates[relationship]
    with pytest.raises(ConfigurationError):
        load_config(write_config(tmp_path, valid_data), repository_root=repo)


def test_symlink_into_checkout_is_rejected(tmp_path, valid_data):
    repo = tmp_path / "checkout"
    repo.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(repo, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation requires host permissions on this platform.")
    valid_data["paths"]["data_dir"] = str(link / "data")
    with pytest.raises(ConfigurationError, match="repository"):
        load_config(write_config(tmp_path, valid_data), repository_root=repo)


@pytest.mark.parametrize("nested", [False, True])
def test_data_and_workspace_overlap_is_rejected(tmp_path, valid_data, nested):
    path = valid_data["paths"]["data_dir"]
    valid_data["paths"]["workspace_dir"] = str(Path(path) / "work") if nested else path
    with pytest.raises(ConfigurationError, match="must not overlap"):
        load_config(write_config(tmp_path, valid_data))


def test_existing_file_is_not_a_storage_directory(tmp_path, valid_data):
    file = tmp_path / "file"
    file.write_text("synthetic", encoding="utf-8")
    valid_data["paths"]["data_dir"] = str(file)
    with pytest.raises(ConfigurationError, match="directories"):
        load_config(write_config(tmp_path, valid_data))


def test_logging_honors_level_and_masks_credentials(capsys):
    import logging

    from jangus.logs import configure, get

    configure("WARNING")
    get("t").info("hidden-info")
    get("t").warning("password=hunter2 Bearer abcdefghijklmnop sk-abcdefghijklmnopqrst ok-text")
    err = capsys.readouterr().err
    assert "hidden-info" not in err
    assert "hunter2" not in err and "abcdefghijklmnop" not in err
    assert "ok-text" in err and "[redacted]" in err
    configure("ERROR", debug=True)
    assert logging.getLogger("jangus").level == logging.DEBUG
    get("t").debug("visible-debug")
    assert "visible-debug" in capsys.readouterr().err
    assert (
        len([h for h in logging.getLogger("jangus").handlers if getattr(h, "_jangus", False)]) == 1
    )
