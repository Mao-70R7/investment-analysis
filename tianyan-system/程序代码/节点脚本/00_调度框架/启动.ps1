param(
    [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
    [ValidateSet('interactive', 'daily', 'initialize', 'check', 'resumeLatest', 'resume', 'node')][string]$Mode = 'daily',
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
$resumePlanner = Join-Path $codeRoot '节点脚本\00_调度框架\resume_plan.py'
if (-not (Test-Path -LiteralPath $resumePlanner)) {
    throw "续作计划程序不存在：$resumePlanner"
}

function Get-ResumePlan {
    param([string]$RequestedRunId)

    $planArguments = @('-X', 'utf8', $resumePlanner, '--workspace-root', $workspace, '--json')
    if ($RequestedRunId) {
        $planArguments += @('--run-id', $RequestedRunId)
    }
    $planOutput = & $python @planArguments
    $planExitCode = $LASTEXITCODE
    if ($planExitCode -ne 0) {
        throw "无法读取续作计划，退出码=$planExitCode"
    }
    try {
        return (($planOutput | Out-String) | ConvertFrom-Json)
    }
    catch {
        throw "续作计划格式无效：$($_.Exception.Message)"
    }
}

function Test-OrphanNodeProcess {
    param([Parameter(Mandatory = $true)][string]$RunId)

    $escapedRunId = [Regex]::Escape($RunId)
    $pattern = "(?i)(?:-RunId|--run-id)\s+[`"']?$escapedRunId(?:[`"']|\s|$)"
    try {
        $match = Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine -match $pattern
        } | Select-Object -First 1
        return $null -ne $match
    }
    catch {
        Write-Warning "无法检查遗留节点进程：$($_.Exception.Message)"
        return $false
    }
}

if ($DryRun -and $Mode -eq 'interactive') {
    $Mode = 'daily'
}

if ($Mode -in @('interactive', 'resumeLatest')) {
    $plan = Get-ResumePlan
    Write-Host '=============================================================================='
    Write-Host '每日更新启动选择'
    if ($plan.runId) {
        Write-Host "推荐续作批次：$($plan.runId)"
        Write-Host "批次状态：$($plan.runStatus)"
        Write-Host "最近恢复标记：$($plan.lastCheckpointAt)"
        Write-Host "已完成节点：$($plan.completedNodes)/$($plan.totalNodes)"
    }
    if ($plan.latestRunId -and -not $plan.isLatestRun) {
        Write-Host (
            "较新的未完成批次：$($plan.latestRunId)，仅完成 " +
            "$($plan.latestCompletedNodes)/$($plan.totalNodes)，" +
            "续作起点=$($plan.latestSuggestedFromNode)"
        )
    }
    Write-Host "判断：$($plan.reason)"
    if ($plan.available) {
        Write-Host "续作起点：$($plan.suggestedFromNode) $($plan.suggestedFromNodeName)"
        Write-Host '续作会重新校验此前节点；当前节点有内部检查点时继续，否则只重启当前节点。'
    }
    if ($plan.staleLock) {
        Write-Host '[状态提示] 检测到失效锁；正式启动时会在确认原 PID 已退出后安全回收。'
    }
    Write-Host '=============================================================================='
    if ($plan.active -or ($plan.runId -and (Test-OrphanNodeProcess -RunId ([string]$plan.runId)))) {
        throw "批次 $($plan.runId) 仍有运行进程，不能并发续作或重做。"
    }
    if ($Mode -eq 'resumeLatest') {
        if (-not $plan.available) {
            throw "没有可续作的最近批次：$($plan.reason)"
        }
        $selection = '1'
    }
    else {
        while ($true) {
            if ($plan.available) {
                Write-Host '[1] 续作推荐断点（复用已验证节点）'
            }
            Write-Host '[2] 放弃最近断点，重新执行新批次'
            Write-Host '[3] 取消，不执行'
            $selection = (Read-Host '请输入 1、2 或 3').Trim()
            if ($selection -in @('1', '2', '3') -and ($selection -ne '1' -or $plan.available)) {
                break
            }
            Write-Host '[输入无效] 请按当前可用选项重新输入。'
        }
    }
    if ($selection -eq '3') {
        Write-Host '[取消] 未启动任何更新任务。'
        if ($transcriptStarted) {
            Stop-Transcript | Out-Null
        }
        exit 0
    }
    if ($selection -eq '2') {
        $Mode = 'daily'
        $ModeArguments = @()
        $ResumeFromNode = $null
        $ResumeToNode = $null
        Write-Host '[选择] 将创建新 runId，从头执行。'
    }
    else {
        $Mode = 'resume'
        $ModeArguments = @([string]$plan.runId)
        $ResumeFromNode = [string]$plan.suggestedFromNode
        Write-Host "[选择] 续作 runId=$($plan.runId)，从节点 $ResumeFromNode 开始。"
    }
}

if ($Mode -eq 'resume' -and -not $ResumeFromNode) {
    $requestedRunId = [string]($ModeArguments | Select-Object -First 1)
    if (-not $requestedRunId) {
        throw 'resume 模式缺少 runId。'
    }
    $plan = Get-ResumePlan -RequestedRunId $requestedRunId
    if (-not $plan.available) {
        throw "批次 $requestedRunId 不可续作：$($plan.reason)"
    }
    if ($plan.active -or (Test-OrphanNodeProcess -RunId $requestedRunId)) {
        throw "批次 $requestedRunId 仍有运行进程，不能并发续作。"
    }
    $ResumeFromNode = [string]$plan.suggestedFromNode
    Write-Host "[自动续作] 已定位首个未完成节点：$ResumeFromNode $($plan.suggestedFromNodeName)"
}

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


