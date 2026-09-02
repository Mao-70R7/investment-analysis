param(
    [string]$DeviceId = "b27b7c93",
    [ValidateSet("latest_only", "all_missing", "none")]
    [string]$HistoryMode = "latest_only",
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string]$ExtraArgsLine = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Split-ExtraArgs {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return @()
    }
    return @($Text -split "\s+" | Where-Object { $_ })
}

function Write-LogLine {
    param([string]$Text = "")
    Write-Host $Text
    Add-Content -LiteralPath $script:LogFile -Value $Text -Encoding UTF8
}

function Write-FileToLog {
    param(
        [string]$Path,
        [string]$Title
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    Write-LogLine ""
    Write-LogLine ("---------------- {0} ----------------" -f $Title)
    Get-Content -LiteralPath $Path -Encoding Default | ForEach-Object {
        Write-LogLine $_
    }
    Write-LogLine ("-------------- END {0} --------------" -f $Title)
}

function Write-NewFileContentToLog {
    param(
        [string]$Path,
        [ref]$Offset,
        [string]$Prefix = ""
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $text = ""
    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try {
            if ([int64]$Offset.Value -gt $stream.Length) {
                $Offset.Value = [int64]0
            }
            [void]$stream.Seek([int64]$Offset.Value, [System.IO.SeekOrigin]::Begin)
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8, $true, 4096, $true)
            try {
                $text = $reader.ReadToEnd()
                $Offset.Value = [int64]$stream.Length
            }
            finally {
                $reader.Dispose()
            }
        }
        finally {
            $stream.Dispose()
        }
    }
    catch {
        return
    }

    if ([string]::IsNullOrEmpty($text)) {
        return
    }

    $normalized = $text.Replace("`r`n", "`n").Replace("`r", "`n")
    foreach ($line in ($normalized -split "`n")) {
        if ($line.Length -eq 0) {
            continue
        }
        Write-LogLine ("{0}{1}" -f $Prefix, $line)
    }
}

function ConvertTo-ProcessArgument {
    param([AllowNull()][string]$Argument)
    if ($null -eq $Argument -or $Argument.Length -eq 0) {
        return '""'
    }
    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }

    $backslash = [char]92
    $quote = [char]34
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append($quote)
    $backslashCount = 0

    foreach ($char in $Argument.ToCharArray()) {
        if ($char -eq $backslash) {
            $backslashCount += 1
            continue
        }
        if ($char -eq $quote) {
            if ($backslashCount -gt 0) {
                [void]$builder.Append($backslash, $backslashCount * 2)
                $backslashCount = 0
            }
            [void]$builder.Append($backslash)
            [void]$builder.Append($quote)
            continue
        }
        if ($backslashCount -gt 0) {
            [void]$builder.Append($backslash, $backslashCount)
            $backslashCount = 0
        }
        [void]$builder.Append($char)
    }

    if ($backslashCount -gt 0) {
        [void]$builder.Append($backslash, $backslashCount * 2)
    }
    [void]$builder.Append($quote)
    return $builder.ToString()
}

