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
$legacyLauncher = Join-Path $PSScriptRoot '启动.ps1'
if (-not (Test-Path -LiteralPath $legacyLauncher)) {
    throw "节点调度启动程序不存在：$legacyLauncher"
}

$forward = @{
    WorkspaceRoot = $WorkspaceRoot
    Mode = $Mode
    ModeArguments = $ModeArguments
}
if ($ResumeFromNode) { $forward.ResumeFromNode = $ResumeFromNode }
if ($ResumeToNode) { $forward.ResumeToNode = $ResumeToNode }
if ($NodeId) { $forward.NodeId = $NodeId }
if ($NodeRunId) { $forward.NodeRunId = $NodeRunId }
if ($Standalone) { $forward.Standalone = $true }
if ($DryRun) { $forward.DryRun = $true }

& $legacyLauncher @forward
exit $LASTEXITCODE
