"""File tool pack (B14).

Tools never run on their own: the kernel executor calls them after policy, budget and
approval checks. Rules built into every tool:

- Paths must be absolute with no `..` segment (schema level). They must stay inside the
  roots the owner approved for this agent, with no symlink or junction on the way.
  Anything outside the roots, or reached through a link, is raised to tier T3, so an agent
  limited to T2 has the call rejected and only an owner approval could ever run it.
- Nothing is overwritten or deleted silently: `copy_new` and `copy_new_tree` create only
  files that do not exist; replacing or deleting is `file.overwrite` / `file.delete` (T3).
- Every write is verified by SHA-256 of source and destination, and reconciled after a crash
  by inspecting the destination. A destination that exists with different bytes is reported
  as unverified (unresolved), never repaired by overwriting.
"""

import hashlib
import os
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import Field, field_validator

from jangus.config import StrictModel
from jangus.kernel.registry import Reconciliation, Tier, ToolSpec

MAX_PATH_CHARS = 1024
MAX_READ_BYTES = 1_048_576
MAX_REPORT_CHARS = 100_000
CHUNK = 1024 * 1024
_REPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class PathRefused(Exception):  # noqa: N818 - a refusal, not an error
    """The path is outside the approved roots or goes through a link."""


def _lexical_ok(value: str) -> str:
    if not value or len(value) > MAX_PATH_CHARS or "\x00" in value:
        raise ValueError("path is empty, too long or contains NUL")
    windows, posix = PureWindowsPath(value), PurePosixPath(value)
    if not (windows.is_absolute() or posix.is_absolute()):
        raise ValueError("path must be absolute")
    if ".." in windows.parts or ".." in posix.parts:
        raise ValueError("path must not contain '..'")
    return value


class ListArgs(StrictModel):
    root: str
    recursive: bool = False
    max_entries: int = Field(default=1000, ge=1, le=10_000)

    _v = field_validator("root")(lambda cls, v: _lexical_ok(v))


class ReadArgs(StrictModel):
    path: str
    max_bytes: int = Field(default=65_536, ge=1, le=MAX_READ_BYTES)

    _v = field_validator("path")(lambda cls, v: _lexical_ok(v))


class HashArgs(StrictModel):
    path: str

    _v = field_validator("path")(lambda cls, v: _lexical_ok(v))


class CopyArgs(StrictModel):
    src: str
    dst: str

    _v = field_validator("src", "dst")(lambda cls, v: _lexical_ok(v))


class CopyTreeArgs(StrictModel):
    src_root: str
    dst_root: str
    max_files: int = Field(default=500, ge=1, le=10_000)

    _v = field_validator("src_root", "dst_root")(lambda cls, v: _lexical_ok(v))


class DeleteArgs(StrictModel):
    path: str

    _v = field_validator("path")(lambda cls, v: _lexical_ok(v))


class ReportArgs(StrictModel):
    name: str
    content: str = Field(max_length=MAX_REPORT_CHARS)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _REPORT_NAME.match(value):
            raise ValueError("report name must be a plain file name")
        return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or os.path.isjunction(path)


class FileWorld:
    """The folders one agent may touch. Roots come from the owner at spec approval time."""

    def __init__(self, roots: Sequence[Path], reports_dir: Path) -> None:
        self.roots = tuple(Path(r).resolve() for r in roots)
        self.reports_dir = Path(reports_dir).resolve()

    def _inside(self, path: Path, roots: Sequence[Path]) -> Path:
        """Resolve `path` under one of `roots`, refusing links on the way."""
        candidate = Path(path)
        for root in roots:
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                try:
                    relative = candidate.resolve().relative_to(root)
                except (ValueError, OSError):
                    continue
            walk = root
            for part in relative.parts:
                walk = walk / part
                if walk.exists() and _is_link(walk):
                    raise PathRefused("path goes through a link")
            if candidate.exists() and candidate.resolve() != (root / relative).resolve():
                raise PathRefused("path resolves somewhere else")
            return root / relative
        raise PathRefused("path is outside the approved folders")

    def resolve(self, raw: str) -> Path:
        return self._inside(Path(raw), self.roots)

    def resolve_report(self, name: str) -> Path:
        return self._inside(self.reports_dir / name, [self.reports_dir])

    def escapes(self, *raws: str) -> bool:
        try:
            for raw in raws:
                self.resolve(raw)
        except (PathRefused, OSError):
            return True
        return False


