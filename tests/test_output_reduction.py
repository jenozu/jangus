"""Tests for the output reduction layer (REDUCE.md): extractors, envelope, show, prune.
No model or provider code is involved. Fixtures under tests/fixtures/ are real tool output."""

import datetime
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools" / "taskmaster"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
tasks = importlib.import_module("tasks")
run = importlib.import_module("run")
extractors = importlib.import_module("extractors")
reduction = importlib.import_module("reduction")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def extract(name: str, fixture_name: str) -> dict:
    return extractors.get(name)[1](fixture(fixture_name), 1)


def f(locator, message, line):
    return {"locator": locator, "message": message, "line": line}


# ---------- extractors on fixed inputs: exact expected dicts ----------


def test_pytest_extractor_failing_run():
    assert extract("pytest", "pytest_fail.txt") == {
        "counts": {"passed": 2, "failed": 4, "errors": 0},
        "failures": [
            f("tests/test_a.py:7", "AssertionError: expected warning, got none", 10),
            f("tests/test_a.py:11", "assert [1, 2] == [1, 3]", 18),
            f("tests/test_a.py:15", "assert 2 == 1", 30),
            f("tests/test_a.py:18", "ValueError: boom", 40),
        ],
    }


def test_pytest_extractor_passing_run():
    assert extract("pytest", "pytest_pass.txt") == {
        "counts": {"passed": 2, "failed": 0, "errors": 0},
        "failures": [],
    }


def test_pytest_extractor_collection_error():
    assert extract("pytest", "pytest_collect_error.txt") == {
        "counts": {"passed": 0, "failed": 0, "errors": 1},
        "failures": [
            f("tests/test_b.py:1", "ModuleNotFoundError: No module named 'not_a_module_xyz'", 8)
        ],
    }


RUFF_FAILURES = [
    ("pkg/m.py:1:1", "I001 Import block is un-sorted or un-formatted"),
    ("pkg/m.py:1:8", "F401 `os` imported but unused"),
    ("pkg/m.py:1:11", "F401 `sys` imported but unused"),
    ("pkg/m.py:5:5", "F841 Local variable `y` is assigned to but never used"),
    ("pkg/m.py:5:9", "F821 Undefined name `undefined_name`"),
]


def test_ruff_extractor_concise_format():
    got = extract("ruff", "ruff_concise.txt")
    assert got == {
        "counts": {"errors": 5},
        "failures": [f(loc, msg, n) for n, (loc, msg) in enumerate(RUFF_FAILURES, 1)],
    }


def test_ruff_extractor_full_format():
    got = extract("ruff", "ruff_full.txt")
    assert got["counts"] == {"errors": 5}
    assert [(x["locator"], x["message"]) for x in got["failures"]] == RUFF_FAILURES
    assert [x["line"] for x in got["failures"]] == [1, 19, 33, 47, 58]


def test_ruff_extractor_passing_run():
    assert extract("ruff", "ruff_pass.txt") == {"counts": {"errors": 0}, "failures": []}


def test_mypy_extractor_failing_run():
    assert extract("mypy", "mypy_fail.txt") == {
        "counts": {"errors": 3},
        "failures": [
            f("pkg/m.py:3", 'Incompatible return value type (got "int", expected "str")  '
              "[return-value]", 1),
            f("pkg/m.py:8", 'Incompatible return value type (got "str", expected "int")  '
              "[return-value]", 2),
            f("pkg/m.py:9", 'Incompatible types in assignment (expression has type "str", '
              'variable has type "int")  [assignment]', 3),
        ],
    }


def test_mypy_extractor_passing_run():
    assert extract("mypy", "mypy_pass.txt") == {"counts": {"errors": 0}, "failures": []}


def test_validate_extractor_errors_and_warnings():
    assert extract("validate", "validate_fail.txt") == {
        "counts": {"errors": 4, "warnings": 2},
        "failures": [
            f("1", "runner 'robot' is not one of kernel, local, model, human", 1),
            f("1", "depends on later task 2 (tasks may depend only on lower ids)", 2),
            f("2", "unknown status 'banana'", 3),
            f("2", "runner 'kernel' needs a non-empty verify command", 4),
        ],
    }


def test_validate_extractor_warnings_only():
    assert extract("validate", "validate_warn.txt") == {
        "counts": {"errors": 0, "warnings": 4},
        "failures": [],
    }


