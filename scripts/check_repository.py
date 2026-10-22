"""Check tracked document links, example retention, and private-data exclusions."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True)


def main():
    tracked = set(git("ls-files").splitlines())
    failures = []
    links = 0
    for name in sorted(tracked):
        path = ROOT / name
        if path.suffix == ".md":
            content = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
                if "://" in target or target.startswith("#"):
                    continue
                links += 1
                resolved = (path.parent / target.split("#")[0]).resolve()
                if (
                    not resolved.is_relative_to(ROOT)
                    or resolved.relative_to(ROOT).as_posix() not in tracked
                ):
                    failures.append(f"Invalid document link in {name}: {target}")
        if re.search(
            r"(^|/)(runtime|artifacts|logs|exports|workspaces|vault|secrets|credentials)/", name
        ):
            failures.append(f"Private-data directory is tracked: {name}")
        if name != ".env.example" and (
            Path(name).name.startswith(".env") or re.search(r"\.(db|sqlite3?|pem|key|gguf)$", name)
        ):
            failures.append(f"Private-data file is tracked: {name}")
    excluded = [
        ".env",
        ".env.local",
        "config/jangus.local.yaml",
        "runtime/state.db",
        "state.sqlite3-wal",
        "logs/run.jsonl",
        "artifacts/private.txt",
        "exports/diagnostic.zip",
        "vault/private.md",
        "workspaces/input.txt",
        "credentials/service.json",
        "model.gguf",
        ".venv/pyvenv.cfg",
    ]
    for name in excluded:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "--no-index", "-q", name], check=False
        )
        if result.returncode != 0:
            failures.append(f"Expected ignore rule missing: {name}")
    for name in [".env.example", "config/jangus.example.yaml", "uv.lock", "rules.md"]:
        if name not in tracked:
            failures.append(f"Required tracked file missing: {name}")
    if "RULES.md" in tracked or "[rules.md](rules.md)" not in (ROOT / "AGENTS.md").read_text():
        failures.append("AGENTS.md must reference lowercase rules.md")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"PASS: {links} relative links; tracked examples; private-data ignore rules.")


if __name__ == "__main__":
    main()