def _tier_if_escapes(world: FileWorld, *fields: str) -> Callable[[Any], Tier | None]:
    def elevation(args: Any) -> Tier | None:
        return Tier.T3 if world.escapes(*(getattr(args, f) for f in fields)) else None

    return elevation


# -- copying ----------------------------------------------------------------


def _copy_exclusive(src: Path, dst: Path) -> bool:
    """Copy src to dst only if dst does not exist. Returns False if it already existed."""
    if dst.exists() or dst.is_symlink():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(f".jangus-part-{hashlib.sha256(str(dst).encode()).hexdigest()[:12]}")
    with src.open("rb") as source, part.open("wb") as target:
        while chunk := source.read(CHUNK):
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())
    if dst.exists() or dst.is_symlink():
        part.unlink()
        return False
    os.replace(part, dst)
    return True


def _same_bytes(a: Path, b: Path) -> bool:
    return a.is_file() and b.is_file() and sha256_file(a) == sha256_file(b)


def _tree_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        here = Path(current)
        dirs[:] = sorted(d for d in dirs if not _is_link(here / d))
        found += [here / f for f in sorted(files) if not _is_link(here / f)]
    return found


def build_file_tools(world: FileWorld) -> list[ToolSpec]:
    def list_files(args: ListArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        root = world.resolve(args.root)
        paths = _tree_files(root) if args.recursive else sorted(p for p in root.iterdir())
        entries = []
        for p in paths[: args.max_entries]:
            if _is_link(p):
                continue
            entries.append(
                {
                    "path": str(p),
                    "dir": p.is_dir(),
                    "size": p.stat().st_size if p.is_file() else None,
                }
            )
        return {"entries": entries, "total": len(paths), "truncated": len(paths) > args.max_entries}

    def read_file(args: ReadArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        path = world.resolve(args.path)
        with path.open("rb") as handle:
            data = handle.read(args.max_bytes + 1)
        return {
            "text": data[: args.max_bytes].decode("utf-8", "replace"),
            "truncated": len(data) > args.max_bytes,
        }

    def hash_file(args: HashArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        return {"sha256": sha256_file(world.resolve(args.path))}

    # copy_new -----------------------------------------------------------------
    def copy_new(args: CopyArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        src, dst = world.resolve(args.src), world.resolve(args.dst)
        created = _copy_exclusive(src, dst)
        return {"dst": str(dst), "created": created}

    def verify_copy(args: CopyArgs, result: dict[str, Any]) -> bool:
        return _same_bytes(world.resolve(args.src), world.resolve(args.dst))

    def reconcile_copy(args: CopyArgs) -> Reconciliation:
        src, dst = world.resolve(args.src), world.resolve(args.dst)
        if not dst.exists():
            return Reconciliation("absent")
        if _same_bytes(src, dst):
            return Reconciliation("applied", {"dst": str(dst), "created": False})
        return Reconciliation("unknown")  # something else is there: never overwrite it

    # copy_new_tree --------------------------------------------------------------
    def plan_tree(args: CopyTreeArgs) -> tuple[list[tuple[Path, Path]], int]:
        src_root, dst_root = world.resolve(args.src_root), world.resolve(args.dst_root)
        pairs = [(f, dst_root / f.relative_to(src_root)) for f in _tree_files(src_root)]
        return pairs, len(pairs)

    def copy_tree(args: CopyTreeArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        pairs, total = plan_tree(args)
        copied: list[str] = []
        same = 0
        conflicts: list[str] = []
        for src, dst in pairs:
            if dst.exists():
                if _same_bytes(src, dst):
                    same += 1
                else:
                    conflicts.append(str(dst))  # reported, never overwritten
                continue
            if len(copied) >= args.max_files:
                continue
            _copy_exclusive(src, dst)
            copied.append(str(dst))
        return {
            "copied": copied,
            "already_present": same,
            "conflicts": conflicts,
            "source_files": total,
            "remaining": sum(1 for _, d in pairs if not d.exists()),
        }

    def verify_tree(args: CopyTreeArgs, result: dict[str, Any]) -> bool:
        if result["conflicts"]:
            return False
        pairs, _ = plan_tree(args)
        by_dst = {str(d): s for s, d in pairs}
        return all(
            _same_bytes(by_dst[d], Path(d)) for d in result["copied"] if d in by_dst
        ) and len(result["copied"]) == len([d for d in result["copied"] if d in by_dst])

    def reconcile_tree(args: CopyTreeArgs) -> Reconciliation:
        pairs, total = plan_tree(args)
        missing = [d for _, d in pairs if not d.exists()]
        differing = [d for s, d in pairs if d.exists() and not _same_bytes(s, d)]
        if differing:
            return Reconciliation("unknown")
        if missing:
            return Reconciliation("absent")  # safe to run again: it copies only what is missing
        return Reconciliation(
            "applied",
            {
                "copied": [],
                "already_present": total,
                "conflicts": [],
                "source_files": total,
                "remaining": 0,
            },
        )

    # overwrite / delete (T3) ------------------------------------------------------
    def overwrite(args: CopyArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        src, dst = world.resolve(args.src), world.resolve(args.dst)
        part = dst.with_name(f".jangus-part-{hashlib.sha256(str(dst).encode()).hexdigest()[:12]}")
        with src.open("rb") as source, part.open("wb") as target:
            while chunk := source.read(CHUNK):
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        os.replace(part, dst)
        return {"dst": str(dst)}

    def reconcile_overwrite(args: CopyArgs) -> Reconciliation:
        src, dst = world.resolve(args.src), world.resolve(args.dst)
        if _same_bytes(src, dst):
            return Reconciliation("applied", {"dst": str(dst)})
        return Reconciliation("absent")

    def delete(args: DeleteArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        path = world.resolve(args.path)
        path.unlink()
        return {"path": str(path)}

    def verify_delete(args: DeleteArgs, result: dict[str, Any]) -> bool:
        return not world.resolve(args.path).exists()

    def reconcile_delete(args: DeleteArgs) -> Reconciliation:
        gone = not world.resolve(args.path).exists()
        return Reconciliation("applied", {"path": args.path}) if gone else Reconciliation("absent")

    # report.write --------------------------------------------------------------------
    def write_report(args: ReportArgs, secrets: Mapping[str, str]) -> dict[str, Any]:
        path = world.resolve_report(args.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return {"path": str(path), "created": False}
        with path.open("xb") as handle:
            handle.write(args.content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        return {"path": str(path), "created": True}

    def content_hash(args: ReportArgs) -> str:
        return hashlib.sha256(args.content.encode("utf-8")).hexdigest()

    def verify_report(args: ReportArgs, result: dict[str, Any]) -> bool:
        path = world.resolve_report(args.name)
        return path.is_file() and sha256_file(path) == content_hash(args)

    def reconcile_report(args: ReportArgs) -> Reconciliation:
        path = world.resolve_report(args.name)
        if not path.exists():
            return Reconciliation("absent")
        if sha256_file(path) == content_hash(args):
            return Reconciliation("applied", {"path": str(path), "created": False})
        return Reconciliation("unknown")

    return [
        ToolSpec(
            "file.list",
            1,
            ListArgs,
            Tier.T1,
            True,
            list_files,
            elevation=_tier_if_escapes(world, "root"),
        ),
        ToolSpec(
            "file.read",
            1,
            ReadArgs,
            Tier.T1,
            True,
            read_file,
            elevation=_tier_if_escapes(world, "path"),
        ),
        ToolSpec(
            "file.hash",
            1,
            HashArgs,
            Tier.T1,
            True,
            hash_file,
            elevation=_tier_if_escapes(world, "path"),
        ),
        ToolSpec(
            "file.copy_new",
            1,
            CopyArgs,
            Tier.T2,
            True,
            copy_new,
            verify=verify_copy,
            reconcile=reconcile_copy,
            elevation=_tier_if_escapes(world, "src", "dst"),
        ),
        ToolSpec(
            "file.copy_new_tree",
            1,
            CopyTreeArgs,
            Tier.T2,
            True,
            copy_tree,
            verify=verify_tree,
            reconcile=reconcile_tree,
            elevation=_tier_if_escapes(world, "src_root", "dst_root"),
        ),
        ToolSpec(
            "report.write",
            1,
            ReportArgs,
            Tier.T2,
            True,
            write_report,
            verify=verify_report,
            reconcile=reconcile_report,
        ),
        ToolSpec(
            "file.overwrite",
            1,
            CopyArgs,
            Tier.T3,
            False,
            overwrite,
            verify=verify_copy,
            reconcile=reconcile_overwrite,
        ),
        ToolSpec(
            "file.delete",
            1,
            DeleteArgs,
            Tier.T3,
            False,
            delete,
            verify=verify_delete,
            reconcile=reconcile_delete,
        ),
    ]


def register_file_tools(registry: Any, world: FileWorld) -> None:
    for tool in build_file_tools(world):
        registry.register(tool)
