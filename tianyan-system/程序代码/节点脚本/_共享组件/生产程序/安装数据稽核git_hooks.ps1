param(
  [switch]$AuditOnlyPostHooks
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.DirectoryInfo]$PSScriptRoot
while ($null -ne $ProjectRoot -and -not (Test-Path -LiteralPath (Join-Path $ProjectRoot.FullName "AGENTS.md"))) {
  $ProjectRoot = $ProjectRoot.Parent
}
if ($null -eq $ProjectRoot) { throw "无法定位包含 AGENTS.md 的项目根目录。" }
$ProjectRoot = $ProjectRoot.FullName
$HooksDir = Join-Path $ProjectRoot ".git\hooks"
if (!(Test-Path $HooksDir)) {
  throw "Missing .git\hooks: $HooksDir"
}

function Write-AdvisorHook {
  param(
    [Parameter(Mandatory=$true)][string]$Name,
    [Parameter(Mandatory=$true)][string]$Mode,
    [string]$LfsCommand = "",
    [switch]$AuditOnly
  )

  $auditOnlyArg = if ($AuditOnly) { " --audit-only" } else { "" }
  $lfsBlock = ""
  if ($LfsCommand) {
    $lfsTemplate = @'
if command -v git-lfs >/dev/null 2>&1; then
  git lfs __LFS_COMMAND__ "$@" || exit $?
fi

'@
    $lfsBlock = $lfsTemplate.Replace("__LFS_COMMAND__", $LfsCommand)
  }
  $content = @'
#!/bin/sh
# Auto-installed by the advisor-monitor data-audit hook installer.
set -u
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$repo_root" || exit 1

__LFS_BLOCK__# If set, this escape hatch is only for emergency local recovery.
if [ "${ADVISOR_DATA_AUDIT_HOOK_SKIP:-}" = "1" ]; then
  echo "[data-audit-hook] skipped by ADVISOR_DATA_AUDIT_HOOK_SKIP=1"
  exit 0
fi

python -X utf8 "节点脚本/_共享组件/生产程序/run_project_data_audit_hook.py" --mode __MODE____AUDIT_ONLY__
'@
  $content = $content.Replace("__LFS_BLOCK__", $lfsBlock).Replace("__MODE__", $Mode).Replace("__AUDIT_ONLY__", $auditOnlyArg)
  $path = Join-Path $HooksDir $Name
  Set-Content -Path $path -Value $content -Encoding UTF8
}

Write-AdvisorHook -Name "pre-commit" -Mode "pre-commit" -AuditOnly
Write-AdvisorHook -Name "pre-push" -Mode "pre-push" -LfsCommand "pre-push" -AuditOnly
Write-AdvisorHook -Name "post-commit" -Mode "post-commit" -LfsCommand "post-commit" -AuditOnly:$AuditOnlyPostHooks
Write-AdvisorHook -Name "post-merge" -Mode "post-merge" -LfsCommand "post-merge" -AuditOnly:$AuditOnlyPostHooks
Write-AdvisorHook -Name "post-checkout" -Mode "post-checkout" -LfsCommand "post-checkout" -AuditOnly:$AuditOnlyPostHooks

Write-Host "Installed advisor data audit Git hooks: pre-commit, pre-push, post-commit, post-merge, post-checkout"
Write-Host "Emergency local bypass: set ADVISOR_DATA_AUDIT_HOOK_SKIP=1"
