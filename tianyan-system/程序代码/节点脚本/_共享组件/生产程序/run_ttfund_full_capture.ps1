param(
    [Parameter(Mandatory = $true)]
    [string]$DeviceId,
    [string]$PythonExe = "python",
    [string]$AdbExe = "adb"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = if ($env:ADVISOR_CODE_ROOT) { [System.IO.Path]::GetFullPath($env:ADVISOR_CODE_ROOT) } else { (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path }
$runAt = Get-Date
$day = $runAt.ToString("yyyy-MM-dd")
$runId = $runAt.ToString("yyyyMMddTHHmmssK").Replace(":", "")
$jobRoot = Join-Path $ProjectRoot ("data\\raw\\ttfund\\full_capture_runs\\{0}\\{1}" -f $day, $runId)
$null = New-Item -ItemType Directory -Path $jobRoot -Force

$statusPath = Join-Path $jobRoot "status.json"
$summaryPath = Join-Path $jobRoot "summary.json"

function Write-Status {
    param(
        [string]$Stage,
        [string]$State,
        [string]$Message = ""
    )
    $payload = [ordered]@{
        updated_at = (Get-Date).ToString("s")
        stage = $Stage
        state = $State
        message = $Message
        device_id = $DeviceId
        job_root = $jobRoot
    }
    $payload | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $statusPath
}

function Invoke-Stage {
    param(
        [string]$StageName,
        [string[]]$CommandArgs
    )
    $logPath = Join-Path $jobRoot ("{0}.log" -f $StageName)
    Write-Status -Stage $StageName -State "running" -Message ($CommandArgs -join " ")
    "[$(Get-Date -Format s)] START $StageName" | Tee-Object -FilePath $logPath -Append | Out-Null
    "[$(Get-Date -Format s)] CMD   $PythonExe -u $($CommandArgs -join ' ')" | Tee-Object -FilePath $logPath -Append | Out-Null
    Push-Location $ProjectRoot
    try {
        & $PythonExe "-u" @CommandArgs 2>&1 | Tee-Object -FilePath $logPath -Append
        if ($LASTEXITCODE -ne 0) {
            throw "Stage failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
    "[$(Get-Date -Format s)] DONE  $StageName" | Tee-Object -FilePath $logPath -Append | Out-Null
}

function Invoke-AdbBestEffort {
    param([string[]]$Args)
    try {
        & $AdbExe @Args | Out-Null
    }
    catch {
    }
}

try {
    Write-Status -Stage "bootstrap" -State "running" -Message "prepare device"
    Invoke-AdbBestEffort -Args @("-s", $DeviceId, "get-state")
    Invoke-AdbBestEffort -Args @("-s", $DeviceId, "shell", "svc", "power", "stayon", "usb")
    Invoke-AdbBestEffort -Args @("-s", $DeviceId, "shell", "input", "keyevent", "224")

    Invoke-Stage -StageName "01_detail_drive" -CommandArgs @(
        ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
        "--device-id", $DeviceId,
        "--use-latest-master",
        "--missing-scope", "detail",
        "--skip-history",
        "--max-attempts", "2",
        "--retry-wait-ms", "2500",
        "--capture-failures"
    )

    Invoke-Stage -StageName "02_collect_after_detail" -CommandArgs @(
        ".\节点脚本\_共享组件\生产程序\collect_ttfund_loggedin.py",
        "--device-id", $DeviceId,
        "--skip-db"
    )

    Invoke-Stage -StageName "03_history_drive" -CommandArgs @(
        ".\节点脚本\_共享组件\生产程序\drive_ttfund_app.py",
        "--device-id", $DeviceId,
        "--use-latest-master",
        "--missing-scope", "history_adjustment",
        "--max-attempts", "2",
        "--retry-wait-ms", "2500",
        "--capture-failures"
    )

    Invoke-Stage -StageName "04_collect_after_history" -CommandArgs @(
        ".\节点脚本\_共享组件\生产程序\collect_ttfund_loggedin.py",
        "--device-id", $DeviceId,
        "--skip-db"
    )

    $summary = [ordered]@{
        finished_at = (Get-Date).ToString("s")
        state = "completed"
        device_id = $DeviceId
        job_root = $jobRoot
        status_path = $statusPath
    }
    $summary | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $summaryPath
    Write-Status -Stage "done" -State "completed" -Message "full capture finished"
}
catch {
    $message = $_.Exception.Message
    $summary = [ordered]@{
        finished_at = (Get-Date).ToString("s")
        state = "failed"
        device_id = $DeviceId
        job_root = $jobRoot
        error = $message
        status_path = $statusPath
    }
    $summary | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $summaryPath
    Write-Status -Stage "failed" -State "failed" -Message $message
    throw
}
