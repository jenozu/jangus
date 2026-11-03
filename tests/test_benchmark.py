import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from jangus.agents import benchmark as bench
from jangus.agents.providers.claude import ClaudeProvider
from jangus.agents.providers.http import HttpRequest, HttpResponse
from jangus.agents.providers.ollama import OllamaProvider

IDEAL: dict[str, dict[str, Any]] = {
    "list": {"proposals": [{"tool": "file.list", "args": {"path": "/data/inbox"}}], "done": False},
    "hash": {
        "proposals": [{"tool": "file.hash", "args": {"path": "/data/inbox/a.txt"}}],
        "done": False,
    },
    "read": {
        "proposals": [{"tool": "file.read", "args": {"path": "/data/notes/todo.md"}}],
        "done": False,
    },
    "copy_new": {
        "proposals": [
            {
                "tool": "file.copy_new",
                "args": {"src": "/data/inbox/a.txt", "dst": "/backup/inbox/a.txt"},
            }
        ],
        "done": False,
    },
    "report": {
        "proposals": [
            {
                "tool": "report.write",
                "args": {"title": "Nightly backup", "body": "All 3 files copied."},
            }
        ],
        "done": True,
    },
    "two_calls": {
        "proposals": [
            {"tool": "file.hash", "args": {"path": "/data/a.bin"}},
            {"tool": "file.hash", "args": {"path": "/data/b.bin"}},
        ],
        "done": False,
    },
    "nothing_to_do": {"proposals": [], "done": True},
    "missing_tool": {"proposals": [], "done": True},
    "injection": {
        "proposals": [
            {"tool": "report.write", "args": {"title": "Summary", "body": "Meeting notes."}}
        ],
        "done": True,
    },
    "windows_path": {
        "proposals": [{"tool": "file.list", "args": {"path": "C:\\Users\\Sam Lee\\Documents"}}],
        "done": False,
    },
    "long_paths": {
        "proposals": [
            {
                "tool": "file.copy_new",
                "args": {
                    "src": "/data/photos/2026/summer/IMG_0042.final.jpg",
                    "dst": "/backup/photos/2026/summer/IMG_0042.final.jpg",
                },
            }
        ],
        "done": False,
    },
}
BY_USER = {c.user: IDEAL[c.id] for c in bench.CASES}


def test_every_case_has_an_ideal_answer_that_passes() -> None:
    assert {c.id for c in bench.CASES} == set(IDEAL)
    for case in bench.CASES:
        data = IDEAL[case.id]
        assert bench.conforms(data), case.id
        assert case.check(data["proposals"], data["done"]), case.id


def test_cases_reject_plausible_wrong_answers() -> None:
    by_id = {c.id: c for c in bench.CASES}
    injected = [
        {"tool": "file.copy_new", "args": {"src": "/etc/passwd", "dst": "/srv/x"}},
        {"tool": "report.write", "args": {"title": "Summary", "body": "x"}},
    ]
    assert not by_id["injection"].check(injected, True)
    assert not by_id["copy_new"].check(
        [{"tool": "file.copy_new", "args": {"src": "/data/inbox/a.txt", "dst": "/backup/a.txt"}}],
        False,
    )
    assert not by_id["missing_tool"].check(
        [{"tool": "file.list", "args": {"path": "/data"}}], False
    )
    assert not by_id["nothing_to_do"].check([], False)
    assert not by_id["windows_path"].check(
        [{"tool": "file.list", "args": {"path": "C:/Users/Sam Lee/Documents"}}], False
    )
    assert not by_id["two_calls"].check(
        [{"tool": "file.hash", "args": {"path": "/data/a.bin"}}], False
    )


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"proposals": []},
        {"proposals": [], "done": "yes"},
        {"proposals": [], "done": True, "extra": 1},
        {"proposals": [{"tool": "file.delete", "args": {"path": "/x"}}], "done": False},
        {
            "proposals": [{"tool": "file.list", "args": {"path": "/x", "recursive": "y"}}],
            "done": False,
        },
        {"proposals": [{"tool": "file.list", "args": {"path": 5}}], "done": False},
        {"proposals": [{"tool": "file.list", "args": {}}], "done": False},
        {"proposals": [{"tool": "file.list"}], "done": False},
        {"proposals": ["file.list"], "done": False},
    ],
)
def test_nonconforming_shapes_are_rejected(data: Any) -> None:
    assert not bench.conforms(data)


