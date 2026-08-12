$ErrorActionPreference = "Stop"

$ProjectRoot = if ($env:ADVISOR_CODE_ROOT) { [System.IO.Path]::GetFullPath($env:ADVISOR_CODE_ROOT) } else { (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path }
$MitmPython = Join-Path $ProjectRoot ".venv-mitm\Scripts\python.exe"
$MitmDump = Join-Path $ProjectRoot ".venv-mitm\Scripts\mitmdump.exe"

if (-not (Test-Path $MitmDump)) {
    throw "mitmdump not found: $MitmDump"
}

$runAt = Get-Date
$day = $runAt.ToString("yyyy-MM-dd")
$runId = $runAt.ToString("yyyyMMddTHHmmss")
$runDir = Join-Path $ProjectRoot ("data\raw\ttfund\interface_probe\{0}\{1}" -f $day, $runId)
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$flowPath = Join-Path $runDir "flows.mitm"
$stdoutPath = Join-Path $runDir "mitmdump.stdout.log"
$stderrPath = Join-Path $runDir "mitmdump.stderr.log"

Write-Host "run_dir=$runDir"
Write-Host "listen=0.0.0.0:8080"
Write-Host "flow_path=$flowPath"

& $MitmDump `
    --listen-host 0.0.0.0 `
    --listen-port 8080 `
    -w $flowPath `
    1>> $stdoutPath `
    2>> $stderrPath
