# Windows development setup

Phase 2 supports package installation and configuration validation. Agent execution, Taskmaster, local models, and VPS jobs arrive later.

Use Windows PowerShell or PowerShell 7 with Git and Python 3.12 or 3.13 installed. Python 3.12 is the default in `.python-version`. No administrator session is required for these steps.

## Install the tool and checkout

Install the pinned uv version into your existing Python installation:

```powershell
py -3.12 -m pip install --user uv==0.12.23
py -3.12 -m uv --version
$JangusPythonScripts = py -3.12 -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"
$env:PATH = "$JangusPythonScripts;$env:PATH"
uv --version
git clone --branch foundation/02-development-setup https://github.com/jenozu/jangus.git
Set-Location jangus
py -3.12 -m uv sync --locked
py -3.12 -m uv run --locked jangus --version
py -3.12 -m uv run --locked jangus config validate --config config/jangus.example.yaml
```

The branch command works while this phase is under review; after it merges use the branch containing the merged work. In an existing checkout, fetch and switch to that branch after preserving your local changes.

If Python 3.13 is installed instead, replace `py -3.12` with `py -3.13` and set `$env:UV_PYTHON = "3.13"` before syncing. Supported range is 3.12–3.13; newer versions are not yet claimed.

The commands add the Python user Scripts directory to PATH for this session so the check scripts can find uv. Using `py -m uv` also works for direct commands. No virtual-environment activation is needed.

## Private configuration

```powershell
$JangusConfigDir = Join-Path $env:LOCALAPPDATA "Jangus"
New-Item -ItemType Directory -Force -Path $JangusConfigDir | Out-Null
$JangusConfigFile = Join-Path $JangusConfigDir "jangus.yaml"
if (-not (Test-Path $JangusConfigFile)) {
    Copy-Item config/jangus.example.yaml $JangusConfigFile
}
$env:JANGUS_CONFIG = $JangusConfigFile
py -3.12 -m uv run --locked jangus config validate
```

This creates only the private configuration directory/file, preserving an existing file. The example's data/workspace paths use your home directory and remain uncreated. Keep both outside the checkout and outside one another. See the [configuration contract](../../config/README.md) for settings and path syntax.

## Development checks

```powershell
py -3.12 -m uv run --locked ruff check .
py -3.12 -m uv run --locked ruff format --check .
py -3.12 -m uv run --locked pytest
py -3.12 -m uv run --locked python scripts/check_repository.py
py -3.12 -m uv build --no-build-isolation
py -3.12 -m uv run --locked python scripts/check_distribution.py
```

The distribution check requires the `uv` executable on PATH for its child commands. The shorter `scripts/windows/check-setup.ps1` also uses `uv` on PATH and checks locked installation, version, and example configuration. If script execution is restricted, run the commands above directly rather than changing system execution policy.

GitHub Actions covers Windows on Python 3.12 and 3.13. That runner evidence does not establish that the owner's desktop, local model, or Obsidian vault has been tested.