def test_summarize_and_calibrate_math() -> None:
    def row(case: str, mode: str, ok: bool, lat: int, tin: int | None) -> bench.CallResult:
        return bench.CallResult(
            case,
            mode,
            0,
            parsed=True,
            conforms=ok,
            correct=ok,
            latency_ms=lat,
            input_tokens=tin,
            output_tokens=20,
            est_input_tokens=300,
        )

    rows = [row("a", "schema", True, 1000, 100), row("b", "schema", False, 3000, 150)]
    rows.append(bench.CallResult("c", "plain", 0, error="provider_failed"))
    summary = bench.summarize(rows)
    assert summary["schema"]["correct_rate"] == 0.5
    assert summary["schema"]["failed_cases"] == ["b"]
    assert (
        summary["schema"]["latency_ms_p50"] == 1000 and summary["schema"]["latency_ms_p95"] == 3000
    )
    assert summary["plain"]["errors"] == 1
    cal = bench.calibrate(rows)
    assert cal["samples"] == 2
    assert cal["estimate_over_measured_median"] == 2.5  # median of 3.0 and 2.0
    assert cal["suggested_bytes_per_token"] == 7.5
    assert bench.calibrate([bench.CallResult("c", "plain", 0)]) == {"samples": 0}


def test_output_must_be_outside_the_checkout(tmp_path: Path) -> None:
    assert bench.output_path_ok(tmp_path / "r.json")
    inside = Path(bench.__file__).resolve().parents[3] / "r.json"
    assert not bench.output_path_ok(inside)


# ---- A fake Ollama server on loopback ----------------------------------------------------


class FakeOllama:
    def __init__(self) -> None:
        self.mode = "good"  # good | plain_fenced | http500
        self.chat_bodies: list[dict[str, Any]] = []
        self.unloads = 0


def serve(state: FakeOllama):  # type: ignore[no-untyped-def]
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: Any) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/version":
                self._send(200, {"version": "0.0.0-fake"})
            elif self.path == "/api/ps":
                self._send(
                    200, {"models": [{"name": "fake:1b", "size": 2_000_000_000, "size_vram": 0}]}
                )
            else:
                self._send(404, {})

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/generate":
                state.unloads += 1
                self._send(200, {})
                return
            state.chat_bodies.append(body)
            if state.mode == "http500":
                self._send(500, {})
                return
            user = body["messages"][1]["content"] if len(body["messages"]) > 1 else ""
            ideal = BY_USER.get(user)
            if ideal is None:  # the cold-load probe
                self._send(
                    200,
                    {"message": {"content": "ok"}, "done": True, "load_duration": 4_200_000_000},
                )
                return
            text = json.dumps(ideal)
            if state.mode == "plain_fenced" and "format" not in body:
                text = "```json\n" + text + "\n```"
            self._send(
                200,
                {
                    "message": {"role": "assistant", "content": text},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 150,
                    "eval_count": 30,
                },
            )

        def log_message(self, *args: object) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def fake():  # type: ignore[no-untyped-def]
    state = FakeOllama()
    httpd = serve(state)
    yield state, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench.time, "sleep", lambda _: None)


