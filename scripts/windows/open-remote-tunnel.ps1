# Explicit, operator-managed loopback tunnel. No SSH host-key acceptance automation.
param(
    [Parameter(Mandatory = $true)][string]$HostAlias,
    [int]$LocalPort = 8765
)
$ErrorActionPreference = "Stop"
if ($HostAlias -notmatch '^[A-Za-z0-9][A-Za-z0-9._@-]{0,126}$' -or
    $HostAlias.StartsWith('-') -or $LocalPort -lt 1024 -or $LocalPort -gt 65535) {
    throw "Invalid SSH host alias or local port."
}
# The user must first verify the expected SSH host fingerprint out-of-band.
& ssh -N -o "BatchMode=yes" -o "StrictHostKeyChecking=yes" `
  -o "ExitOnForwardFailure=yes" -o "ClearAllForwardings=yes" `
  -L "127.0.0.1:${LocalPort}:127.0.0.1:8765" $HostAlias
if ($LASTEXITCODE -ne 0) {
    throw "Tunnel unavailable; verify the SSH host identity and local port."
}
