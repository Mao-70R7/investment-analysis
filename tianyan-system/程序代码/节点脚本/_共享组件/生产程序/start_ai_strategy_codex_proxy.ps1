$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "ai_strategy_codex_proxy.mjs"
if (-not (Test-Path $scriptPath)) {
  throw "Proxy script not found: $scriptPath"
}

$defaultConfig = Resolve-Path (Join-Path $PSScriptRoot "..\basic_data\config\模型服务配置.js") -ErrorAction SilentlyContinue
if (-not $env:AI_STRATEGY_MODEL_CONFIG -and $defaultConfig) {
  $env:AI_STRATEGY_MODEL_CONFIG = $defaultConfig.Path
}
if (-not $env:AI_STRATEGY_CODEX_MODEL) {
  $env:AI_STRATEGY_CODEX_MODEL = "gpt-5.4-mini"
}
if (-not $env:AI_STRATEGY_CODEX_SERVICE_TIER) {
  $env:AI_STRATEGY_CODEX_SERVICE_TIER = "fast"
}
if (-not $env:AI_STRATEGY_CODEX_REASONING) {
  $env:AI_STRATEGY_CODEX_REASONING = "low"
}
if (-not $env:AI_STRATEGY_CODEX_TIMEOUT_MS) {
  $env:AI_STRATEGY_CODEX_TIMEOUT_MS = "120000"
}
if (-not $env:CODEX_CMD) {
  $codexCmd = Join-Path $env:APPDATA "npm\codex.cmd"
  if (Test-Path $codexCmd) {
    $env:CODEX_CMD = $codexCmd
  }
}
if (-not $env:AI_STRATEGY_CODEX_CWD) {
  $env:AI_STRATEGY_CODEX_CWD = Join-Path $env:TEMP "ai-strategy-codex-cwd"
}
if (-not (Test-Path $env:AI_STRATEGY_CODEX_CWD)) {
  New-Item -ItemType Directory -Force -Path $env:AI_STRATEGY_CODEX_CWD | Out-Null
}

$proxyPort = if ($env:AI_STRATEGY_PROXY_PORT) { [int]$env:AI_STRATEGY_PROXY_PORT } else { 8787 }
$healthUrl = "http://127.0.0.1:$proxyPort/healthz"
try {
  $health = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2
  if ($health.StatusCode -eq 200) {
    Write-Host "AI strategy Codex proxy already running: $healthUrl"
    return
  }
} catch {
  # No healthy proxy is running; start a new one below.
}

Write-Host "AI strategy Codex proxy config: $env:AI_STRATEGY_MODEL_CONFIG"
Write-Host "AI strategy Codex command: $env:CODEX_CMD"
Write-Host "AI strategy Codex cwd: $env:AI_STRATEGY_CODEX_CWD"
Write-Host "Health check: $healthUrl"
node $scriptPath
