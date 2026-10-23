"""Strict owner-supplied expectations and immutable execution plans."""

import hashlib
import json
import re
from pathlib import PureWindowsPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from jangus.config import StrictModel

TOOLS = frozenset({"file.list", "file.read", "file.hash", "file.copy", "file.report"})
MAX_FILE_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
POLICY_VERSION = "phase4-v1"


class RuntimeFault(Exception):
    """A stable, safe diagnostic code; never include arbitrary exception text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_name(value: str) -> str:
    parts = value.split("/")
    if len(value) > 240 or any(
        p in {"", ".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", p)
        or p.endswith(".")
        or PureWindowsPath(p).is_reserved()
        for p in parts
    ):
        raise ValueError("Use portable relative paths without traversal or reserved names")
    return value


class FileExpectation(StrictModel):
    source: str
    destination: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _paths = field_validator("source", "destination")(relative_name)


class Manifest(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    files: list[FileExpectation] = Field(min_length=1, max_length=40)
    report: str

    _report = field_validator("report")(relative_name)

    @model_validator(mode="after")
    def no_collisions(self):
        outputs = [f.destination.casefold() for f in self.files] + [self.report.casefold()]
        inputs = {f.source.casefold() for f in self.files}
        if len(set(outputs)) != len(outputs) or set(outputs) & inputs:
            raise ValueError("Outputs must be unique and separate from inputs")
        names = set(outputs) | inputs
        if any(a != b and b.startswith(a + "/") for a in names for b in names):
            raise ValueError("A file cannot also be a parent directory")
        return self

    def report_bytes(self) -> bytes:
        return encoded({"schema_version": 1, "files": [f.model_dump() for f in self.files]})


class Action(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    tool: Literal["file.list", "file.read", "file.hash", "file.copy", "file.report"]
    path: str
    source: str | None = None
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    dependencies: list[str] = Field(default_factory=list, max_length=100)

    _path = field_validator("path")(relative_name)

    @model_validator(mode="after")
    def correct_arguments(self):
        if self.source is not None:
            relative_name(self.source)
        if (self.tool == "file.copy") != (self.source is not None):
            raise ValueError("Only copy requires source")
        if self.tool != "file.list" and self.expected_sha256 is None:
            raise ValueError("Expected digest is required")
        return self


class Plan(StrictModel):
    revision: int = Field(default=1, ge=1, le=1)
    actions: list[Action] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_dag(self):
        by_id = {a.id: a for a in self.actions}
        if len(by_id) != len(self.actions):
            raise ValueError("Duplicate action IDs")
        visited, active = set(), set()

        def visit(key):
            if key in active or key not in by_id:
                raise ValueError("Cyclic or missing dependency")
            if key in visited:
                return
            active.add(key)
            for dep in by_id[key].dependencies:
                visit(dep)
            active.remove(key)
            visited.add(key)

        for key in by_id:
            visit(key)
        return self


def compile_plan(manifest: Manifest) -> Plan:
    actions = [
        Action(
            id=f"copy_{i}",
            tool="file.copy",
            path=f.destination,
            source=f.source,
            expected_sha256=f.sha256,
        )
        for i, f in enumerate(manifest.files, 1)
    ]
    actions.append(
        Action(
            id="report",
            tool="file.report",
            path=manifest.report,
            expected_sha256=digest(manifest.report_bytes()),
            dependencies=[a.id for a in actions],
        )
    )
    return Plan(actions=actions)