function ConvertTo-ProcessArgumentLine {
    param([string[]]$Arguments)
    return (($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join " ")
}

function Invoke-LoggedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList
    )
    $stdoutPath = Join-Path $script:LogDir ("{0}_{1}.stdout.log" -f $script:RunId, $Name)
    $stderrPath = Join-Path $script:LogDir ("{0}_{1}.stderr.log" -f $script:RunId, $Name)
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue

    Write-LogLine ("[CMD] {0} {1}" -f $FilePath, ($ArgumentList -join " "))
    $started = Get-Date

    $commandBody = "chcp 65001 > nul & set PYTHONUTF8=1 & set PYTHONIOENCODING=utf-8 & {0} {1} 1> {2} 2> {3}" -f `
        (ConvertTo-ProcessArgument $FilePath),
        (ConvertTo-ProcessArgumentLine -Arguments $ArgumentList),
        (ConvertTo-ProcessArgument $stdoutPath),
        (ConvertTo-ProcessArgument $stderrPath)

    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $(if ($env:ComSpec) { $env:ComSpec } else { "cmd.exe" })
    $processInfo.Arguments = "/d /c $commandBody"
    $processInfo.WorkingDirectory = $ProjectRoot
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $processInfo

    [void]$process.Start()
    $stdoutOffset = [int64]0
    $stderrOffset = [int64]0
    while (-not $process.WaitForExit(15000)) {
        Write-NewFileContentToLog -Path $stdoutPath -Offset ([ref]$stdoutOffset)
        Write-NewFileContentToLog -Path $stderrPath -Offset ([ref]$stderrOffset) -Prefix "[stderr] "
        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        Write-LogLine ("[INFO] {0} still running. pid={1} elapsed_seconds={2}" -f $Name, $process.Id, $elapsed)
    }
    $process.WaitForExit()
    Write-NewFileContentToLog -Path $stdoutPath -Offset ([ref]$stdoutOffset)
    Write-NewFileContentToLog -Path $stderrPath -Offset ([ref]$stderrOffset) -Prefix "[stderr] "
    $exitCode = $process.ExitCode
    $process.Dispose()

    $elapsedTotal = [int]((Get-Date) - $started).TotalSeconds
    Write-LogLine ("[INFO] {0} exited. exit_code={1} elapsed_seconds={2}" -f $Name, $exitCode, $elapsedTotal)
    Write-LogLine ("[INFO] {0} stdout log: {1}" -f $Name, $stdoutPath)
    if ((Test-Path -LiteralPath $stderrPath) -and ((Get-Item -LiteralPath $stderrPath).Length -gt 0)) {
        Write-LogLine ("[INFO] {0} stderr log: {1}" -f $Name, $stderrPath)
    }
    return [int]$exitCode
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
Set-Location $ProjectRoot

$runDay = Get-Date -Format "yyyy-MM-dd"
$script:RunId = Get-Date -Format "yyyyMMdd_HHmmss"
$script:LogRoot = Join-Path $ProjectRoot ("logs\daily_incremental\{0}" -f $runDay)
$script:LogDir = Join-Path $script:LogRoot $script:RunId
New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null
$script:LogFile = Join-Path $script:LogDir "console.log"
New-Item -ItemType File -Path $script:LogFile -Force | Out-Null

$extraArgs = Split-ExtraArgs -Text $ExtraArgsLine
$jobExitCode = 1

try {
    Write-LogLine "============================================================"
    Write-LogLine ("[INFO] Daily incremental started at {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    Write-LogLine ("[INFO] Project root : {0}" -f $ProjectRoot)
    Write-LogLine ("[INFO] Device ID    : {0}" -f $DeviceId)
    Write-LogLine ("[INFO] History mode : {0}" -f $HistoryMode)
    Write-LogLine ("[INFO] Log file     : {0}" -f $script:LogFile)
    if ($extraArgs.Count -gt 0) {
        Write-LogLine ("[INFO] Extra args   : {0}" -f ($extraArgs -join " "))
    }
    Write-LogLine "============================================================"
    Write-LogLine ""

    $lockPath = Join-Path $ProjectRoot "data\run_daily_incremental.lock"
    if (Test-Path -LiteralPath $lockPath) {
        $running = Get-CimInstance Win32_Process |
            Where-Object {
                ($_.CommandLine -like "*run_daily_incremental.ps1*" -or
                 $_.CommandLine -like "*run_ttfund_incremental_update.ps1*" -or
                 $_.CommandLine -like "*collect_ttfund_loggedin.py*" -or
                 $_.CommandLine -like "*run_ttfund_post_update_quality.py*") -and
                $_.CommandLine -notlike "*Get-CimInstance Win32_Process*"
            } |
            Select-Object -First 1
        if ($null -eq $running) {
            $lockItem = Get-Item -LiteralPath $lockPath
            Write-LogLine ("[WARN] Removing stale lock: {0} LastWriteTime={1}" -f $lockPath, $lockItem.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"))
            Remove-Item -LiteralPath $lockPath -Force
        }
        else {
            Write-LogLine ("[WARN] Lock exists and a matching process is running: PID={0}" -f $running.ProcessId)
        }
    }

    $commandArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $ProjectRoot "run_daily_incremental.ps1"),
        "-DeviceId", $DeviceId,
        "-HistoryMode", $HistoryMode
    )
    if ($extraArgs.Count -gt 0) {
        $commandArgs += $extraArgs
    }

    $jobExitCode = Invoke-LoggedProcess -Name "daily_incremental" -FilePath "powershell" -ArgumentList $commandArgs

    Write-LogLine ""
    Write-LogLine "============================================================"
    Write-LogLine ("[INFO] PowerShell job exit code: {0}" -f $jobExitCode)
    Write-LogLine "[INFO] Printing parsed result summary..."
    Write-LogLine "============================================================"

    $summaryArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $ProjectRoot "节点脚本\_共享组件\生产程序\print_daily_incremental_result.ps1"),
        "-ProjectRoot", $ProjectRoot,
        "-RunDay", $runDay,
        "-ExitCode", ([string]$jobExitCode)
    )
    [void](Invoke-LoggedProcess -Name "result_summary" -FilePath "powershell" -ArgumentList $summaryArgs)

    Write-LogLine ""
    if ($jobExitCode -eq 0) {
        Write-LogLine "[RESULT] SUCCESS"
    }
    else {
        Write-LogLine "[RESULT] FAILED"
    }
    Write-LogLine ("[INFO] Finished at {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    Write-LogLine ("[INFO] Log file: {0}" -f $script:LogFile)
}
catch {
    Write-LogLine ("[ERROR] {0}" -f $_.Exception.Message)
    $jobExitCode = 1
}

exit $jobExitCode
