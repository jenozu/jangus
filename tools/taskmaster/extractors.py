"""Deterministic output extractors for the kernel runner (stdlib only, see REDUCE.md).

An extractor is a pure function: (text, exit_code) -> {"counts": {...}, "failures": [...]}.
It does no I/O, no network and no model call. Extractors are registered by name with a
version, and a task selects one with its `extract` field (default: generic).

A failure is {"locator": str, "message": str} and may carry an internal "line" (1-based line
in the text). The envelope never shows "line"; `run.py show` uses it to find the right place.
The generic extractor also returns "tail" (the last lines of the text).
"""
import re
from collections.abc import Callable
from typing import Protocol

DEFAULT_EXTRACTOR = "generic"
GENERIC_TAIL_LINES = 20
ERROR_PATTERN = re.compile(r"error|Error|FAILED|Traceback")

Extractor = Callable[[str, int | None], dict]
REGISTRY: dict[str, tuple[int, Extractor]] = {}


class Summarizer(Protocol):
    """Layer 2 placeholder (REDUCE.md section 9): intentionally has NO implementation.

    A later change may add a local-model summarizer behind this interface. Nothing on the run
    path references it, and this module must never import model or provider code.
    """

    def summarize(self, envelope: dict, raw_text: str) -> str: ...


def register(name: str, version: int):
    def deco(fn: Extractor) -> Extractor:
        REGISTRY[name] = (version, fn)
        return fn
    return deco


def names() -> list[str]:
    return sorted(REGISTRY)


def get(name: str) -> tuple[int, Extractor]:
    if name not in REGISTRY:
        raise KeyError(f"unknown extractor '{name}' (known: {', '.join(names())})")
    return REGISTRY[name]


def label(name: str) -> str:
    """The versioned name that goes into the envelope, e.g. pytest@1."""
    return f"{name}@{get(name)[0]}"


def check_result(result) -> None:
    """Raise ValueError unless an extractor returned the agreed shape."""
    if not isinstance(result, dict):
        raise ValueError("extractor must return a dict")
    counts, failures = result.get("counts"), result.get("failures")
    if not isinstance(counts, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
        for k, v in counts.items()
    ):
        raise ValueError("counts must be a dict of str -> int")
    if not isinstance(failures, list) or not all(
        isinstance(f, dict) and isinstance(f.get("locator"), str)
        and isinstance(f.get("message"), str) and isinstance(f.get("line", 1), int)
        for f in failures
    ):
        raise ValueError("failures must be a list of {locator, message} dicts")
    tail = result.get("tail")
    if tail is not None and not (isinstance(tail, list) and all(isinstance(x, str) for x in tail)):
        raise ValueError("tail must be a list of strings")


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def _failure(locator: str, message: str, line: int) -> dict:
    return {"locator": locator, "message": message.strip(), "line": line}


# ---------- generic@1 ----------

@register("generic", 1)
def generic(text: str, exit_code: int | None) -> dict:
    lines = text.splitlines()
    failures = [
        _failure(f"line {n}", ln, n) for n, ln in enumerate(lines, 1) if ERROR_PATTERN.search(ln)
    ]
    counts = {"lines": len(lines), "matched": len(failures)}
    return {"counts": counts, "failures": failures, "tail": lines[-GENERIC_TAIL_LINES:]}


# ---------- pytest@1 ----------

