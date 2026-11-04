<#
.SYNOPSIS
  Registers the Windows scheduled task that runs `jangus tick` (owner task 21).

.DESCRIPTION
  NOT YET RUN ON A REAL PC. Review it, run it in a normal (non-admin) PowerShell from the
  repository folder, and tell the agent what happened. It only creates or replaces one task
  named "Jangus tick" that runs every 15 minutes for the current user while logged on. It
  does not store secrets, change power settings or touch any other task.

.PARAMETER Config
  Absolute path to your jangus.toml (outside the checkout).

.PARAMETER Remove
  Remove the task instead of creating it.

.EXAMPLE
  .\scripts\install_task.ps1 -Config C:\Users\you\jangus\jangus.toml
#>
param(
  [Parameter(Mandatory = $true)][string]$Config,
  [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "Jangus tick"

if ($Remove) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Removed '$TaskName'."
  return
}

if (-not [System.IO.Path]::IsPathRooted($Config) -or -not (Test-Path -LiteralPath $Config)) {
  throw "Config must be an existing absolute path: $Config"
}
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Uv = (Get-Command uv -ErrorAction Stop).Source

# cmd /c lets the task set JANGUS_CONFIG for this one command only.
$Command = "/c set `"JANGUS_CONFIG=$Config`" && `"$Uv`" run --locked jangus tick"
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $Command -WorkingDirectory $Repo
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
  -Description "Runs Jangus agents that are due (jangus tick)." -Force | Out-Null
Write-Host "Registered '$TaskName': every 15 minutes, working directory $Repo."
Write-Host "Check it with: Get-ScheduledTaskInfo -TaskName '$TaskName'"
