"""Explicit revision-pinned skill loading. Text is never an authority source."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from jangus.execution.files import no_links

MAX_SKILL = 16 * 1024
MAX_CATALOG = 16 * 1024


class SkillFailure(ValueError):
    """A safe failure code for unreviewed or unavailable instructions."""


@dataclass(frozen=True)
class ReviewedSkill:
    name: str
    text: str
    sha256: str
    blob_sha: str
    required_tools: frozenset[str]


def _git_blob(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    # Git blob IDs are SHA-1 by definition; used only for catalog compatibility.
    return hashlib.sha1(header + content).hexdigest()  # nosec B324


class SkillRegistry:
    """Only the catalog's exact Git blob revisions may be supplied to workers."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        location = root / "skills" / "catalog.json"
        no_links(location)
        if not location.is_file():
            raise SkillFailure("catalog_missing")
        raw = location.read_bytes()
        if len(raw) > MAX_CATALOG:
            raise SkillFailure("catalog_too_large")
        try:
            data = json.loads(raw)
            if set(data) != {"version", "reviewed"} or data["version"] != 1:
                raise ValueError("catalog version")
            if not isinstance(data["reviewed"], dict):
                raise ValueError("reviewed")
            for name, item in data["reviewed"].items():
                if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", name):
                    raise ValueError("name")
                if set(item) != {"blob_sha", "required_tools"}:
                    raise ValueError("entry")
                if not re.fullmatch(r"[0-9a-f]{40}", item["blob_sha"]):
                    raise ValueError("digest")
                if (
                    not isinstance(item["required_tools"], list)
                    or any(not isinstance(tool, str) for tool in item["required_tools"])
                    or len(item["required_tools"]) != len(set(item["required_tools"]))
                ):
                    raise ValueError("tools")
        except (ValueError, TypeError, KeyError):
            raise SkillFailure("catalog_invalid") from None
        self.reviewed = data["reviewed"]

    def load(self, name: str, allowed_tools: set[str]) -> ReviewedSkill:
        if name not in self.reviewed:
            raise SkillFailure("skill_unreviewed")
        entry = self.reviewed[name]
        required = frozenset(entry["required_tools"])
        if not required <= allowed_tools:
            raise SkillFailure("skill_tool_denied")
        path = self.root / "skills" / name / "SKILL.md"
        no_links(path)
        if not path.resolve().is_relative_to(self.root) or not path.is_file():
            raise SkillFailure("skill_missing")
        content = path.read_bytes()
        if len(content) > MAX_SKILL or _git_blob(content) != entry["blob_sha"]:
            raise SkillFailure("skill_revision_changed")
        try:
            value = content.decode("utf-8")
        except UnicodeDecodeError:
            raise SkillFailure("skill_not_utf8") from None
        return ReviewedSkill(
            name=name,
            text=value,
            sha256=hashlib.sha256(content).hexdigest(),
            blob_sha=entry["blob_sha"],
            required_tools=required,
        )


def load_rules(root: Path, expected_sha256: str) -> tuple[str, str]:
    path = root / "rules.md"
    no_links(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise SkillFailure("rules_outside_root")
    try:
        content = path.read_bytes()
    except OSError:
        raise SkillFailure("rules_unavailable") from None
    sha = hashlib.sha256(content).hexdigest()
    if sha != expected_sha256:
        raise SkillFailure("rules_revision_changed")
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise SkillFailure("rules_invalid") from None
    return text, sha
