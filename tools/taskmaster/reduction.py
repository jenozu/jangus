"""Output reduction for the deterministic runner (stdlib only, see REDUCE.md).

Pipeline for one kernel step: save the full output to runs/<date>/<task>-<n>.log, run the
task's extractor over that same text, build a small envelope, append runs/index.jsonl and
update runs/latest.json. Every number in the envelope comes from code: the exit code, the
duration, the extractor counts and locators, and the sha256 of the raw file. No model is
involved, and this module imports none (a test enforces it).
"""
import datetime
import hashlib
import json
import os
from pathlib import Path

import extractors

ENVELOPE_MAX_BYTES = 4096
MAX_FAILURES = 20
MAX_MESSAGE_CHARS = 200
RETENTION_DAYS = 14
SHOW_MAX_LINES = 200
SHOW_DEFAULT_CONTEXT = 20
SHOW_LINE_MAX_CHARS = 500
STDERR_MARK = b"\n--- stderr ---\n"
INDEX_NAME = "index.jsonl"
LATEST_NAME = "latest.json"


class ReductionError(Exception):
    """A reduction, show or prune request cannot be served."""


# ---------- small helpers ----------

def local_now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def cut(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    text = str(text).replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def display_path(path: Path, root: Path) -> str:
    """Path as stored in output: relative to the repo root when possible, always with '/'."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def inside(path: Path, base: Path) -> bool:
    return base.resolve() in path.resolve().parents


def dumps(envelope: dict) -> str:
    return json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))


def encode(envelope: dict) -> str:
    """Serialise with envelope_bytes set to the real length of the final line."""
    size = 0
    for _ in range(10):
        envelope["envelope_bytes"] = size
        line = dumps(envelope)
        if len(line) == size:
            return line
        size = len(line)
    raise RuntimeError("envelope size did not settle")  # cannot happen: digits only grow


# ---------- envelope ----------

def build_envelope(*, task, status, exit_code, duration_s, counts, failures, tail, raw,
                   raw_sha256, extractor, error=None) -> tuple[dict, str]:
    """Build the envelope and its one-line JSON, never larger than ENVELOPE_MAX_BYTES."""
    visible = [
        {"locator": cut(f["locator"]), "message": cut(f["message"])}
        for f in failures[:MAX_FAILURES]
    ]
    env: dict = {
        "task": str(task), "status": status, "exit_code": exit_code, "duration_s": duration_s,
        "counts": counts, "failures": visible, "failures_total": len(failures),
        "truncated": False, "raw": raw, "raw_sha256": raw_sha256, "extractor": extractor,
        "envelope_bytes": 0,
    }
    if tail is not None:
        env["tail"] = [cut(x) for x in tail]
    if error is not None:
        env["error"] = cut(error)
    line = encode(env)
    while len(line) > ENVELOPE_MAX_BYTES:
        if env["failures"]:
            env["failures"].pop()
        elif env.get("tail"):
            env["tail"].pop(0)  # keep the newest lines
        else:
            raise RuntimeError("envelope cannot be made small enough")
        env["truncated"] = True
        line = encode(env)
    return env, line


# ---------- index, latest ----------

def read_index(runs_dir: Path) -> list[dict]:
    path = runs_dir / INDEX_NAME
    if not path.exists():
        return []
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(ln)
        except ValueError:
            continue  # a torn line must not break reading
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_index(runs_dir: Path, rows: list[dict]) -> None:
    path = runs_dir / INDEX_NAME
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read_latest(runs_dir: Path) -> dict:
    try:
        data = json.loads((runs_dir / LATEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_latest(runs_dir: Path, data: dict) -> None:
    path = runs_dir / LATEST_NAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8",
                   newline="\n")
    os.replace(tmp, path)


def next_run_number(rows: list[dict], task: str) -> int:
    runs = [r.get("run") for r in rows if str(r.get("task")) == task]
    return max([x for x in runs if isinstance(x, int)], default=0) + 1


# ---------- the pipeline ----------

def raw_bytes(out: bytes, err: bytes) -> bytes:
    return out + (STDERR_MARK + err if err else b"")


def save_raw(runs_dir: Path, when: datetime.datetime, task: str, run: int, data: bytes) -> Path:
    folder = runs_dir / when.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{task}-{run}.log"
    with path.open("xb") as f:  # never overwrite an earlier log
        f.write(data)
    return path


def reduce_step(item: dict, result: dict, *, root: Path, runs_dir: Path, extract: str,
                now: datetime.datetime | None = None) -> tuple[dict, str]:
    """Save, extract, envelope, index. Returns (envelope, the one-line JSON to print)."""
    when = now or local_now()
    task = str(item["id"])
    runs_dir.mkdir(parents=True, exist_ok=True)
    rows = read_index(runs_dir)
    run = next_run_number(rows, task)
    data = raw_bytes(result["_stdout"], result["_stderr"])
    path = save_raw(runs_dir, when, task, run, data)
    sha = hashlib.sha256(data).hexdigest()
    text = data.decode("utf-8", "replace")

    error = None
    counts: dict = {}
    failures: list = []
    tail = None
    try:
        extracted = extractors.get(extract)[1](text, result["exit_code"])
        extractors.check_result(extracted)
        counts, failures, tail = extracted["counts"], extracted["failures"], extracted.get("tail")
        clean = bool(result["passed"]) and not counts.get("failed") and not counts.get("errors")
        status = "pass" if clean else "fail"
    except Exception as e:  # noqa: BLE001 - any extractor fault becomes status=error
        status = "error"
        error = f"{type(e).__name__}: {e}"
    env, line = build_envelope(
        task=task, status=status, exit_code=result["exit_code"], duration_s=result["seconds"],
        counts=counts, failures=failures, tail=tail, raw=display_path(path, root),
        raw_sha256=sha, extractor=extractors.label(extract), error=error,
    )
    with (runs_dir / INDEX_NAME).open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({
            "ts": when.isoformat(timespec="seconds"), "task": task, "run": run,
            "status": status, "raw": env["raw"], "raw_sha256": sha,
            "extractor": env["extractor"], "envelope_bytes": env["envelope_bytes"],
        }) + "\n")
    latest = read_latest(runs_dir)
    latest[task] = env
    write_latest(runs_dir, latest)
    return env, line


# ---------- show (bounded drill-down) ----------

def find_entry(runs_dir: Path, task: str, run: int | None) -> dict:
    rows = [r for r in read_index(runs_dir) if str(r.get("task")) == str(task)]
    if run is not None:
        rows = [r for r in rows if r.get("run") == run]
    if not rows:
        raise ReductionError(f"no recorded run for task {task}" + (f" run {run}" if run else ""))
    return max(rows, key=lambda r: r.get("run") if isinstance(r.get("run"), int) else 0)


def raw_path(root: Path, runs_dir: Path, entry: dict) -> Path:
    path = Path(str(entry.get("raw", "")))
    if not path.is_absolute():
        path = root / path
    if not inside(path, runs_dir):
        raise ReductionError("raw path is outside the runs directory")
    return path


def show(root: Path, runs_dir: Path, task: str, run: int | None, failure: int | None,
         context: int) -> tuple[list[str], list[str]]:
    """Return (lines to print, warnings). Never more than SHOW_MAX_LINES lines."""
    if context < 0:
        raise ReductionError("--context must be 0 or more")
    if failure is not None and failure < 1:
        raise ReductionError("--failure counts from 1")
    entry = find_entry(runs_dir, task, run)
    path = raw_path(root, runs_dir, entry)
    try:
        data = path.read_bytes()
    except OSError:
        raise ReductionError(f"raw log is gone (pruned?): {entry.get('raw')}") from None
    warnings = []
    if hashlib.sha256(data).hexdigest() != entry.get("raw_sha256"):
        warnings.append("warning: raw log does not match its recorded sha256")
    lines = data.decode("utf-8", "replace").splitlines()
    if failure is None:
        first, last = 1, min(len(lines), SHOW_MAX_LINES)
    else:
        name = str(entry.get("extractor", extractors.DEFAULT_EXTRACTOR)).split("@")[0]
        try:
            found = extractors.get(name)[1](data.decode("utf-8", "replace"), None)
            extractors.check_result(found)
        except Exception as e:  # noqa: BLE001
            raise ReductionError(f"extractor {name} failed on this log: {e}") from e
        items = found["failures"]
        if failure > len(items):
            raise ReductionError(f"failure {failure} does not exist (this run has {len(items)})")
        item = items[failure - 1]
        center = item.get("line")
        if not isinstance(center, int):
            center = next((n for n, ln in enumerate(lines, 1) if item["locator"] in ln), None)
        if center is None:
            raise ReductionError(f"cannot find {item['locator']} in the raw log")
        context = min(context, (SHOW_MAX_LINES - 1) // 2)
        first, last = max(1, center - context), min(len(lines), center + context)
    out = [f"{n}: {lines[n - 1][:SHOW_LINE_MAX_CHARS]}" for n in range(first, last + 1)]
    return out[:SHOW_MAX_LINES], warnings


# ---------- prune (manual, never automatic) ----------

def prune(root: Path, runs_dir: Path, days: int = RETENTION_DAYS, dry_run: bool = False,
          now: datetime.datetime | None = None) -> dict:
    """Delete raw logs older than `days` and their index/latest entries."""
    if days < 0:
        raise ReductionError("--days must be 0 or more")
    cutoff = (now or local_now()) - datetime.timedelta(days=days)
    rows = read_index(runs_dir)
    keep, drop = [], []
    for r in rows:
        try:
            old = datetime.datetime.fromisoformat(str(r.get("ts"))) < cutoff
        except ValueError:
            old = False  # unreadable timestamp: keep, never guess
        (drop if old else keep).append(r)
    removed_files = 0
    for r in drop:
        path = raw_path(root, runs_dir, r)
        if path.exists():
            removed_files += 1
            if not dry_run:
                path.unlink()
    dropped_raw = {str(r.get("raw")) for r in drop}
    if runs_dir.exists():  # logs of old days that the index no longer knows about
        for folder in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
            try:
                day = datetime.date.fromisoformat(folder.name)
            except ValueError:
                continue
            if day < cutoff.date():
                for stale in folder.glob("*.log"):
                    if stale.exists() and not any(raw_path(root, runs_dir, r) == stale for r in drop):
                        removed_files += 1
                        if not dry_run:
                            stale.unlink()
    removed_dirs = 0
    if not dry_run and runs_dir.exists():
        for folder in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
            if not any(folder.iterdir()):
                folder.rmdir()
                removed_dirs += 1
        if drop:
            write_index(runs_dir, keep)
            latest = read_latest(runs_dir)
            kept = {t: e for t, e in latest.items()
                    if not (isinstance(e, dict) and str(e.get("raw")) in dropped_raw)}
            write_latest(runs_dir, kept)
    return {"entries": len(drop), "files": removed_files, "dirs": removed_dirs,
            "kept": len(keep), "dry_run": dry_run}