def test_generic_extractor_matches_error_lines_and_keeps_the_tail():
    got = extract("generic", "generic_noisy.txt")
    assert got["counts"] == {"lines": 9, "matched": 3}
    assert got["failures"] == [
        f("line 4", "Traceback (most recent call last):", 4),
        f("line 7", "ValueError: bad widget", 7),
        f("line 9", "FAILED to link", 9),
    ]
    assert got["tail"][0] == "building widgets" and got["tail"][-1] == "FAILED to link"


def test_generic_tail_is_the_last_20_lines():
    text = "\n".join(f"row {i}" for i in range(100))
    got = extractors.get("generic")[1](text, 0)
    assert got["tail"] == [f"row {i}" for i in range(80, 100)]
    assert got["failures"] == []


def test_locators_use_forward_slashes_even_for_windows_output():
    ruff = extractors.get("ruff")[1](
        "pkg\\m.py:1:1: F401 `os` imported but unused\nFound 1 error.\n", 1)
    assert ruff["failures"][0]["locator"] == "pkg/m.py:1:1"
    mypy = extractors.get("mypy")[1](
        'pkg\\m.py:3: error: bad  [x]\nFound 1 error in 1 file (checked 1 source file)\n', 1)
    assert mypy["failures"][0]["locator"] == "pkg/m.py:3"
    pytest_out = extractors.get("pytest")[1](
        "FAILED tests\\test_a.py::test_x - boom\n=== 1 failed in 0.01s ===\n", 1)
    assert pytest_out["failures"][0]["locator"] == "tests/test_a.py::test_x"


@pytest.mark.parametrize("name", ["pytest", "ruff", "mypy", "validate"])
def test_parsing_extractors_raise_on_unrecognised_output(name):
    with pytest.raises(ValueError):
        extractors.get(name)[1]("nothing useful here\n", 1)


def test_extractors_are_versioned_and_registered():
    assert extractors.names() == ["generic", "mypy", "pytest", "ruff", "validate"]
    assert extractors.label("pytest") == "pytest@1"
    with pytest.raises(KeyError):
        extractors.get("nope")


# ---------- repo harness ----------


def kernel(tid, verify, **extra):
    return {
        "id": tid, "title": f"task {tid}", "status": "pending", "priority": "medium",
        "dependencies": [], "details": "", "testStrategy": "x", "subtasks": [],
        "runner": "kernel", "verify": verify, **extra,
    }


def printing(name: str, exit_code: int) -> str:
    """A verify command that prints a fixture and exits with exit_code."""
    path = (FIXTURES / name).as_posix()
    body = f"import sys; sys.stdout.write(open('{path}').read()); sys.exit({exit_code})"
    return f'python3 -c "{body}"'


class Repo:
    def __init__(self, root: Path):
        self.root = root
        self.file = root / "tasks.json"
        self.allowlist = root / "allowlist.json"
        self.allowlist.write_text(json.dumps({"allowed": [["python3", "-c"]]}), encoding="utf-8")

    def write(self, task_list):
        self.file.write_text(
            json.dumps({"master": {"tasks": task_list, "metadata": {}}}), encoding="utf-8"
        )

    def task(self, tid):
        data = json.loads(self.file.read_text(encoding="utf-8"))
        return next(t for t in data["master"]["tasks"] if t["id"] == tid)

    def main(self, *args):
        return run.main(["--root", str(self.root), "--file", str(self.file),
                         "--allowlist", str(self.allowlist), *args])

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    def index(self):
        return reduction.read_index(self.runs)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def envelopes(capsys) -> list[dict]:
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    return [json.loads(ln) for ln in lines]


# ---------- pipeline and envelope ----------


def test_failing_pytest_step_prints_one_small_envelope_with_the_locators(repo, capsys):
    repo.write([kernel(1, printing("pytest_fail.txt", 1), extract="pytest")])
    assert repo.main("run") == 1
    out = capsys.readouterr().out
    json_lines = [ln for ln in out.splitlines() if ln.startswith("{")]
    assert len(json_lines) == 1
    env = json.loads(json_lines[0])
    assert len(json_lines[0].encode("utf-8")) <= 4096
    assert env["envelope_bytes"] == len(json_lines[0].encode("utf-8"))
    assert env["status"] == "fail" and env["exit_code"] == 1
    assert env["counts"] == {"passed": 2, "failed": 4, "errors": 0}
    assert [x["locator"] for x in env["failures"]] == [
        "tests/test_a.py:7", "tests/test_a.py:11", "tests/test_a.py:15", "tests/test_a.py:18"]
    assert all(set(x) == {"locator", "message"} for x in env["failures"])  # no internal "line"
    assert env["failures_total"] == 4 and env["truncated"] is False
    assert env["extractor"] == "pytest@1" and env["task"] == "1"
    assert "\\" not in env["raw"] and not env["raw"].startswith("/")
    assert "=== test session starts" not in out  # the raw output is not printed
    assert repo.task(1)["status"] == "pending"


