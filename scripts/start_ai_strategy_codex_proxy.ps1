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
  $env:AI_STRATEGY_CODEX_TIMEOUT_MS = "60000"
}

Write-Host "AI strategy Codex proxy config: $env:AI_STRATEGY_MODEL_CONFIG"
node $scriptPath
