"""Bounded registered file tools in a private, exclusively controlled workspace.

Reject links/reparse points and recheck native handles. This is not a hostile-user
filesystem sandbox; the workspace must not be concurrently modified externally.
"""

import os
import stat
from pathlib import Path

from jangus.execution.contracts import (
    MAX_FILE_BYTES,
    TOOLS,
    Action,
    RuntimeFault,
    digest,
    relative_name,
)


def no_links(path: Path):
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeFault("unsafe_path")


class FileTools:
    def __init__(self, workspace: Path, allowed: set[str]):
        self.workspace = workspace.absolute()
        no_links(self.workspace)
        if not self.workspace.is_dir():
            raise RuntimeFault("workspace_missing")
        self.identity = self.workspace.stat()
        self.allowed = allowed

    def path(self, name: str) -> Path:
        try:
            relative_name(name)
        except ValueError:
            raise RuntimeFault("unsafe_path") from None
        no_links(self.workspace)
        if not os.path.samestat(self.identity, self.workspace.stat()):
            raise RuntimeFault("workspace_changed")
        target = self.workspace / name
        no_links(target)
        if not target.resolve().is_relative_to(self.workspace.resolve()):
            raise RuntimeFault("unsafe_path")
        if not target.parent.is_dir():
            raise RuntimeFault("parent_missing")
        return target

    def read(self, name: str) -> bytes:
        target = self.path(name)
        before = target.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RuntimeFault("unsafe_file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(target, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeFault("unsafe_file")
            if not os.path.samestat(before, info) or not os.path.samestat(
                info, self.path(name).stat()
            ):
                raise RuntimeFault("file_changed")
            data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise RuntimeFault("file_too_large")
        return data

    def write_new(self, name: str, data: bytes):
        if len(data) > MAX_FILE_BYTES:
            raise RuntimeFault("file_too_large")
        target = self.path(name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        # Exclusive creation is the only write mode. Partial outputs are preserved
        # as uncertain evidence, never deleted or silently replaced on retry.
        with os.fdopen(os.open(target, flags, 0o600), "wb") as stream:
            if not os.path.samestat(os.fstat(stream.fileno()), self.path(name).stat()):
                raise RuntimeFault("file_changed")
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def execute(self, action: Action, report: bytes = b"") -> dict:
        if action.tool not in TOOLS or action.tool not in self.allowed:
            raise RuntimeFault("tool_denied")
        if action.tool == "file.list":
            directory = self.path(action.path)
            result = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    if len(result) >= 100:
                        raise RuntimeFault("listing_too_large")
                    self.path(action.path + "/" + entry.name)
                    result.append(entry.name)
            return {"entries": sorted(result)}
        if action.tool == "file.copy":
            content = self.read(action.source)
            if digest(content) != action.expected_sha256:
                raise RuntimeFault("input_mismatch")
            self.write_new(action.path, content)
        elif action.tool == "file.report":
            if digest(report) != action.expected_sha256:
                raise RuntimeFault("report_mismatch")
            self.write_new(action.path, report)
        else:
            content = self.read(action.path)
            if digest(content) != action.expected_sha256:
                raise RuntimeFault("input_mismatch")
            if action.tool == "file.read":
                return {"sha256": digest(content), "bytes": len(content), "content": content}
        return {"sha256": digest(self.read(action.path))}