def test_envelope_has_exactly_the_agreed_keys(repo, capsys):
    repo.write([kernel(1, printing("pytest_pass.txt", 0), extract="pytest")])
    assert repo.main("run") == 0
    (env,) = envelopes(capsys)
    assert set(env) == {"task", "status", "exit_code", "duration_s", "counts", "failures",
                        "failures_total", "truncated", "raw", "raw_sha256", "extractor",
                        "envelope_bytes"}
    assert env["status"] == "pass" and env["failures"] == [] and env["failures_total"] == 0
    assert repo.task(1)["status"] == "done"


def test_raw_file_exists_and_its_sha256_matches_the_envelope(repo, capsys):
    repo.write([kernel(1, printing("pytest_fail.txt", 1), extract="pytest")])
    repo.main("run")
    (env,) = envelopes(capsys)
    raw = repo.root / env["raw"]
    data = raw.read_bytes()
    assert hashlib.sha256(data).hexdigest() == env["raw_sha256"]
    assert raw.parent.name == datetime.date.today().isoformat() or raw.parent.parent.name == "runs"
    assert b"4 failed, 2 passed" in data
    raw.write_bytes(data + b"tampered")
    assert hashlib.sha256(raw.read_bytes()).hexdigest() != env["raw_sha256"]


def test_raw_file_keeps_stdout_and_stderr(repo, capsys):
    code = "import sys; print('to-out'); print('to-err', file=sys.stderr)"
    repo.write([kernel(1, f'python3 -c "{code}"')])
    repo.main("run")
    (env,) = envelopes(capsys)
    text = (repo.root / env["raw"]).read_text(encoding="utf-8")
    assert "to-out" in text and "to-err" in text and "--- stderr ---" in text
    assert hashlib.sha256((repo.root / env["raw"]).read_bytes()).hexdigest() == env["raw_sha256"]


def test_index_and_latest_are_updated_and_runs_are_numbered(repo, capsys):
    repo.write([kernel(1, printing("pytest_fail.txt", 1), extract="pytest")])
    repo.main("run")
    repo.main("run")
    first, second = envelopes(capsys)
    rows = repo.index()
    assert [r["run"] for r in rows] == [1, 2]
    assert rows[0]["task"] == "1" and rows[0]["status"] == "fail"
    assert rows[0]["raw"] == first["raw"] and rows[1]["raw"] == second["raw"]
    assert rows[0]["envelope_bytes"] == first["envelope_bytes"]
    assert first["raw"].endswith("1-1.log") and second["raw"].endswith("1-2.log")
    latest = json.loads((repo.runs / "latest.json").read_text(encoding="utf-8"))
    assert latest["1"] == second


def test_exit_zero_but_extractor_sees_failures_is_not_a_pass(repo, capsys):
    repo.write([kernel(1, printing("pytest_fail.txt", 0), extract="pytest")])
    assert repo.main("run") == 1
    (env,) = envelopes(capsys)
    assert env["status"] == "fail" and env["exit_code"] == 0
    assert repo.task(1)["status"] == "pending"


def test_default_extractor_is_generic(repo, capsys):
    repo.write([kernel(1, 'python3 -c "print(\'Error: x\')"')])
    repo.main("run")
    (env,) = envelopes(capsys)
    assert env["extractor"] == "generic@1" and env["status"] == "pass"
    assert env["failures"][0]["message"] == "Error: x"
    assert env["tail"] == ["Error: x"]


# ---------- size cap ----------


def long_failures(n: int, size: int = 200) -> list[dict]:
    return [{"locator": f"file{i}.py:{i}", "message": "m" * size, "line": i} for i in range(n)]


def build(failures, tail=None):
    return reduction.build_envelope(
        task="1", status="fail", exit_code=1, duration_s=0.5, counts={"failed": len(failures)},
        failures=failures, tail=tail, raw="runs/x.log", raw_sha256="0" * 64, extractor="t@1")


