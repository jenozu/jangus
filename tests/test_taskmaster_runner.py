"""Tests for the deterministic task runner (tools/taskmaster/run.py) and the runner/verify
validator rules in tools/taskmaster/tasks.py. No model or provider code is involved."""

import ast
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools" / "taskmaster"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
tasks = importlib.import_module("tasks")
run = importlib.import_module("run")

MARK = "python3 -c \"open('{}', 'w').close()\""  # creates a marker file in the repo root


def mk(tid, deps=(), status="pending", **extra):
    base = {
        "id": tid,
        "title": f"task {tid}",
        "status": status,
        "priority": "medium",
        "dependencies": list(deps),
        "details": "",
        "testStrategy": "x",
        "subtasks": [],
    }
    base.update(extra)
    return base


def kernel(tid, verify, deps=(), **extra):
    return mk(tid, deps, runner="kernel", verify=verify, **extra)


class Repo:
    def __init__(self, root: Path):
        self.root = root
        self.file = root / "tasks.json"
        self.allowlist = root / "allowlist.json"
        self.set_allowlist([["python3", "-c"]])

    def set_allowlist(self, allowed, **extra):
        self.allowlist.write_text(json.dumps({"allowed": allowed, **extra}), encoding="utf-8")

    def write(self, task_list):
        self.file.write_text(
            json.dumps({"master": {"tasks": task_list, "metadata": {}}}), encoding="utf-8"
        )

    def tasks(self):
        return json.loads(self.file.read_text(encoding="utf-8"))["master"]["tasks"]

    def task(self, tid):
        return next(t for t in self.tasks() if t["id"] == tid)

    def main(self, *args):
        return run.main(
            [
                "--root",
                str(self.root),
                "--file",
                str(self.file),
                "--allowlist",
                str(self.allowlist),
                *args,
            ]
        )

    def runs(self):
        log = self.root / ".taskmaster" / "runs" / "runs.jsonl"
        return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]

    def exists(self, name):
        return (self.root / name).exists()


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


# ---------- validator ----------


def errors_for(*items):
    errors, _ = tasks.validate(list(items))
    return errors


def test_validate_rejects_unknown_runner():
    errs = errors_for(mk(1, runner="robot"))
    assert any("runner 'robot'" in e for e in errs)


@pytest.mark.parametrize("runner", ["kernel", "local", "model", "human"])
def test_validate_accepts_each_runner(runner):
    extra = {"verify": "pytest"} if runner == "kernel" else {}
    assert errors_for(mk(1, runner=runner, **extra)) == []


def test_validate_rejects_kernel_without_verify():
    assert any("needs a non-empty verify" in e for e in errors_for(mk(1, runner="kernel")))
    assert any(
        "needs a non-empty verify" in e for e in errors_for(mk(1, runner="kernel", verify="  "))
    )


def test_validate_rejects_kernel_with_manual_verify():
    assert any("manual" in e for e in errors_for(mk(1, runner="kernel", verify="manual")))


def test_validate_rejects_kernel_subtask():
    parent = mk(
        1,
        subtasks=[
            {
                "id": 1,
                "title": "s",
                "status": "pending",
                "dependencies": [],
                "testStrategy": "x",
                "runner": "kernel",
                "verify": "pytest",
            }
        ],
    )
    assert any("top-level" in e for e in errors_for(parent))


def test_validate_checks_optional_step_fields():
    errs = errors_for(
        kernel(1, "pytest", verifyTimeoutSeconds=0, verifyExpectedExit="0", verifyCwd="")
    )
    assert len(errs) == 3


def test_validate_keeps_unknown_keys_and_missing_runner_ok():
    assert errors_for(mk(1, somethingElse=5)) == []


def test_real_task_list_is_valid():
    path = Path(__file__).resolve().parent.parent / ".taskmaster" / "tasks" / "tasks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    errors, _ = tasks.validate(data["master"]["tasks"])
    assert errors == []


# ---------- plan ----------


def test_plan_lists_only_pending_kernel_tasks_in_dependency_order(repo, capsys):
    repo.write(
        [
            kernel(1, MARK.format("a"), status="done"),
            mk(2),  # model by default
            mk(3, runner="human", verify="manual"),
            kernel(4, MARK.format("b"), deps=[1]),
            kernel(5, MARK.format("c"), deps=[4]),
        ]
    )
    assert repo.main("plan") == 0
    lines = [ln.split()[0] for ln in capsys.readouterr().out.splitlines()]
    assert lines == ["4", "5"]
    assert not repo.exists("a")  # plan runs nothing


def test_kernel_order_puts_dependencies_first_even_with_higher_ids():
    order = run.kernel_order([kernel(2, "x", deps=[7]), kernel(7, "x")])
    assert [t["id"] for t in order] == [7, 2]


