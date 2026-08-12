param(
  [string]$TaskName = "AI Strategy Codex Proxy"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
  Write-Host "Scheduled task not found: $TaskName"
  return
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task: $TaskName"