def test_envelope_over_4096_bytes_is_truncated_and_flagged():
    env, line = build(long_failures(20))
    assert len(line.encode("utf-8")) <= 4096
    assert env["truncated"] is True
    assert 0 < len(env["failures"]) < 20
    assert env["failures_total"] == 20  # the real count is kept
    assert env["envelope_bytes"] == len(line.encode("utf-8"))
    kept = len(env["failures"])
    assert [x["locator"] for x in env["failures"]] == [f"file{i}.py:{i}" for i in range(kept)]


def test_small_envelope_is_not_flagged_as_truncated():
    env, _ = build(long_failures(2, size=10))
    assert env["truncated"] is False and len(env["failures"]) == 2


def test_tail_is_trimmed_when_the_envelope_is_still_too_big():
    tail = [f"{i:02d}" + "t" * 198 for i in range(40)]
    env, line = build([], tail=tail)
    assert len(line.encode("utf-8")) <= 4096 and env["truncated"] is True
    assert 0 < len(env["tail"]) < 40
    assert env["tail"][-1] == tail[-1]  # the newest lines are the ones kept


def test_failures_over_20_are_capped_and_failures_total_is_exact():
    env, _ = build(long_failures(35, size=5))
    assert len(env["failures"]) == 20
    assert env["failures_total"] == 35


def test_messages_are_cut_to_200_characters():
    env, _ = build(long_failures(1, size=500))
    assert len(env["failures"][0]["message"]) == 200


# ---------- extractor errors ----------


def test_extractor_that_raises_gives_status_error_keeps_raw_and_does_not_mark_done(
    repo, capsys, monkeypatch
):
    def boom(text, exit_code):
        raise RuntimeError("parser broke")

    monkeypatch.setitem(extractors.REGISTRY, "boom", (1, boom))
    repo.write([kernel(1, 'python3 -c "print(\'hello\')"', extract="boom")])
    assert repo.main("run") == 1
    (env,) = envelopes(capsys)
    assert env["status"] == "error" and env["exit_code"] == 0
    assert "parser broke" in env["error"]
    raw = repo.root / env["raw"]
    assert raw.exists() and b"hello" in raw.read_bytes()
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == env["raw_sha256"]
    assert repo.task(1)["status"] == "pending"
    assert repo.index()[0]["status"] == "error"


def test_extractor_returning_a_bad_shape_is_an_error(repo, capsys, monkeypatch):
    monkeypatch.setitem(extractors.REGISTRY, "bad", (1, lambda text, code: {"counts": "x"}))
    repo.write([kernel(1, 'python3 -c "print(1)"', extract="bad")])
    assert repo.main("run") == 1
    assert envelopes(capsys)[0]["status"] == "error"


def test_unrecognised_output_for_a_parsing_extractor_is_an_error_not_a_pass(repo, capsys):
    repo.write([kernel(1, 'python3 -c "print(1)"', extract="pytest")])
    assert repo.main("run") == 1
    assert envelopes(capsys)[0]["status"] == "error"
    assert repo.task(1)["status"] == "pending"


# ---------- extract field validation ----------


def test_unknown_extract_name_fails_validation():
    errors, _ = tasks.validate([kernel(1, "pytest", extract="pytes")])
    assert any("extract 'pytes' is not a known extractor" in e for e in errors)


def test_known_extract_names_pass_validation():
    for name in extractors.names():
        assert tasks.validate([kernel(1, "pytest", extract=name)])[0] == []


def test_non_string_extract_fails_validation():
    errors, _ = tasks.validate([kernel(1, "pytest", extract=3)])
    assert any("extract" in e for e in errors)


def test_unknown_extract_is_refused_at_run_time_and_nothing_runs(repo, capsys):
    marker = repo.root / "ran"
    cmd = f"python3 -c \"open('{marker.as_posix()}', 'w').close()\""
    repo.write([kernel(1, cmd, extract="nope")])
    assert repo.main("run") == 1
    assert "unknown extractor 'nope'" in capsys.readouterr().out
    assert not marker.exists() and not repo.runs.exists()


# ---------- show ----------


