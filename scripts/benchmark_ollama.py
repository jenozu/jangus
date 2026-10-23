"""Loopback-only Ollama synthetic JSON benchmark. No real task data."""
import argparse
import json
import statistics
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL = "http://127.0.0.1:11434/api/chat"
METRICS = (
    "prompt_eval_count",
    "eval_count",
    "prompt_eval_duration",
    "eval_duration",
    "load_duration",
)
CASES = (
    ("arithmetic", "Answer with JSON object: integer answer is 7 plus 5.", {"answer": 12}),
    (
        "selection",
        "Answer with JSON object: choice is alphabetically first of pear, apple, plum.",
        {"choice": "apple"},
    ),
    ("boolean", "Answer with JSON object: safe is boolean true.", {"safe": True}),
)


def sample(model, name, prompt, expected, timeout):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "keep_alive": "0",
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 128},
    }
    request = Request(
        URL,
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ValueError("bad_status")
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized")
        response = json.loads(raw)
        answer = json.loads(response["message"]["content"])
        result = {"case": name, "passed": type(answer) is dict and answer == expected}
        for key in METRICS:
            if type(response.get(key)) is int and response[key] >= 0:
                result[key] = response[key]
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
        result = {"case": name, "passed": False, "error": "unavailable_or_invalid"}
    result["elapsed_seconds"] = round(time.perf_counter() - start, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if not args.model.strip() or not 1 <= args.timeout <= 600:
        parser.error("invalid model or timeout")
    output = args.output.expanduser().resolve()
    if output.exists() or any(
        (p / ".git").exists() for p in (output.parent, *output.parent.parents)
    ):
        parser.error("output must be a new file outside any git checkout")
    results = [
        sample(args.model, name, prompt, expected, args.timeout)
        for _ in range(3)
        for name, prompt, expected in CASES
    ]
    report = {
        "kind": "synthetic_smoke_only",
        "model": args.model,
        "endpoint": URL,
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "median_elapsed_seconds": statistics.median(r["elapsed_seconds"] for r in results),
        "samples": results,
        "hardware_memory_not_measured": True,
        "real_workflow_not_measured": True,
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(f"Synthetic JSON checks: {report['passed']}/{report['total']}")
    return int(report["passed"] != report["total"])


if __name__ == "__main__":
    raise SystemExit(main())
