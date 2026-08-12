param(
  [int]$Port = 7676,
  [switch]$NoBrowser,
  [switch]$EnableCodexFallback
)

$ErrorActionPreference = "Stop"

$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$packageParent = Split-Path -Parent $packageRoot
# Runtime state must stay outside the generated package. Keeping a process cwd
# or an open log handle below packageRoot prevents atomic replacement on Windows.
$runtimeDir = Join-Path $packageParent ".minimal_publish_runtime"
$logDir = Join-Path $runtimeDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$proxyStart = Join-Path $PSScriptRoot "start_ai_strategy_codex_proxy.ps1"
$serverScript = Join-Path $PSScriptRoot "serve_basic_data_site.py"
$proxyHealth = "http://127.0.0.1:8787/healthz"
$siteUrl = "http://127.0.0.1:$Port/basic_data/strategies.html"
$runtimeState = Join-Path $runtimeDir "processes.json"
$state = [ordered]@{ startedAt = (Get-Date).ToString("o"); proxyPid = $null; sitePid = $null; siteUrl = $siteUrl }

function Test-Http([string]$Url) {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
    return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
  } catch {
    return $false
  }
}

if ($EnableCodexFallback -and -not (Test-Http $proxyHealth)) {
  $proxyLog = Join-Path $logDir "ai_proxy.log"
  $proxyArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$proxyStart`""
  $proxyProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $proxyArgs -WorkingDirectory $runtimeDir -WindowStyle Hidden -PassThru -RedirectStandardOutput $proxyLog -RedirectStandardError (Join-Path $logDir "ai_proxy_error.log")
  $state.proxyPid = $proxyProcess.Id
  foreach ($attempt in 1..30) {
    if (Test-Http $proxyHealth) { break }
    Start-Sleep -Seconds 1
  }
  if (-not (Test-Http $proxyHealth)) {
    throw "AI strategy proxy did not become healthy. Check $proxyLog"
  }
}

if (-not (Test-Http $siteUrl)) {
  $python = (Get-Command python -ErrorAction Stop).Source
  $siteLog = Join-Path $logDir "site.log"
  $siteArgs = "-X utf8 `"$serverScript`" --host 127.0.0.1 --port $Port --directory `"$packageRoot`""
  $siteProcess = Start-Process -FilePath $python -ArgumentList $siteArgs -WorkingDirectory $runtimeDir -WindowStyle Hidden -PassThru -RedirectStandardOutput $siteLog -RedirectStandardError (Join-Path $logDir "site_error.log")
  $state.sitePid = $siteProcess.Id
  foreach ($attempt in 1..30) {
    if (Test-Http $siteUrl) { break }
    Start-Sleep -Seconds 1
  }
  if (-not (Test-Http $siteUrl)) {
    throw "Minimal publish site did not start. Check $siteLog"
  }
}

$state | ConvertTo-Json | Set-Content -LiteralPath $runtimeState -Encoding utf8
Write-Host "Minimal publish set: $siteUrl"
if ($EnableCodexFallback) {
  Write-Host "Codex fallback health: $proxyHealth"
} else {
  Write-Host "AI model: use the inner-deepseek configuration bundled with the page"
}
if (-not $NoBrowser) {
  Start-Process $siteUrl
}