def make_run(root: Path, task="1", lines=500, error_at=(250,), extract="generic", now=None):
    out = "\n".join(
        f"Error: bad thing {n}" if n in error_at else f"row {n}" for n in range(1, lines + 1)
    ).encode() + b"\n"
    result = {"_stdout": out, "_stderr": b"", "exit_code": 1, "seconds": 0.1, "passed": False}
    return reduction.reduce_step({"id": task}, result, root=root, runs_dir=root / "runs",
                                 extract=extract, now=now)


def test_show_without_failure_prints_only_the_first_200_lines(repo, capsys):
    make_run(repo.root, lines=500)
    assert repo.main("show", "1") == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 200
    assert out[0] == "1: row 1" and out[-1] == "200: row 200"


def test_show_failure_prints_lines_around_the_locator(repo, capsys):
    make_run(repo.root, lines=500, error_at=(250,))
    assert repo.main("show", "1", "--failure", "1", "--context", "3") == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["247: row 247", "248: row 248", "249: row 249", "250: Error: bad thing 250",
                   "251: row 251", "252: row 252", "253: row 253"]


def test_show_failure_is_bounded_to_200_lines_whatever_the_context(repo, capsys):
    make_run(repo.root, lines=2000, error_at=(1000,))
    assert repo.main("show", "1", "--failure", "1", "--context", "100000") == 0
    out = capsys.readouterr().out.splitlines()
    assert 0 < len(out) <= 200
    assert any("Error: bad thing 1000" in ln for ln in out)


def test_show_default_context_is_20_each_side(repo, capsys):
    make_run(repo.root, lines=500, error_at=(250,))
    repo.main("show", "1", "--failure", "1")
    assert len(capsys.readouterr().out.splitlines()) == 41


def test_show_picks_the_kth_failure_and_a_given_run(repo, capsys):
    make_run(repo.root, lines=100, error_at=(10, 60))
    make_run(repo.root, lines=100, error_at=(20,))
    repo.main("show", "1", "--run", "1", "--failure", "2", "--context", "0")
    assert capsys.readouterr().out.splitlines() == ["60: Error: bad thing 60"]
    repo.main("show", "1", "--failure", "1", "--context", "0")  # latest run is run 2
    assert capsys.readouterr().out.splitlines() == ["20: Error: bad thing 20"]


def test_show_cuts_very_long_lines(repo, capsys):
    result = {"_stdout": b"x" * 100000 + b"\n", "_stderr": b"", "exit_code": 0, "seconds": 0,
              "passed": True}
    reduction.reduce_step({"id": "1"}, result, root=repo.root, runs_dir=repo.runs,
                          extract="generic")
    repo.main("show", "1")
    (line,) = capsys.readouterr().out.splitlines()
    assert len(line) <= 505


def test_show_errors_are_clear_and_exit_2(repo, capsys):
    assert repo.main("show", "9") == 2
    make_run(repo.root, lines=10, error_at=(5,))
    assert repo.main("show", "1", "--failure", "7") == 2
    assert repo.main("show", "1", "--failure", "0") == 2
    assert repo.main("show", "1", "--context", "-1") == 2
    assert repo.main("show", "1", "--run", "5") == 2
    err = capsys.readouterr().err
    assert "no recorded run" in err and "does not exist" in err


def test_show_reports_a_pruned_raw_log(repo, capsys):
    env, _ = make_run(repo.root, lines=10)
    (repo.root / env["raw"]).unlink()
    assert repo.main("show", "1") == 2
    assert "raw log is gone" in capsys.readouterr().err


def test_show_warns_when_the_raw_log_was_changed(repo, capsys):
    env, _ = make_run(repo.root, lines=10)
    raw = repo.root / env["raw"]
    raw.write_bytes(raw.read_bytes() + b"extra\n")
    assert repo.main("show", "1") == 0
    assert "does not match its recorded sha256" in capsys.readouterr().err


def test_show_refuses_a_raw_path_outside_the_runs_directory(repo, capsys):
    make_run(repo.root, lines=5)
    rows = repo.index()
    rows[0]["raw"] = "../outside.log"
    reduction.write_index(repo.runs, rows)
    assert repo.main("show", "1") == 2
    assert "outside the runs directory" in capsys.readouterr().err


