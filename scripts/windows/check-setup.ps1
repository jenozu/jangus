$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "../..")
try {
    & uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    & uv run --locked jangus --version
    if ($LASTEXITCODE -ne 0) { throw "Package import failed." }
    & uv run --locked jangus config validate --config config/jangus.example.yaml
    if ($LASTEXITCODE -ne 0) { throw "Example configuration validation failed." }
}
finally {
    Pop-Location
}
