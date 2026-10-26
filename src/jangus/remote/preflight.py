"""Read-only Ubuntu deployment checks. No service changes, secrets or network access."""

import configparser
import stat
import sys
from pathlib import Path

from jangus.execution.contracts import RuntimeFault
from jangus.execution.files import no_links
from jangus.remote.protocol import RemoteFault

EXPECTED = {
    "Type": "simple",
    "User": "jangus-worker",
    "Group": "jangus-worker",
    "WorkingDirectory": "/var/lib/jangus",
    "EnvironmentFile": "/etc/jangus/worker.env",
    "Restart": "on-failure",
    "RestartSec": "3",
    "ExecStart": (
        "/opt/jangus/.venv/bin/python -m jangus.remote.service "
        "--state-dir /var/lib/jangus/remote --port 8765"
    ),
    "UMask": "0077",
    "NoNewPrivileges": "true",
    "PrivateTmp": "true",
    "ProtectHome": "true",
    "ProtectSystem": "strict",
    "ReadWritePaths": "/var/lib/jangus",
    "RestrictAddressFamilies": "AF_INET AF_UNIX",
    "IPAddressDeny": "any",
    "IPAddressAllow": "localhost",
}


def verify_unit(contents: bytes) -> None:
    """Reject missing/overridden protection directives or unknown service entries."""
    if len(contents) > 16 * 1024:
        raise RemoteFault("unit_unsafe")
    try:
        text = contents.decode("utf-8")
        config = configparser.ConfigParser(interpolation=None, strict=True)
        config.optionxform = str
        config.read_string(text)
        if "Service" not in config or set(config["Service"]) != set(EXPECTED):
            raise ValueError("directive mismatch")
        if any(config["Service"][key] != value for key, value in EXPECTED.items()):
            raise ValueError("policy differs")
        if not {"Unit", "Service", "Install"} <= set(config.sections()):
            raise ValueError("sections missing")
    except (ValueError, UnicodeError, configparser.Error):
        raise RemoteFault("unit_unsafe") from None


def verify_private_path(path: Path, owner: int, *, directory: bool) -> None:
    """Stat-only permission and ownership checks; do not open private files."""
    try:
        no_links(path)
        info = path.stat()
        if (
            info.st_uid != owner
            or info.st_mode & 0o077
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
        ):
            raise RemoteFault("permissions_unsafe")
    except FileNotFoundError:
        raise RemoteFault("path_missing") from None
    except (OSError, ValueError, RuntimeFault):
        raise RemoteFault("path_unsafe") from None


def verify_host(unit_path: Path, state_root: Path, env_path: Path) -> dict:
    """Must run on the actual Ubuntu host with permission to stat the paths."""
    if sys.platform != "linux":
        raise RemoteFault("linux_required")
    import pwd

    try:
        account = pwd.getpwnam("jangus-worker")
    except KeyError:
        raise RemoteFault("service_account_missing") from None
    if account.pw_uid == 0 or account.pw_gid == 0:
        raise RemoteFault("service_account_privileged")
    try:
        no_links(unit_path)
        verify_unit(unit_path.read_bytes())
    except (OSError, RuntimeFault):
        raise RemoteFault("unit_unavailable") from None
    verify_private_path(state_root, account.pw_uid, directory=True)
    verify_private_path(env_path, 0, directory=False)
    # No credential contents, hostnames, personal paths or tokens are printed.
    return {"service": "jangus-worker", "checked": True, "deployment": False}
