param(
    [ValidateSet('daily', 'check', 'migration', 'ttfund')][string]$Mode = 'daily'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$codeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$workspaceRoot = $codeRoot
if (Test-Path -LiteralPath (Join-Path $codeRoot '..\本机配置\runtime.local.json')) {
    $workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $codeRoot '..')).Path
}
$entry = Join-Path $workspaceRoot '00_每日数据更新并发布_唯一入口.bat'
if (-not (Test-Path -LiteralPath $entry)) {
    throw "找不到正式入口：$entry"
}
Write-Warning "当前脚本只保留一轮兼容，请改用：$entry"
switch ($Mode) {
    'check' { & $entry check }
    'migration' { & $entry node migration_package }
    'ttfund' { & $entry node ttfund_incremental }
    default { & $entry }
}
exit $LASTEXITCODE
