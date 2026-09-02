param(
    [string]$DeviceId = "",
    [string]$AdbExe = "",
    [string[]]$StrategyId = @(),
    [string]$StrategyFile = "",
    [int]$SampleSize = 3,
    [int]$LaunchWarmupSec = 8,
    [int]$DetailScanSwipes = 1,
    [switch]$AllowNonEmulator,
    [switch]$ListDevices,
    [switch]$SkipLaunch,
    [switch]$FailOnInvalid
)

$ErrorActionPreference = "Stop"
$ProjectRoot = if ($env:ADVISOR_CODE_ROOT) { [System.IO.Path]::GetFullPath($env:ADVISOR_CODE_ROOT) } else { (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path }
Set-Location $ProjectRoot

if (-not $AdbExe) {
    $localAdb = Join-Path $ProjectRoot "tools\platform-tools\adb.exe"
    if (Test-Path -LiteralPath $localAdb) {
        $AdbExe = $localAdb
    }
    else {
        $AdbExe = "adb"
    }
}

$pythonArgs = @(
    "-X", "utf8",
    ".\节点脚本\_共享组件\生产程序\probe_ttfund_emulator_data.py",
    "--adb-path", $AdbExe,
    "--sample-size", "$SampleSize",
    "--launch-warmup-sec", "$LaunchWarmupSec",
    "--detail-scan-swipes", "$DetailScanSwipes"
)

if ($DeviceId) {
    $pythonArgs += @("--device-id", $DeviceId)
}
if ($StrategyFile) {
    $pythonArgs += @("--strategy-file", $StrategyFile)
}
foreach ($sid in $StrategyId) {
    if ($sid) {
        $pythonArgs += @("--strategy-id", $sid)
    }
}
if ($AllowNonEmulator) {
    $pythonArgs += "--allow-non-emulator"
}
if ($ListDevices) {
    $pythonArgs += "--list-devices"
}
if ($SkipLaunch) {
    $pythonArgs += "--skip-launch"
}
if ($FailOnInvalid) {
    $pythonArgs += "--fail-on-invalid"
}

Write-Host "[ttfund-emulator-probe] project root: $ProjectRoot"
Write-Host "[ttfund-emulator-probe] adb: $AdbExe"
Write-Host "[ttfund-emulator-probe] output: data\raw\ttfund\emulator_probe"

& python @pythonArgs
exit $LASTEXITCODE