def test_plan_reports_blocked_and_refused(repo, capsys):
    repo.write([mk(1), kernel(2, MARK.format("a"), deps=[1]), kernel(3, "rm -rf x")])
    assert repo.main("plan") == 1  # task 3 is refused (not on the allowlist)
    out = capsys.readouterr().out
    assert "blocked by 1" in out
    assert "REFUSED" in out


def test_plan_with_nothing_pending(repo, capsys):
    repo.write([mk(1)])
    assert repo.main("plan") == 0
    assert "no pending kernel tasks" in capsys.readouterr().out


# ---------- run ----------


def test_run_marks_done_only_after_verify_passes(repo, capsys):
    repo.write([kernel(1, MARK.format("ran1"))])
    assert repo.main("run") == 0
    assert repo.exists("ran1")
    t = repo.task(1)
    assert t["status"] == "done"
    assert "kernel run passed: exit 0" in t["details"]
    row = repo.runs()[0]
    assert row["task"] == 1 and row["passed"] is True
    assert row["stdout_sha256"] == hashlib.sha256(b"").hexdigest()


def test_run_records_sha256_of_full_stdout_and_tails(repo):
    # write raw bytes so the expected hash is the same on every OS (print adds CRLF on Windows)
    code = "import sys; sys.stdout.buffer.write(b''.join(b'%d\\n' % i for i in range(50)))"
    repo.write([kernel(1, f'python3 -c "{code}"')])
    assert repo.main("run") == 0
    expected = "".join(f"{i}\n" for i in range(50)).encode()
    row = repo.runs()[0]
    assert row["stdout_sha256"] == hashlib.sha256(expected).hexdigest()
    assert row["stdout_tail"] == [str(i) for i in range(30, 50)]


def test_run_stops_at_first_failure_and_does_not_mark_done(repo, capsys):
    repo.write(
        [
            kernel(1, MARK.format("first")),
            kernel(2, "python3 -c \"import sys; print('boom'); sys.exit(1)\"", deps=[1]),
            kernel(3, MARK.format("third"), deps=[1]),
        ]
    )
    assert repo.main("run") == 1
    assert repo.task(1)["status"] == "done"
    assert repo.task(2)["status"] == "pending"
    assert repo.task(3)["status"] == "pending"
    assert repo.exists("first") and not repo.exists("third")
    out = capsys.readouterr().out
    assert "boom" in out and "FAILED task 2" in out
    handoff = (repo.root / "handoff" / "TASK-2.md").read_text(encoding="utf-8")
    assert "Suggested runner: model" in handoff and "boom" in handoff


def test_failed_run_leaves_tasks_file_untouched(repo):
    repo.write([kernel(1, 'python3 -c "import sys; sys.exit(4)"')])
    before = repo.file.read_bytes()
    assert repo.main("run") == 1
    assert repo.file.read_bytes() == before


def test_expected_exit_code_is_honoured(repo):
    repo.write([kernel(1, 'python3 -c "import sys; sys.exit(3)"', verifyExpectedExit=3)])
    assert repo.main("run") == 0
    assert repo.task(1)["status"] == "done"


def test_timeout_fails_the_step(repo, capsys):
    repo.write([kernel(1, 'python3 -c "import time; time.sleep(30)"', verifyTimeoutSeconds=1)])
    assert repo.main("run") == 1
    assert "timeout after 1s" in capsys.readouterr().out
    assert repo.task(1)["status"] == "pending"


@pytest.mark.parametrize(
    "verify",
    [
        "python3 -c pass && python3 -c pass",
        "python3 -c pass | python3 -c pass",
        "python3 -c pass ; python3 -c pass",
        "python3 -c pass > out.txt",
        "python3 -c pass $(python3 -c pass)",
        "python3 -c pass `python3 -c pass`",
    ],
)
def test_command_with_shell_operator_is_refused(repo, verify):
    repo.write([kernel(1, verify)])
    assert repo.main("run") == 1
    assert repo.task(1)["status"] == "pending"
    assert not repo.exists("out.txt")


def test_semicolon_inside_a_quoted_argument_is_just_text(repo):
    code = "import sys; open('quoted', 'w').close()"
    repo.write([kernel(1, f'python3 -c "{code}"')])
    assert repo.main("run") == 0
    assert repo.exists("quoted")


def test_command_not_on_allowlist_is_refused_and_not_run(repo, capsys):
    repo.set_allowlist([["uv", "run"]])
    repo.write([kernel(1, MARK.format("nope"))])
    assert repo.main("run") == 1
    assert not repo.exists("nope")
    assert "not on the allowlist" in capsys.readouterr().out
    assert (repo.root / "handoff" / "TASK-1.md").exists()


