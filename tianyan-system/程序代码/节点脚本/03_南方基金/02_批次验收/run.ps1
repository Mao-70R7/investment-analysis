param(
    [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$NodeRunDir,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$codeRoot = $env:ADVISOR_CODE_ROOT
if (-not $codeRoot) { $codeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path }
$bridge = Join-Path $codeRoot '节点脚本\00_调度框架\bridge_node.py'
if (-not (Test-Path -LiteralPath $bridge)) { throw "bridge_node.py was not found: $bridge" }
$python = if ($env:ADVISOR_PYTHON_EXE) { $env:ADVISOR_PYTHON_EXE } else { 'python' }
$arguments = @('-u', '-X', 'utf8', $bridge, '--action', 'southern_gate', '--workspace-root', $WorkspaceRoot, '--run-id', $RunId, '--node-run-dir', $NodeRunDir)
if ($DryRun) { $arguments += '--dry-run' }
& $python @arguments
exit $LASTEXITCODE
