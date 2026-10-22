# Linux development setup

This guide covers the Phase 2 package on Ubuntu, including Ubuntu 22.04. No daemon, open port, SSH tunnel, or remote-job service is installed in this phase.

Install Git and the pinned uv tool using the [official uv installation guidance](https://docs.astral.sh/uv/getting-started/installation/). If you have a suitable Python/pip environment, `python3 -m pip install --user uv==0.12.23` is another option. Do not use sudo pip or overwrite the distribution's managed Python. Ensure `uv --version` prints `0.12.23` and that `uv` is on PATH.

Ubuntu 22.04's system Python may be older than the supported range. uv can install a separate Python without replacing it:

```bash
uv python install 3.12
git clone --branch foundation/02-development-setup https://github.com/jenozu/jangus.git
cd jangus
uv sync --locked
uv run --locked jangus --version
uv run --locked jangus config validate --config config/jangus.example.yaml
```

The named branch is for this phase while under review; after merge, use the branch containing the merged work. For Python 3.13, install it and set `UV_PYTHON=3.13` for the session. Supported range is 3.12–3.13.

## Private configuration

```bash
mkdir -p "$HOME/.config/jangus"
if [ ! -e "$HOME/.config/jangus/jangus.yaml" ]; then
    cp config/jangus.example.yaml "$HOME/.config/jangus/jangus.yaml"
fi
export JANGUS_CONFIG="$HOME/.config/jangus/jangus.yaml"
uv run --locked jangus config validate
```

The commands preserve an existing config. Review its paths using the [configuration contract](../../config/README.md). Validation creates no data/workspace directories and contacts no service.

## Development checks

```bash
bash scripts/linux/check-setup.sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
uv run --locked python scripts/check_repository.py
uv build --no-build-isolation
uv run --locked python scripts/check_distribution.py
```

A GitHub Actions matrix checks Ubuntu 22.04 and Windows with Python 3.12 and 3.13. Actual VPS service setup and resilience validation remain Phase 7 work.
