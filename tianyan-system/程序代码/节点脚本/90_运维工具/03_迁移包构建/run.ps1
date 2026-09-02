param(
    [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$NodeRunDir,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$codeRoot = $env:ADVISOR_CODE_ROOT
if (-not $codeRoot) {
    $codeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
}
$bridge = Join-Path $codeRoot '节点脚本\00_调度框架\bridge_node.py'
$python = if ($env:ADVISOR_PYTHON_EXE) { $env:ADVISOR_PYTHON_EXE } else { 'python' }
$arguments = @('-u', '-X', 'utf8', $bridge, '--action', 'migration_package', '--workspace-root', $WorkspaceRoot, '--run-id', $RunId, '--node-run-dir', $NodeRunDir)
$env:ADVISOR_MIGRATION_PARENT_RUN_ID = $RunId
if ($DryRun) { $arguments += '--dry-run' }
& $python @arguments
exit $LASTEXITCODE