def test_allowlist_matches_by_prefix_only(repo):
    repo.set_allowlist([["python3", "-c", "pass"]])
    repo.write([kernel(1, MARK.format("x"))])
    assert repo.main("run") == 1


def test_missing_allowlist_fails_closed(repo, capsys):
    repo.allowlist.unlink()
    repo.write([kernel(1, MARK.format("x"))])
    assert repo.main("run") == 2
    assert not repo.exists("x")


def test_malformed_allowlist_fails_closed(repo):
    repo.allowlist.write_text('{"allowed": "python3"}', encoding="utf-8")
    repo.write([kernel(1, MARK.format("x"))])
    assert repo.main("run") == 2
    assert not repo.exists("x")


def test_cwd_outside_repo_is_refused(repo):
    repo.write([kernel(1, MARK.format("x"), verifyCwd="..")])
    assert repo.main("run") == 1
    assert repo.task(1)["status"] == "pending"


def test_cwd_inside_repo_is_used(repo):
    (repo.root / "sub").mkdir()
    repo.write([kernel(1, MARK.format("here"), verifyCwd="sub")])
    assert repo.main("run") == 0
    assert (repo.root / "sub" / "here").exists()


def test_non_kernel_tasks_are_never_run(repo):
    repo.write(
        [
            mk(1, verify=MARK.format("model")),
            mk(2, runner="human", verify="manual"),
            mk(3, runner="local", verify=MARK.format("local")),
        ]
    )
    assert repo.main("run") == 0
    assert not repo.exists("model") and not repo.exists("local")
    assert [t["status"] for t in repo.tasks()] == ["pending"] * 3


def test_only_refuses_a_non_kernel_task(repo):
    repo.write([mk(1, verify=MARK.format("model"))])
    assert repo.main("run", "--only", "1") == 2
    assert not repo.exists("model")


def test_blocked_task_is_skipped_not_run(repo, capsys):
    repo.write([mk(1), kernel(2, MARK.format("blocked"), deps=[1])])
    assert repo.main("run") == 0
    assert not repo.exists("blocked")
    assert "blocked by 1" in capsys.readouterr().out
    assert repo.task(2)["status"] == "pending"


def test_done_tasks_are_skipped_but_only_reverifies(repo):
    repo.write([kernel(1, MARK.format("again"), status="done")])
    assert repo.main("run") == 0
    assert not repo.exists("again")
    assert repo.main("run", "--only", "1") == 0
    assert repo.exists("again")
    assert "re-verified" in repo.task(1)["details"]


def test_from_starts_at_the_given_task(repo):
    repo.write(
        [
            kernel(1, MARK.format("one")),
            kernel(2, MARK.format("two")),
            kernel(3, MARK.format("three")),
        ]
    )
    assert repo.main("run", "--from", "2") == 0
    assert not repo.exists("one") and repo.exists("two") and repo.exists("three")
    assert repo.task(1)["status"] == "pending"


def test_from_rejects_an_unknown_task(repo):
    repo.write([kernel(1, MARK.format("one"))])
    assert repo.main("run", "--from", "9") == 2
    assert not repo.exists("one")


# ---------- checklist ----------


def test_checklist_is_generated_and_deterministic(repo):
    repo.write([kernel(1, MARK.format("a"), status="done"), mk(2), kernel(3, MARK.format("b"))])
    assert repo.main("checklist") == 0
    first = (repo.root / "DETERMINISTIC.md").read_bytes()
    assert first.startswith(run.GENERATED_MARK.encode())
    assert b"| [x] | 1 |" in first and b"| [ ] | 3 |" in first and b"| 2 |" not in first
    assert repo.main("checklist") == 0
    assert (repo.root / "DETERMINISTIC.md").read_bytes() == first


def test_checklist_will_not_overwrite_a_hand_written_file(repo):
    (repo.root / "DETERMINISTIC.md").write_text("# my spec\n", encoding="utf-8")
    repo.write([kernel(1, MARK.format("a"))])
    assert repo.main("checklist") == 2
    assert (repo.root / "DETERMINISTIC.md").read_text(encoding="utf-8") == "# my spec\n"
    assert repo.main("checklist", "--force") == 0


# ---------- no model code on the run path ----------


SIBLINGS = {"tasks", "extractors", "reduction"}  # tools/taskmaster modules on the run path


def imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules |= {n.name.split(".")[0] for n in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_run_path_imports_no_model_or_provider_code():
    allowed = set(sys.stdlib_module_names) | SIBLINGS
    for name in ("run.py", "tasks.py", "extractors.py", "reduction.py"):
        modules = imported_modules(TOOLS / name)
        assert modules <= allowed, f"{name}: non-stdlib imports: {sorted(modules - allowed)}"
        assert not any(m.startswith("jangus") for m in modules)
