param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('daily', 'initialize', 'check')]
    [string]$Mode
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$workspaceRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$pythonExe = Join-Path $workspaceRoot '运行环境\python\Scripts\python.exe'
$installer = Join-Path $workspaceRoot '程序代码\scripts\install_runtime_environment.ps1'
$runtimeCli = Join-Path $workspaceRoot '程序代码\scripts\runtime_workspace_cli.py'
$logRoot = Join-Path $workspaceRoot '运行状态\logs\launcher'
$timestamp = Get-Date -Format 'yyyyMMddTHHmmss'
$logPath = Join-Path $logRoot ("{0}_{1}.log" -f $timestamp, $Mode)
$logWriter = $null
$exitCode = 2

function Write-LauncherLine {
    param([AllowEmptyString()][string]$Message)

    Write-Host $Message
    if ($null -ne $script:logWriter) {
        $script:logWriter.WriteLine($Message)
    }
}

function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][object[]]$Arguments
    )

    & $FilePath @Arguments 2>&1 | ForEach-Object {
        Write-LauncherLine ([string]$_)
    }
    return [int]$LASTEXITCODE
}

function Invoke-EnvironmentInstaller {
    param([switch]$Recreate)

    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "运行环境安装器不存在: $installer"
    }
    $arguments = @(
        '-NoLogo',
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $installer,
        '-WorkspaceRoot', $workspaceRoot
    )
    if ($Recreate) {
        $arguments += '-RecreateVenv'
    }
    $installerExitCode = Invoke-LoggedCommand -FilePath 'powershell.exe' -Arguments $arguments
    if ($installerExitCode -ne 0) {
        throw "运行环境安装或修复失败，退出码: $installerExitCode"
    }
}

function Test-RuntimePython {
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
        return $false
    }
    & $pythonExe -c "import sys,requests,pandas,numpy,openpyxl;raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

try {
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    try {
        $logWriter = New-Object System.IO.StreamWriter($logPath, $false, (New-Object System.Text.UTF8Encoding($true)))
        $logWriter.AutoFlush = $true
    }
    catch {
        Write-Warning "启动日志无法创建: $($_.Exception.Message)"
    }

    Write-LauncherLine '============================================================================'
    Write-LauncherLine "天眼系统运行入口: $Mode"
    Write-LauncherLine "工作区: $workspaceRoot"
    Write-LauncherLine "开始时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-LauncherLine "启动日志: $logPath"
    Write-LauncherLine '============================================================================'
    Write-LauncherLine '[启动 1/3] 检查 Python 3.12 和运行依赖。'

    if (-not (Test-RuntimePython)) {
        if (Test-Path -LiteralPath $pythonExe -PathType Leaf) {
            Write-LauncherLine '[环境修复] 当前 Python 环境不可用，正在自动重建。'
            Invoke-EnvironmentInstaller -Recreate
        }
        else {
            Write-LauncherLine '[首次运行] 正在自动安装运行环境。'
            Invoke-EnvironmentInstaller
        }
    }
    if (-not (Test-RuntimePython)) {
        throw '运行环境安装后仍未通过 Python 3.12 和依赖检查。'
    }
    if (-not (Test-Path -LiteralPath $runtimeCli -PathType Leaf)) {
        throw "程序入口不存在: $runtimeCli"
    }
    Write-LauncherLine '[完成 1/3] Python 运行环境检查通过。'
    Write-LauncherLine '[启动 2/3] 检查统一运行入口。'
    Write-LauncherLine '[完成 2/3] 统一运行入口可用。'

    $env:PATH = "$(Split-Path -Parent $pythonExe);$env:PATH"
    $arguments = @('-u', '-X', 'utf8', $runtimeCli, '--workspace-root', $workspaceRoot)
    switch ($Mode) {
        'daily' { $arguments += 'daily' }
        'initialize' { $arguments += @('initialize', '--quick-check') }
        'check' { $arguments += @('check', '--check-devices', '--check-network') }
    }

    Write-LauncherLine "[启动 3/3] 开始执行 $Mode，以下输出将实时同步到控制台和日志。"
    $exitCode = Invoke-LoggedCommand -FilePath $pythonExe -Arguments $arguments
    if ($exitCode -ne 0) {
        Write-LauncherLine "[失败] 运行退出码: $exitCode。请查看启动日志: $logPath"
    }
    else {
        Write-LauncherLine '[完成 3/3] 任务执行成功。'
    }
}
catch {
    Write-LauncherLine "[启动失败] $($_.Exception.Message)"
    Write-LauncherLine "错误详情已记录到: $logPath"
    $exitCode = 2
}
finally {
    Write-LauncherLine '============================================================================'
    Write-LauncherLine "结束时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-LauncherLine "退出码: $exitCode"
    Write-LauncherLine '============================================================================'
    if ($null -ne $logWriter) {
        try { $logWriter.Dispose() } catch { }
    }
}

exit $exitCode