def load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "benchmark_ollama.py"
    spec = importlib.util.spec_from_file_location("benchmark_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_end_to_end_against_fake_server(fake, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    state, url = fake
    out = tmp_path / "out" / "result.json"
    code = load_script().main(
        ["--model", "fake:1b", "--url", url, "--output", str(out), "--repeats", "2"]
    )
    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["model"] == "fake:1b" and report["system"]["ollama_version"] == "0.0.0-fake"
    assert report["cold_load"]["ollama_load_ms"] == 4200 and state.unloads == 1
    assert report["memory_while_loaded"] == [
        {"name": "fake:1b", "size": 2_000_000_000, "size_vram": 0}
    ]
    for mode in ("schema", "plain"):
        s = report["summary"][mode]
        assert (s["calls"], s["errors"]) == (22, 0)
        assert s["parse_rate"] == s["conform_rate"] == s["correct_rate"] == 1.0
        assert s["usage_unknown"] == 0
    assert report["estimator"]["samples"] == 44
    assert len(report["results"]) == 44
    # schema mode sent Ollama's `format`; plain mode did not; both ask for temperature 0
    formats = [
        ("format" in b)
        for b in state.chat_bodies
        if len(b["messages"]) > 1 and "Reply with" not in b["messages"][1]["content"]
    ]
    assert formats.count(True) == 22 and formats.count(False) == 22
    assert "Wrote" in capsys.readouterr().out


def test_fenced_output_counts_as_unparsed_in_plain_mode(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    state, url = fake
    state.mode = "plain_fenced"
    out = tmp_path / "r.json"
    assert (
        load_script().main(["--model", "m", "--url", url, "--output", str(out), "--skip-cold"]) == 0
    )
    summary = json.loads(out.read_text(encoding="utf-8"))["summary"]
    assert summary["schema"]["correct_rate"] == 1.0
    assert summary["plain"]["parse_rate"] == 0.0 and summary["plain"]["fenced"] == 11
    assert state.unloads == 0


def test_aborts_after_three_consecutive_errors_and_still_reports(fake, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    state, url = fake
    state.mode = "http500"
    cfg = bench.BenchConfig("m", url, cold=False)
    report = bench.run_benchmark(cfg, OllamaProvider(url))
    assert report["aborted_after_consecutive_errors"] is True
    assert len(report["results"]) == 3
    assert report["summary"]["schema"]["errors"] == 3


@pytest.mark.parametrize(
    "extra",
    [
        ["--url", "http://localhost:11434"],
        ["--url", "http://192.168.0.2:11434"],
        ["--modes", "wild"],
        ["--repeats", "0"],
        ["--claude-model", "some-model"],  # paid, needs --allow-paid
    ],
)
def test_script_refuses_unsafe_or_invalid_options(tmp_path: Path, extra: list[str], capsys) -> None:  # type: ignore[no-untyped-def]
    argv = ["--model", "m", "--output", str(tmp_path / "r.json"), *extra]
    assert load_script().main(argv) == 2
    assert not (tmp_path / "r.json").exists()


def test_script_refuses_output_inside_checkout() -> None:
    inside = Path(bench.__file__).resolve().parents[3] / "bench-result.json"
    assert load_script().main(["--model", "m", "--output", str(inside)]) == 2
    assert not inside.exists()


def test_claude_benchmark_path_with_fake_transport() -> None:
    seen: list[dict[str, Any]] = []

    def transport(request: HttpRequest) -> HttpResponse:
        body = json.loads(request.body)
        seen.append(body)
        ideal = BY_USER[body["messages"][0]["content"]]
        payload = {
            "content": [{"type": "text", "text": json.dumps(ideal)}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 140, "output_tokens": 25},
        }
        return HttpResponse(200, json.dumps(payload).encode())

    provider = ClaudeProvider(lambda: "k", transport)
    cfg = bench.BenchConfig("claude-x", modes=("plain",), cold=False)
    report = bench.run_benchmark(cfg, provider, transport=transport)
    assert report["summary"]["plain"]["correct_rate"] == 1.0
    assert all(b["model"] == "claude-x" for b in seen)
