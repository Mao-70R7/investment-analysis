param(
    [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
    [ValidateSet('daily', 'initialize', 'check', 'resume', 'node')][string]$Mode = 'daily',
    [string[]]$ModeArguments = @(),
    [string]$ResumeFromNode,
    [string]$ResumeToNode,
    [string]$NodeId,
    [string]$NodeRunId,
    [switch]$Standalone,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$workspace = (Resolve-Path -LiteralPath $WorkspaceRoot).Path
$launcherLogRoot = Join-Path $workspace '运行状态\logs\launcher'
$launcherLog = Join-Path $launcherLogRoot ("{0}_{1}.log" -f (Get-Date -Format 'yyyyMMddTHHmmss'), $Mode)
$transcriptStarted = $false
New-Item -ItemType Directory -Path $launcherLogRoot -Force | Out-Null
try {
    Start-Transcript -LiteralPath $launcherLog -Force | Out-Null
    $transcriptStarted = $true
}
catch {
    Write-Warning "启动日志无法创建：$($_.Exception.Message)"
}
Write-Host "[启动] 控制台输出同步日志：$launcherLog"
$configPath = Join-Path $workspace '本机配置\runtime.local.json'
$codeRoot = $workspace
if (Test-Path -LiteralPath $configPath) {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($config.codeRoot -and $config.codeRoot -ne '.') {
        $codeRoot = Join-Path $workspace ([string]$config.codeRoot -replace '/', '\')
    }
}
$codeRoot = (Resolve-Path -LiteralPath $codeRoot).Path
$orchestrator = Join-Path $codeRoot '节点脚本\00_调度框架\orchestrator.py'
if (-not (Test-Path -LiteralPath $orchestrator)) {
    throw "节点调度器不存在：$orchestrator"
}
$python = $env:ADVISOR_PYTHON_EXE
if (-not $python) {
    $runtimePython = Join-Path $workspace '运行环境\python\Scripts\python.exe'
    $runtimeReady = $false
    if (Test-Path -LiteralPath $runtimePython) {
        & $runtimePython -X utf8 -c "import sys,requests,pandas,numpy,openpyxl;raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" *> $null
        $runtimeReady = $LASTEXITCODE -eq 0
    }
    if ($runtimeReady) {
        $python = $runtimePython
    }
    elseif (Test-Path -LiteralPath $configPath) {
        $installer = Join-Path $codeRoot '节点脚本\_共享组件\生产程序\install_runtime_environment.ps1'
        if (-not (Test-Path -LiteralPath $installer)) {
            throw "运行环境安装器不存在：$installer"
        }
        if (Test-Path -LiteralPath $runtimePython) {
            Write-Host '[环境] 已有 Python 环境不可用，开始在本机重建。'
            & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $installer -WorkspaceRoot $workspace -RecreateVenv
        }
        else {
            Write-Host '[环境] 未发现工作区 Python，开始检查并安装依赖。'
            & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $installer -WorkspaceRoot $workspace
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $runtimePython)) {
            throw "运行环境安装失败，退出码=$LASTEXITCODE"
        }
        $python = $runtimePython
    }
    else {
        $python = 'python'
    }
}
$env:ADVISOR_PYTHON_EXE = $python
$arguments = @('-u', '-X', 'utf8', $orchestrator, '--workspace-root', $workspace)
if ($DryRun) { $arguments += '--dry-run' }
$arguments += $Mode
if ($Mode -eq 'node') {
    if (-not $NodeId) {
        throw 'node 模式必须提供 NodeId。'
    }
    $arguments += $NodeId
    if ($NodeRunId) {
        $arguments += @('--run-id', $NodeRunId)
    }
    if ($Standalone) {
        $arguments += '--standalone'
    }
}
else {
    $arguments += $ModeArguments
    if ($Mode -eq 'resume' -and $ResumeFromNode) {
        $arguments += @('--from-node', $ResumeFromNode)
    }
    if ($Mode -eq 'resume' -and $ResumeToNode) {
        $arguments += @('--to-node', $ResumeToNode)
    }
}
try {
    & $python @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}
exit $exitCode


