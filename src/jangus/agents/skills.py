"""Explicit revision-pinned skill loading. Text is never an authority source."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from jangus.execution.files import no_links

MAX_SKILL = 16 * 1024
MAX_CATALOG = 16 * 1024
# Reviewed anchor for skills/catalog.json, like RULES_HASH for rules.md. Changing
# the catalog requires changing this value in the same reviewed change.
CATALOG_SHA256 = "78ea92f9cc2410aee403f001dcc91d50195c91d69e722f84185f3646e19e589b"


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
    return hashlib.sha1(header + content).hexdigest()


class SkillRegistry:
    """Only the catalog's exact reviewed revisions (Git blob and SHA-256) reach workers.

    The catalog itself is pinned by SHA-256 in code, so an edited catalog cannot
    re-approve an edited skill.
    """

    def __init__(self, root: Path, expected_catalog_sha256: str = CATALOG_SHA256):
        self.root = root.resolve()
        location = root / "skills" / "catalog.json"
        no_links(location)
        if not location.is_file():
            raise SkillFailure("catalog_missing")
        raw = location.read_bytes()
        if len(raw) > MAX_CATALOG:
            raise SkillFailure("catalog_too_large")
        if hashlib.sha256(raw).hexdigest() != expected_catalog_sha256:
            raise SkillFailure("catalog_revision_changed")
        try:
            data = json.loads(raw)
            if set(data) != {"version", "reviewed"} or data["version"] != 1:
                raise ValueError("catalog version")
            if not isinstance(data["reviewed"], dict):
                raise ValueError("reviewed")
            for name, item in data["reviewed"].items():
                if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", name):
                    raise ValueError("name")
                if set(item) != {"blob_sha", "sha256", "required_tools"}:
                    raise ValueError("entry")
                if not re.fullmatch(r"[0-9a-f]{40}", item["blob_sha"]):
                    raise ValueError("digest")
                if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
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
        if (
            len(content) > MAX_SKILL
            or _git_blob(content) != entry["blob_sha"]
            or hashlib.sha256(content).hexdigest() != entry["sha256"]
        ):
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
