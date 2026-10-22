#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
uv sync --locked
uv run --locked jangus --version
uv run --locked jangus config validate --config config/jangus.example.yaml
