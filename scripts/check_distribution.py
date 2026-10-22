"""Install the built wheel with locked runtime dependencies in an isolated environment."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def main():
    wheels = sorted((ROOT / "dist").glob("jangus-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected one wheel in dist; remove stale build outputs first.")
    with tempfile.TemporaryDirectory(prefix="jangus-wheel-") as temporary:
        directory = Path(temporary)
        environment = directory / "venv"
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        requirements = directory / "requirements.txt"
        run(
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            str(requirements),
            cwd=ROOT,
        )
        run("uv", "venv", "--python", sys.executable, str(environment), cwd=directory)
        run(
            "uv",
            "pip",
            "sync",
            "--python",
            str(python),
            "--require-hashes",
            str(requirements),
            cwd=directory,
        )
        run(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--no-deps",
            str(wheels[0]),
            cwd=directory,
        )
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("VIRTUAL_ENV", None)
        env.pop("JANGUS_CONFIG", None)
        run(str(python), "-I", "-m", "jangus", "--version", cwd=directory, env=env)
        run(
            str(python),
            "-I",
            "-m",
            "jangus",
            "config",
            "validate",
            "--config",
            str(ROOT / "config/jangus.example.yaml"),
            cwd=directory,
            env=env,
        )
        print("PASS: wheel imported and CLI validated outside the source checkout.")


if __name__ == "__main__":
    main()