PYTEST_ITEM = re.compile(
    r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|warnings?|deselected|rerun)\b"
)
PYTEST_SUMMARY = re.compile(r"^(?:=+ )?(.*?) in [\d.]+s(?: \(\d+:\d+:\d+\))?(?: =+)?$")
PYTEST_RESULT = re.compile(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$")
PYTEST_HEADER = re.compile(r"^_{2,} (.+?) _{2,}$")
PYTEST_SECTION_END = re.compile(r"^(?:_{3,} .+ _{3,}|={3,}.*)$")
PYTEST_FRAME = re.compile(r"^(\S+?\.py):(\d+): ")
PYTEST_E_LINE = re.compile(r"^E\s+(\S.*)$")


def _pytest_counts(lines: list[str]) -> dict:
    for ln in reversed(lines):
        m = PYTEST_SUMMARY.match(ln.strip())
        if not m:
            continue
        items = PYTEST_ITEM.findall(m.group(1))
        if not items and "no tests ran" not in m.group(1):
            continue
        counts = {"passed": 0, "failed": 0, "errors": 0}
        for number, word in items:
            if word in counts:
                counts[word] += int(number)
            elif word in ("error", "errors"):
                counts["errors"] += int(number)
        return counts
    raise ValueError("no pytest summary line found")


def _pytest_section(lines: list[str], nodeid: str) -> tuple[int, int] | None:
    """Find the traceback section of a failing node: (header index, end index)."""
    wanted = {nodeid.split("::", 1)[-1].replace("::", "."), f"ERROR collecting {nodeid}"}
    for i, ln in enumerate(lines):
        m = PYTEST_HEADER.match(ln)
        if m and m.group(1) in wanted:
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if PYTEST_SECTION_END.match(lines[j]):
                    end = j
                    break
            return i, end
    return None


@register("pytest", 1)
def pytest_extractor(text: str, exit_code: int | None) -> dict:
    lines = text.splitlines()
    counts = _pytest_counts(lines)
    failures = []
    for n, ln in enumerate(lines, 1):
        m = PYTEST_RESULT.match(ln.strip())
        if not m:
            continue
        nodeid, message, line = _posix(m.group(2)), m.group(3) or "", n
        locator = nodeid
        section = _pytest_section(lines, nodeid)
        if section:
            start, end = section
            line = start + 1
            frames = [PYTEST_FRAME.match(x) for x in lines[start:end]]
            frames = [f for f in frames if f]
            if frames:
                locator = f"{_posix(frames[-1].group(1))}:{frames[-1].group(2)}"
            if not message:
                e_lines = [PYTEST_E_LINE.match(x) for x in lines[start:end]]
                e_lines = [e for e in e_lines if e]
                message = e_lines[-1].group(1) if e_lines else ""
        failures.append(_failure(locator, message, line))
    return {"counts": counts, "failures": failures}


# ---------- ruff@1 ----------

RUFF_CONCISE = re.compile(r"^(\S.*?:\d+:\d+): ([A-Z]+\d+) (?:\[\*\] )?(.*)$")
RUFF_HEADER = re.compile(r"^([A-Z]+\d+) (?:\[\*\] )?(.*)$")
RUFF_ARROW = re.compile(r"^\s*--> (.+:\d+:\d+)\s*$")
RUFF_FOUND = re.compile(r"^Found (\d+) errors?\.$")


@register("ruff", 1)
def ruff_extractor(text: str, exit_code: int | None) -> dict:
    lines = text.splitlines()
    failures = []
    for i, ln in enumerate(lines):
        m = RUFF_CONCISE.match(ln)
        if m:
            failures.append(_failure(_posix(m.group(1)), f"{m.group(2)} {m.group(3)}", i + 1))
            continue
        h = RUFF_HEADER.match(ln)
        arrow = RUFF_ARROW.match(lines[i + 1]) if h and i + 1 < len(lines) else None
        if h and arrow:
            failures.append(_failure(_posix(arrow.group(1)), f"{h.group(1)} {h.group(2)}", i + 1))
    errors = None
    for ln in reversed(lines):
        f = RUFF_FOUND.match(ln.strip())
        if f:
            errors = int(f.group(1))
            break
        if ln.strip() == "All checks passed!":
            errors = 0
            break
    if errors is None:
        if not failures:
            raise ValueError("no ruff summary or diagnostics found")
        errors = len(failures)
    return {"counts": {"errors": errors}, "failures": failures}


# ---------- mypy@1 ----------

MYPY_ERROR = re.compile(r"^(.+?):(\d+)(?::(\d+))?: error: (.*)$")
MYPY_FOUND = re.compile(r"^Found (\d+) errors? in \d+ files?")
MYPY_OK = re.compile(r"^Success: no issues found")


@register("mypy", 1)
def mypy_extractor(text: str, exit_code: int | None) -> dict:
    lines = text.splitlines()
    failures = []
    for n, ln in enumerate(lines, 1):
        m = MYPY_ERROR.match(ln)
        if m:
            loc = f"{_posix(m.group(1))}:{m.group(2)}" + (f":{m.group(3)}" if m.group(3) else "")
            failures.append(_failure(loc, m.group(4), n))
    errors = None
    for ln in reversed(lines):
        f = MYPY_FOUND.match(ln.strip())
        if f:
            errors = int(f.group(1))
            break
        if MYPY_OK.match(ln.strip()):
            errors = 0
            break
    if errors is None:
        if not failures:
            raise ValueError("no mypy summary or diagnostics found")
        errors = len(failures)
    return {"counts": {"errors": errors}, "failures": failures}


# ---------- validate@1 (tasks.py validate) ----------

VALIDATE_ERROR = re.compile(r"^ERROR: (?:(\d+(?:\.\d+)?): )?(.*)$")
VALIDATE_WARN = re.compile(r"^warn: ")


@register("validate", 1)
def validate_extractor(text: str, exit_code: int | None) -> dict:
    failures, warnings, ok = [], 0, False
    for n, ln in enumerate(text.splitlines(), 1):
        m = VALIDATE_ERROR.match(ln)
        if m:
            failures.append(_failure(m.group(1) or "validate", m.group(2), n))
        elif VALIDATE_WARN.match(ln):
            warnings += 1
        elif ln.strip() == "ok: no problems found":
            ok = True
    if not (failures or warnings or ok):
        raise ValueError("no output of tasks.py validate recognised")
    return {"counts": {"errors": len(failures), "warnings": warnings}, "failures": failures}