def test_show_on_a_real_pytest_log_points_at_the_traceback(repo, capsys):
    text = fixture("pytest_fail.txt").encode("utf-8")
    result = {"_stdout": text, "_stderr": b"", "exit_code": 1, "seconds": 1, "passed": False}
    reduction.reduce_step({"id": "7"}, result, root=repo.root, runs_dir=repo.runs, extract="pytest")
    repo.main("show", "7", "--failure", "2", "--context", "2")
    out = capsys.readouterr().out
    assert "TestC.test_m" in out  # the section header of failure 2 (line 18)


# ---------- prune ----------


def aware(days_ago: int) -> datetime.datetime:
    return datetime.datetime.now().astimezone() - datetime.timedelta(days=days_ago)


def test_prune_deletes_logs_older_than_14_days_and_their_index_entries(repo, capsys):
    old, _ = make_run(repo.root, task="1", lines=5, now=aware(20))
    new, _ = make_run(repo.root, task="2", lines=5, now=aware(2))
    assert repo.main("prune") == 0
    assert not (repo.root / old["raw"]).exists()
    assert (repo.root / new["raw"]).exists()
    assert [r["task"] for r in repo.index()] == ["2"]
    latest = json.loads((repo.runs / "latest.json").read_text(encoding="utf-8"))
    assert list(latest) == ["2"]
    assert "deleted 1 raw log" in capsys.readouterr().out


def test_prune_removes_empty_date_folders(repo):
    old, _ = make_run(repo.root, lines=3, now=aware(30))
    folder = (repo.root / old["raw"]).parent
    repo.main("prune")
    assert not folder.exists()


def test_prune_keeps_a_log_at_exactly_the_boundary_and_honours_days(repo):
    make_run(repo.root, task="1", lines=3, now=aware(10))
    repo.main("prune")  # 10 days old: inside the 14 day window
    assert len(repo.index()) == 1
    repo.main("prune", "--days", "5")
    assert repo.index() == []


def test_prune_dry_run_deletes_nothing(repo, capsys):
    old, _ = make_run(repo.root, lines=3, now=aware(40))
    assert repo.main("prune", "--dry-run") == 0
    assert (repo.root / old["raw"]).exists() and len(repo.index()) == 1
    assert "would delete 1 raw log" in capsys.readouterr().out


def test_prune_removes_old_logs_the_index_does_not_know(repo):
    old, _ = make_run(repo.root, lines=3, now=aware(30))
    stray = (repo.root / old["raw"]).parent / "orphan-9.log"
    stray.write_text("x", encoding="utf-8")
    (repo.runs / "index.jsonl").write_text("", encoding="utf-8")
    repo.main("prune")
    assert not stray.exists()


def test_prune_never_deletes_outside_the_runs_directory(repo, capsys):
    outside = repo.root / "precious.txt"
    outside.write_text("keep me", encoding="utf-8")
    make_run(repo.root, lines=3, now=aware(30))
    rows = repo.index()
    rows[0]["raw"] = "precious.txt"
    reduction.write_index(repo.runs, rows)
    assert repo.main("prune") == 2
    assert outside.exists()


def test_prune_is_not_run_automatically(repo, capsys):
    old, _ = make_run(repo.root, lines=3, now=aware(40))
    repo.write([kernel(1, 'python3 -c "print(1)"')])
    repo.main("run")
    assert (repo.root / old["raw"]).exists()  # a normal run leaves old logs alone


# ---------- the run path stays free of model code ----------


def test_reduction_modules_import_no_model_or_provider_code():
    import ast

    stdlib = set(sys.stdlib_module_names)
    for name in ("extractors.py", "reduction.py"):
        tree = ast.parse((TOOLS / name).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {n.name.split(".")[0] for n in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        assert found <= stdlib | {"extractors"}, f"{name}: {sorted(found - stdlib)}"


def test_a_full_run_loads_no_model_or_provider_module(repo):
    import subprocess

    repo.write([kernel(1, printing("pytest_fail.txt", 1), extract="pytest")])
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(TOOLS)!r})\n"
        "import run\n"
        f"run.main(['--root', {str(repo.root)!r}, '--file', {str(repo.file)!r},"
        f" '--allowlist', {str(repo.allowlist)!r}, 'run'])\n"
        f"run.main(['--root', {str(repo.root)!r}, '--file', {str(repo.file)!r},"
        " 'show', '1', '--failure', '1'])\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, check=False
    )
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    banned = {"jangus", "anthropic", "openai", "ollama", "httpx", "requests", "urllib3",
              "aiohttp", "pydantic", "yaml"}
    assert not {m.split(".")[0] for m in loaded} & banned
