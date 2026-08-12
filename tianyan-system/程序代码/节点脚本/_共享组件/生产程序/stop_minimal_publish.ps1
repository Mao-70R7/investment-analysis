$ErrorActionPreference = "Stop"

$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$packageParent = Split-Path -Parent $packageRoot
$runtimeState = Join-Path $packageParent ".minimal_publish_runtime\processes.json"
$legacyRuntimeState = Join-Path $packageRoot ".runtime\processes.json"
if (-not (Test-Path -LiteralPath $runtimeState) -and (Test-Path -LiteralPath $legacyRuntimeState)) {
  $runtimeState = $legacyRuntimeState
}
if (-not (Test-Path -LiteralPath $runtimeState)) {
  Write-Host "No minimal publish runtime state found."
  exit 0
}

$state = Get-Content -LiteralPath $runtimeState -Raw -Encoding utf8 | ConvertFrom-Json
foreach ($pidValue in @($state.sitePid, $state.proxyPid)) {
  if ($pidValue) {
    Stop-Process -Id ([int]$pidValue) -Force -ErrorAction SilentlyContinue
  }
}
Remove-Item -LiteralPath $runtimeState -Force -ErrorAction SilentlyContinue
Write-Host "Minimal publish processes stopped."
