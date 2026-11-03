"""Run the local model benchmark. OWNER RUNS THIS on their own PC.

    uv run --locked python scripts/benchmark_ollama.py --model TAG --output RESULTS.json

Talks only to a loopback Ollama (default http://127.0.0.1:11434). Writes one JSON file
outside the Git checkout. With --claude-model it also benchmarks that Claude model (paid, a
few cents) and needs --allow-paid plus the key in JANGUS_SECRET_ANTHROPIC_API_KEY.
"""

import argparse
import json
import sys
from pathlib import Path

from jangus.agents.benchmark import BenchConfig, output_path_ok, run_benchmark
from jangus.agents.providers.claude import ClaudeProvider
from jangus.agents.providers.ollama import OllamaProvider
from jangus.config import check_loopback_url
from jangus.kernel.secrets import EnvBackend


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Benchmark a local Ollama model for Jangus.")
    p.add_argument("--model", required=True, help="exact Ollama tag, as shown by `ollama list`")
    p.add_argument("--url", default="http://127.0.0.1:11434")
    p.add_argument(
        "--output", required=True, type=Path, help="JSON file; must be outside the checkout"
    )
    p.add_argument("--repeats", type=int, default=1, help="passes over all cases per mode")
    p.add_argument("--modes", default="schema,plain", help="schema (Ollama format) and/or plain")
    p.add_argument("--timeout", type=float, default=300.0, help="seconds per request")
    p.add_argument("--skip-cold", action="store_true", help="do not unload the model first")
    p.add_argument("--claude-model", help="also benchmark this Claude model (paid)")
    p.add_argument("--allow-paid", action="store_true", help="confirm the paid Claude calls")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    modes = tuple(m for m in args.modes.split(",") if m)
    if not modes or not set(modes) <= {"schema", "plain"} or args.repeats < 1:
        print(
            "error: --modes must be schema and/or plain; --repeats must be at least 1",
            file=sys.stderr,
        )
        return 2
    try:
        check_loopback_url(args.url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not output_path_ok(args.output):
        print(
            "error: --output is inside the Git checkout; choose a path outside it", file=sys.stderr
        )
        return 2
    if args.claude_model and not args.allow_paid:
        print(
            "error: --claude-model makes paid API calls; add --allow-paid to confirm",
            file=sys.stderr,
        )
        return 2

    def progress(line: str) -> None:
        print(line, flush=True)

    base = args.url.rstrip("/")
    cfg = BenchConfig(args.model, base, args.repeats, modes, args.timeout, cold=not args.skip_cold)
    report = run_benchmark(cfg, OllamaProvider(base), progress=progress)
    if args.claude_model:
        env = EnvBackend()
        claude_cfg = BenchConfig(
            args.claude_model, base, args.repeats, ("plain",), args.timeout, cold=False
        )
        provider = ClaudeProvider(lambda: env.get("anthropic_api_key"))
        report["claude"] = run_benchmark(claude_cfg, provider, progress=progress)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({"summary": report["summary"], "cold_load": report.get("cold_load")}, indent=2)
    )
    print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
