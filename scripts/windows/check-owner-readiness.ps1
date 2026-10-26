# Read-only user-machine prerequisites; never connects to provider or modifies configuration.
# Run from a reviewed Jangus checkout. Prints presence checks only, not local paths or model tags.
$ErrorActionPreference = "Stop"

function Has-Tool([string] $Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

$hasPython = $false
if (Has-Tool "py") {
    & py -3.12 --version > $null 2>&1
    $hasPython = $LASTEXITCODE -eq 0
    if (-not $hasPython) {
        & py -3.13 --version > $null 2>&1
        $hasPython = $LASTEXITCODE -eq 0
    }
}

$hasGit = Has-Tool "git"
$hasUv = Has-Tool "uv"
$hasOllama = Has-Tool "ollama"
$hasSsh = Has-Tool "ssh"
$hasCheckout = (Test-Path -LiteralPath (Join-Path (Get-Location) "rules.md")) -and
    (Test-Path -LiteralPath (Join-Path (Get-Location) "pyproject.toml"))

$payload = [ordered]@{
    schema = 1
    provenance = "read_only_windows_prerequisite_report"
    git_installed = $hasGit
    python_312_or_313_available = $hasPython
    uv_installed = $hasUv
    ollama_command_installed = $hasOllama
    ssh_command_installed = $hasSsh
    jangus_checkout_detected = [bool]$hasCheckout
    model_benchmark_completed = "not_evaluated"
    actual_ollama_model_selected = "not_evaluated"
    vault_reviewer_enrolled = "not_evaluated"
    vps_host_authorized = "not_evaluated"
    owner_mvp_accepted = "not_evaluated"
    changes_made = $false
}
$payload | ConvertTo-Json -Depth 3
