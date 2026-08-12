param(
  [string]$TaskName = "AI Strategy Codex Proxy",
  [switch]$NoStart
)

$ErrorActionPreference = "Stop"

$startScript = Join-Path $PSScriptRoot "start_ai_strategy_codex_proxy.ps1"
if (-not (Test-Path $startScript)) {
  throw "Start script not found: $startScript"
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`""

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null

if (-not $NoStart) {
  Start-ScheduledTask -TaskName $TaskName
}

Write-Host "Installed scheduled task: $TaskName"
Write-Host "User: $currentUser"
Write-Host "Start script: $startScript"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
