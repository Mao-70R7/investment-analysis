from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import py_compile
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_workspace import CONFIG_RELATIVE_PATH, WorkspaceLayout, load_workspace


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())


def resolve_runtime_layout() -> WorkspaceLayout | None:
    configured_workspace = str(os.environ.get("ADVISOR_WORKSPACE_ROOT") or "").strip()
    if configured_workspace:
        return load_workspace(Path(configured_workspace), require_config=True)
    for candidate in (PROJECT_ROOT, *PROJECT_ROOT.parents):
        if (candidate / CONFIG_RELATIVE_PATH).is_file():
            return load_workspace(candidate, require_config=True)
    return None


RUNTIME_LAYOUT = resolve_runtime_layout()
DEFAULT_REPORT_ROOT = Path(
    os.environ.get("ADVISOR_REPORT_ROOT")
    or (RUNTIME_LAYOUT.publish_root if RUNTIME_LAYOUT else PROJECT_ROOT / "site" / "最小发布集")
)
DEFAULT_DB_PATH = RUNTIME_LAYOUT.main_db if RUNTIME_LAYOUT else PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SITE_DIR = DEFAULT_REPORT_ROOT / "basic_data"
DEFAULT_OUTPUT_ROOT = (
    RUNTIME_LAYOUT.output_root / "data_audit_hook"
    if RUNTIME_LAYOUT
    else PROJECT_ROOT / "outputs" / "data_audit_hook"
)
DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "数据稽核规则规范.json"
BUSINESS_QUALITY_RULE_IDS = {
    "页面数据包新鲜度": "BUSINESS_QUALITY_PAGE_PACK_FRESHNESS",
    "页面数据包体积": "BUSINESS_QUALITY_PAGE_PACK_SIZE",
    "当前持仓基金行业覆盖": "BUSINESS_QUALITY_CURRENT_HOLDING_INDUSTRY_COVERAGE",
    "重要策略元数据缺失": "BUSINESS_QUALITY_IMPORTANT_STRATEGY_METADATA_MISSING",
    "目标盈标签强证据": "BUSINESS_QUALITY_TARGET_PROFIT_EVIDENCE",
    "目标盈期次单独分析": "BUSINESS_QUALITY_TARGET_PROFIT_SEPARATE_ANALYSIS",
}
PERSISTENCE_LOOKBACK_RUNS = 8
PERSISTENCE_WARN_THRESHOLD = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行项目修改后的全量数据稽核 hook。")
    parser.add_argument("--mode", default="manual", choices=["manual", "pre-commit", "post-commit", "post-merge", "post-checkout", "pre-push", "watcher"])
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--audit-only", action="store_true", help="只运行已有页面包稽核，不重建报表包。")
    parser.add_argument("--skip-static", action="store_true", help="跳过 Python/JS/PowerShell 静态检查。")
    parser.add_argument("--fail-on-warn", action="store_true", help="存在 warn 时也返回非 0。")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--page-set",
        choices=["auto", "minimal_publish", "basic_data", "all"],
        default="auto",
        help="刷新部署清单的页面范围；auto 会继承现有清单或根据正式目录文件自动判断。",
    )
    return parser.parse_args()


def now_text() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def run(cmd: list[str], *, cwd: Path = PROJECT_ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print(f"[data-audit-hook] RUN {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=cwd, text=True, encoding="utf-8", errors="replace", env=env)


def project_arg(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def collect_files(patterns: list[str], roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            files.extend(path for path in root.rglob(pattern) if path.is_file())
    return sorted(set(files))


def production_node_files(patterns: list[str]) -> list[Path]:
    return [
        path
        for path in collect_files(patterns, [PROJECT_ROOT / "节点脚本"])
        if "98_非生产工具" not in path.parts and "99_兼容入口" not in path.parts
    ]


def static_check_python() -> dict[str, Any]:
    files = production_node_files(["*.py"])
    errors = []
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append({"file": str(path), "error": str(exc)})
    return {"name": "python_py_compile", "files": len(files), "errors": errors}


def is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"run", "Popen", "call", "check_call", "check_output"}
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
    )


def is_sys_executable(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "executable" and isinstance(node.value, ast.Name) and node.value.id == "sys"


def static_check_python_subprocess_utf8() -> dict[str, Any]:
    files = production_node_files(["*.py"])
    errors = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:
            errors.append({"file": str(path), "line": 0, "ruleId": "PYTHON_SUBPROCESS_UTF8_REQUIRED", "error": f"cannot parse Python AST: {exc}"})
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not is_subprocess_call(node) or not node.args:
                continue
            first_arg = node.args[0]
            if not isinstance(first_arg, ast.List) or not first_arg.elts or not is_sys_executable(first_arg.elts[0]):
                continue
            constants = [item.value for item in first_arg.elts if isinstance(item, ast.Constant)]
            if "-X" not in constants or "utf8" not in constants:
                errors.append(
                    {
                        "file": str(path),
                        "line": node.lineno,
                        "ruleId": "PYTHON_SUBPROCESS_UTF8_REQUIRED",
                        "error": "project Python subprocess calls that use sys.executable must include -X utf8 to avoid Chinese-path corruption on Windows.",
                    }
                )
    return {"name": "python_subprocess_utf8", "files": len(files), "errors": errors}


def static_check_js() -> dict[str, Any]:
    files = collect_files(["*.js"], [PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序", PROJECT_ROOT / "site" / "basic_data" / "assets", PROJECT_ROOT / "basic_data" / "assets"])
    errors = []
    for path in files:
        completed = run(["node", "--check", str(path)])
        if completed.returncode:
            errors.append({"file": str(path), "returncode": completed.returncode})
    return {"name": "node_check", "files": len(files), "errors": errors}


def static_check_powershell() -> dict[str, Any]:
    files = sorted(set(PROJECT_ROOT.glob("*.ps1")) | set(production_node_files(["*.ps1"])))
    if not files:
        return {"name": "powershell_parser", "files": 0, "errors": []}
    files_json = json.dumps([str(path) for path in files], ensure_ascii=False)
    script = f"""
$ErrorActionPreference = 'Stop'
$files = ConvertFrom-Json @'
{files_json}
'@
$failed = @()
foreach ($file in $files) {{
  $tokens = $null
  $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors) | Out-Null
  if ($errors.Count -gt 0) {{
    $failed += [pscustomobject]@{{ file = $file; errors = ($errors | ForEach-Object {{ $_.Message }}) }}
  }}
}}
if ($failed.Count -gt 0) {{
  $failed | ConvertTo-Json -Depth 5
  exit 2
}}
"""
    completed = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])
    errors = []
    if completed.returncode:
        errors.append({"returncode": completed.returncode, "output": (completed.stdout or "")[-4000:]})
    return {"name": "powershell_parser", "files": len(files), "errors": errors}


def run_static_checks() -> list[dict[str, Any]]:
    checks = [static_check_python(), static_check_python_subprocess_utf8(), static_check_js(), static_check_powershell()]
    for check in checks:
        print(f"[data-audit-hook] {check['name']}: files={check['files']} errors={len(check['errors'])}")
    return checks


def latest_audit_report(root: Path | None = None) -> Path | None:
    root = root or PROJECT_ROOT / "outputs" / "data_audit"
    if not root.exists():
        return None
    reports = list(root.glob("*/*/data_audit_report.json"))
    return max(reports, key=lambda path: path.stat().st_mtime) if reports else None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_manifest_page_set(report_root: Path, requested: str) -> str:
    if requested != "auto":
        return requested
    manifest_path = report_root / "deployment_manifest.json"
    if manifest_path.exists():
        try:
            existing = read_json(manifest_path)
        except Exception:
            existing = {}
        page_set = existing.get("pageSet")
        if isinstance(page_set, str) and page_set == "minimal_publish":
            return "minimal_publish"
        # Current minimal deployment manifests disclose the user-facing page
        # names as an array.  A materialized package is still unambiguously a
        # minimal publish package and must not be routed to the full-site audit.
        if isinstance(page_set, list) and is_materialized_minimal_publish_package(report_root):
            return "minimal_publish"
        if isinstance(page_set, str) and page_set == "all":
            return "all"
    all_required = [
        report_root / "strategy_center" / "index.html",
        report_root / "strategy_center" / "data" / "summary.js",
        report_root / "strategy_center" / "data" / "quality.js",
        report_root / "full_data_statistics_report" / "index.html",
    ]
    if all(path.exists() for path in all_required):
        return "all"
    if manifest_path.exists() and isinstance(existing.get("pageSet"), str) and existing.get("pageSet") == "basic_data":
        return "basic_data"
    return "basic_data"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_minimal_publish_package(
    report_root: Path,
    *,
    output_root: Path | None = None,
    run_id: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Audit an already-built minimal package without requiring full-report raw packs."""
    report_root = report_root.resolve()
    generated_at = datetime.now().astimezone()
    run_id = run_id or generated_at.strftime("%Y%m%dT%H%M%S%z")
    output_root = output_root or PROJECT_ROOT / "outputs" / "data_audit"
    output_dir = output_root / generated_at.strftime("%Y-%m-%d") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    issues: list[dict[str, Any]] = []
    findings: list[str] = []
    required_payloads: dict[str, dict[str, Any]] = {}
    for name in ("package_validation.json", "deployment_manifest.json", "version.json"):
        path = report_root / name
        if not path.is_file():
            findings.append(f"缺少 {name}")
            continue
        try:
            payload = read_json(path)
        except Exception as exc:
            findings.append(f"{name} 无法解析：{exc}")
            continue
        if not isinstance(payload, dict):
            findings.append(f"{name} 顶层不是 JSON object")
            continue
        required_payloads[name] = payload

    validation = required_payloads.get("package_validation.json", {})
    manifest = required_payloads.get("deployment_manifest.json", {})
    version = required_payloads.get("version.json", {})

    if validation:
        if validation.get("status") != "ready":
            findings.append(f"package_validation.status={validation.get('status')!r}，应为 'ready'")
        checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
        policy = validation.get("policy") if isinstance(validation.get("policy"), dict) else {}
        blocking_checks = policy.get("blockingZeroChecks") if isinstance(policy.get("blockingZeroChecks"), list) else []
        nonzero = {
            str(name): checks.get(name)
            for name in blocking_checks
            if checks.get(name) not in (0, 0.0)
        }
        if nonzero:
            findings.append(f"阻断检查不为 0：{json.dumps(nonzero, ensure_ascii=False, sort_keys=True)}")

    manifest_build_id = str(manifest.get("buildId") or "") if manifest else ""
    version_build_id = str(version.get("buildId") or "") if version else ""
    if manifest and version and (not manifest_build_id or manifest_build_id != version_build_id):
        findings.append(
            f"deployment_manifest.buildId={manifest_build_id!r} 与 version.buildId={version_build_id!r} 不一致"
        )

    verified_file_count = 0
    verified_total_bytes = 0
    missing_files: list[str] = []
    invalid_paths: list[str] = []
    size_mismatches: list[dict[str, Any]] = []
    hash_mismatches: list[str] = []
    entries = manifest.get("files") if isinstance(manifest.get("files"), list) else None
    if manifest and entries is None:
        findings.append("deployment_manifest.files 不是数组")
        entries = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            invalid_paths.append(repr(entry))
            continue
        relative_text = str(entry.get("path") or "").replace("\\", "/").strip()
        relative_path = Path(relative_text)
        if not relative_text or relative_path.is_absolute() or ".." in relative_path.parts:
            invalid_paths.append(relative_text or "<empty>")
            continue
        candidate = (report_root / relative_path).resolve()
        try:
            candidate.relative_to(report_root)
        except ValueError:
            invalid_paths.append(relative_text)
            continue
        if not candidate.is_file():
            missing_files.append(relative_text)
            continue
        actual_size = candidate.stat().st_size
        expected_size = entry.get("size")
        if not isinstance(expected_size, int) or actual_size != expected_size:
            size_mismatches.append(
                {"path": relative_text, "expected": expected_size, "actual": actual_size}
            )
            continue
        expected_hash = str(entry.get("sha256") or "").lower()
        actual_hash = sha256_file(candidate)
        if len(expected_hash) != 64 or actual_hash != expected_hash:
            hash_mismatches.append(relative_text)
            continue
        verified_file_count += 1
        verified_total_bytes += actual_size

    if invalid_paths:
        findings.append(f"清单路径非法 {len(invalid_paths)} 个，示例={invalid_paths[:10]}")
    if missing_files:
        findings.append(f"清单文件缺失 {len(missing_files)} 个，示例={missing_files[:10]}")
    if size_mismatches:
        findings.append(f"清单文件大小不一致 {len(size_mismatches)} 个，示例={size_mismatches[:10]}")
    if hash_mismatches:
        findings.append(f"清单文件哈希不一致 {len(hash_mismatches)} 个，示例={hash_mismatches[:10]}")
    if manifest and isinstance(entries, list):
        expected_count = manifest.get("fileCount")
        expected_total_bytes = manifest.get("totalBytes")
        if expected_count != len(entries):
            findings.append(
                f"deployment_manifest.fileCount={expected_count!r}，实际清单条目={len(entries)}"
            )
        listed_total_bytes = sum(
            entry.get("size", 0)
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("size"), int)
        )
        if expected_total_bytes != listed_total_bytes:
            findings.append(
                f"deployment_manifest.totalBytes={expected_total_bytes!r}，清单汇总={listed_total_bytes}"
            )

    if findings:
        rule = read_rule_catalog().get("PAGE_REQUIRED_PACK_MISSING", {})
        issues.append(
            {
                "ruleId": "PAGE_REQUIRED_PACK_MISSING",
                "severity": rule.get("severity") or "error",
                "scope": "minimal_publish.package_integrity",
                "item": "最小发布包完整性校验失败",
                "detail": "；".join(findings),
                "原因说明": "最小发布包的验证结果、版本标识或部署清单与实际文件不一致。",
                "优化建议": "从 report_build 节点重新生成最小发布包，通过 package_validation 和清单哈希校验后再发布。",
                "修复责任脚本": "节点脚本/_共享组件/生产程序/build_minimal_publish_set.py",
                "修复责任节点": "report_build",
            }
        )

    error_count = sum(1 for issue in issues if issue.get("severity") == "error")
    warn_count = sum(1 for issue in issues if issue.get("severity") == "warn")
    report = {
        "version": 1,
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "status": "error" if error_count else ("warn" if warn_count else "ok"),
        "reportRoot": str(report_root),
        "auditMode": "minimal_publish",
        "summary": {
            "error": error_count,
            "warn": warn_count,
            "total": len(issues),
            "manifestEntryCount": len(entries or []),
            "verifiedFileCount": verified_file_count,
            "verifiedTotalBytes": verified_total_bytes,
        },
        "buildId": manifest_build_id,
        "issues": issues,
    }
    report_path = output_dir / "data_audit_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path, report


def is_materialized_minimal_publish_package(report_root: Path) -> bool:
    """Distinguish a compressed publish package from its uncompressed staging source."""
    package_validation_path = report_root / "package_validation.json"
    version_path = report_root / "version.json"
    manifest_path = report_root / "deployment_manifest.json"
    if not package_validation_path.is_file() or not version_path.is_file() or not manifest_path.is_file():
        return False
    try:
        manifest = read_json(manifest_path)
    except Exception:
        return False
    return (
        isinstance(manifest, dict)
        and isinstance(manifest.get("files"), list)
        and isinstance(manifest.get("fileCount"), int)
    )


def read_rule_catalog(path: Path = DEFAULT_RULES_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
    except Exception:
        return {}
    rules = payload.get("rules") if isinstance(payload, dict) else {}
    return rules if isinstance(rules, dict) else {}


def issue_key(issue: dict[str, Any]) -> str:
    return "|".join(str(issue.get(part) or "") for part in ("ruleId", "scope", "item"))


def recent_hook_summaries(output_root: Path, current_output_dir: Path, limit: int = PERSISTENCE_LOOKBACK_RUNS) -> list[dict[str, Any]]:
    if not output_root.exists():
        return []
    paths = [
        path
        for path in output_root.glob("*/*/hook_summary.json")
        if path.resolve() != (current_output_dir / "hook_summary.json").resolve()
    ]
    summaries = []
    for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        try:
            summaries.append(read_json(path))
        except Exception:
            continue
    return summaries


def attach_persistence(issues: list[dict[str, Any]], output_root: Path, current_output_dir: Path) -> list[dict[str, Any]]:
    recent = recent_hook_summaries(output_root, current_output_dir)
    counts: Counter[str] = Counter()
    for summary in recent:
        seen = {issue_key(issue) for issue in summary.get("issues") or []}
        counts.update(seen)
    enriched = []
    for issue in issues:
        copied = dict(issue)
        copied["recentOccurrences"] = counts.get(issue_key(issue), 0) + 1
        enriched.append(copied)
    return enriched


def governance_issues(issues: list[dict[str, Any]], rule_catalog: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    missing = sorted({str(issue.get("ruleId") or "UNCLASSIFIED_AUDIT_RULE") for issue in issues if str(issue.get("ruleId") or "") not in rule_catalog})
    for rule_id in missing:
        rule = rule_catalog.get("RULE_CATALOG_MISSING", {})
        output.append(
            {
                "severity": rule.get("severity") or "warn",
                "ruleId": "RULE_CATALOG_MISSING",
                "scope": "rule_catalog",
                "item": "规则未登记",
                "detail": f"未登记 ruleId={rule_id}",
                "原因说明": rule.get("原因说明") or "问题类型未沉淀到规则库。",
                "优化建议": rule.get("优化建议") or "补充规则说明和检测逻辑。",
                "修复责任脚本": rule.get("修复责任脚本") or "config/数据稽核规则规范.json",
                "修复责任节点": rule.get("修复责任节点") or "data_audit",
            }
        )
    persistent = [
        issue
        for issue in issues
        if int(issue.get("recentOccurrences") or 0) >= PERSISTENCE_WARN_THRESHOLD
    ]
    if persistent:
        rule = rule_catalog.get("PERSISTENT_AUDIT_ISSUE", {})
        output.append(
            {
                "severity": rule.get("severity") or "warn",
                "ruleId": "PERSISTENT_AUDIT_ISSUE",
                "scope": "data_audit_hook",
                "item": "持续性问题",
                "detail": f"{len(persistent)} 个问题在最近 {PERSISTENCE_LOOKBACK_RUNS} 次 hook 中重复出现达到 {PERSISTENCE_WARN_THRESHOLD} 次以上。",
                "原因说明": rule.get("原因说明") or "同类问题持续出现。",
                "优化建议": rule.get("优化建议") or "升级为显式优化任务。",
                "修复责任脚本": rule.get("修复责任脚本") or "对应 issue 的责任脚本",
                "sample": [
                    {
                        "ruleId": issue.get("ruleId"),
                        "scope": issue.get("scope"),
                        "item": issue.get("item"),
                        "recentOccurrences": issue.get("recentOccurrences"),
                    }
                    for issue in persistent[:20]
                ],
            }
        )
    return output


def read_business_quality_issues(site_dir: Path) -> list[dict[str, Any]]:
    path = site_dir / "data" / "data_quality_pack.json"
    if not path.exists():
        return []
    try:
        payload = read_json(path)
    except Exception as exc:
        return [
            {
                "severity": "warn",
                "ruleId": "DATA_QUALITY_PACK_READ_FAILED",
                "scope": "data_quality_pack",
                "item": "数据质量包读取失败",
                "detail": str(exc),
                "原因说明": "无法读取业务质量 gate 输出，hook 不能确认页面质量状态。",
                "优化建议": "重新运行 节点脚本/_共享组件/生产程序/构建基础数据质量包.py 或完整报表包构建。",
                "修复责任脚本": "节点脚本/_共享组件/生产程序/构建基础数据质量包.py",
                "修复责任节点": "report_build",
            }
        ]
    issues = []
    for check in payload.get("checks") or []:
        status = str(check.get("状态") or check.get("status") or "").lower()
        if status in {"ok", "ready", ""}:
            continue
        item = check.get("项目") or check.get("name") or "业务质量检查"
        detail = f"当前值={check.get('当前值', '')}；门槛={check.get('门槛', '')}；说明={check.get('说明', '')}"
        issues.append(
            {
                "severity": "error" if status == "error" else "warn",
                "ruleId": BUSINESS_QUALITY_RULE_IDS.get(str(item), f"BUSINESS_QUALITY_{str(item).upper()}"),
                "scope": "data_quality_pack",
                "item": item,
                "detail": detail,
                "原因说明": check.get("说明") or "业务质量 gate 标记该指标未达到门槛。",
                "优化建议": f"影响页面：{check.get('影响页面', '未标注')}；按责任脚本修复后重新运行完整 hook。",
                "修复责任脚本": check.get("修复责任脚本") or "节点脚本/_共享组件/生产程序/build_basic_data_report_packs.py",
                "修复责任节点": check.get("修复责任节点") or "report_build",
            }
        )
    return issues


def write_hook_summary(output_dir: Path, summary: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "hook_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 项目数据稽核 Hook 汇总",
        "",
        f"- 运行时间：{summary['generatedAt']}",
        f"- 模式：{summary['mode']}",
        f"- 状态：{summary['status']}",
        f"- 静态检查错误：{summary['staticErrorCount']}",
        f"- 标准化稽核报告：{summary.get('auditReportPath') or '未生成'}",
        f"- 业务质量 warning/error：{len(summary.get('businessQualityIssues') or [])}",
        "",
        "## 不符合规范字段/数据问题",
        "",
    ]
    issues = summary.get("issues") or []
    if not issues:
        lines.append("未发现 error/warn。")
    else:
        for issue in issues[:50]:
            lines.append(f"- [{issue.get('severity')}] {issue.get('ruleId')} / {issue.get('scope')} / {issue.get('item')}")
            lines.append(f"  - 原因说明：{issue.get('原因说明', '待补充')}")
            lines.append(f"  - 优化建议：{issue.get('优化建议', '待补充')}")
            lines.append(f"  - 修复责任脚本：{issue.get('修复责任脚本', '待补充')}")
            lines.append(f"  - 修复责任节点：{issue.get('修复责任节点', 'data_audit')}")
    (output_dir / "hook_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not args.audit_only:
        print("[data-audit-hook] minimal-publish-only policy: audit existing package without rebuilding legacy full reports", flush=True)
        args.audit_only = True
    if os.environ.get("ADVISOR_DATA_AUDIT_HOOK_SKIP") == "1":
        print("[data-audit-hook] skipped by ADVISOR_DATA_AUDIT_HOOK_SKIP=1")
        return
    if os.environ.get("ADVISOR_DATA_AUDIT_RUNNING") == "1":
        print("[data-audit-hook] skipped to avoid recursive hook execution")
        return

    env = dict(os.environ)
    env["ADVISOR_DATA_AUDIT_RUNNING"] = "1"
    run_id = now_text()
    output_dir = args.output_root / datetime.now().astimezone().strftime("%Y-%m-%d") / run_id
    standardized_audit_root = output_dir / "standardized"
    static_checks: list[dict[str, Any]] = []
    static_error_count = 0
    manifest_page_set = resolve_manifest_page_set(args.report_root, args.page_set)
    report_path: Path | None = None
    audit_report: dict[str, Any] = {}

    if not args.skip_static:
        static_checks = run_static_checks()
        static_error_count = sum(len(check.get("errors") or []) for check in static_checks)
        if static_error_count:
            summary = {
                "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "mode": args.mode,
                "status": "error",
                "staticChecks": static_checks,
                "staticErrorCount": static_error_count,
                "issues": [],
            }
            write_hook_summary(output_dir, summary)
            raise SystemExit(2)

    if (
        args.audit_only
        and manifest_page_set == "minimal_publish"
        and is_materialized_minimal_publish_package(args.report_root)
    ):
        report_path, audit_report = audit_minimal_publish_package(
            args.report_root,
            output_root=standardized_audit_root,
            run_id=run_id,
        )
        completed = subprocess.CompletedProcess(
            args=["minimal_publish_package_audit"],
            returncode=2 if (audit_report.get("summary") or {}).get("error") else 0,
        )
    elif args.audit_only:
        completed = run(
            [
                sys.executable,
                "-X",
                "utf8",
                project_arg(PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "标准化数据稽核.py"),
                "--db-path",
                project_arg(DEFAULT_DB_PATH),
                "--site-dir",
                project_arg(args.report_root / "basic_data"),
                "--output-root",
                project_arg(standardized_audit_root),
                "--fail-on-error",
            ],
            env=env,
        )
    else:
        completed = run(
            [
                sys.executable,
                "-X",
                "utf8",
                project_arg(PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_basic_data_report_packs.py"),
                "--report-root",
                project_arg(args.report_root),
            ],
            env=env,
        )
    if report_path is None:
        report_path = latest_audit_report(standardized_audit_root)
        audit_report = read_json(report_path) if report_path else {}
    if report_path is None:
        issue = {
            "severity": "error",
            "ruleId": "DATA_AUDIT_REPORT_MISSING",
            "scope": "data_audit_hook",
            "item": "标准化稽核报告未生成",
            "detail": f"稽核命令退出码={completed.returncode}，专属输出目录中没有 data_audit_report.json。",
            "原因说明": "标准化稽核进程在报告落盘前异常退出，调度器无法区分数据错误与执行故障。",
            "优化建议": "检查标准化稽核控制台异常并修复；hook 已保留本次摘要，不得复用其他批次的旧报告。",
            "修复责任脚本": "节点脚本/_共享组件/生产程序/标准化数据稽核.py；节点脚本/_共享组件/生产程序/运行项目数据稽核hook.py",
            "修复责任节点": "data_audit",
        }
        summary = {
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": args.mode,
            "status": "error",
            "staticChecks": static_checks,
            "staticErrorCount": static_error_count,
            "auditReportPath": "",
            "auditSummary": {},
            "auditCommandReturnCode": completed.returncode,
            "businessQualityIssues": [],
            "ruleGovernanceIssues": [],
            "ruleCatalogPath": str(DEFAULT_RULES_PATH),
            "ruleCatalogCount": len(read_rule_catalog()),
            "persistenceLookbackRuns": PERSISTENCE_LOOKBACK_RUNS,
            "issues": [issue],
        }
        write_hook_summary(output_dir, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(completed.returncode or 2)

    manifest_script = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "write_analysis_platform_deploy_manifest.py"
    if not args.audit_only and manifest_script.exists():
        completed = run(
            [
                sys.executable,
                "-X",
                "utf8",
                project_arg(manifest_script),
                "--deploy-dir",
                project_arg(args.report_root),
                "--page-set",
                manifest_page_set,
            ],
            env=env,
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)

    rule_catalog = read_rule_catalog()
    audit_issues = audit_report.get("issues") or []
    business_quality_issues = read_business_quality_issues(args.report_root / "basic_data")
    issues = attach_persistence(audit_issues + business_quality_issues, args.output_root, output_dir)
    rule_governance_issues = governance_issues(issues, rule_catalog)
    issues = issues + rule_governance_issues
    issue_counter = Counter(issue.get("severity") for issue in issues)
    status = "error" if issue_counter["error"] else ("warn" if issue_counter["warn"] else "ok")
    summary = {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": args.mode,
        "status": status,
        "staticChecks": static_checks,
        "staticErrorCount": static_error_count,
        "auditReportPath": str(report_path) if report_path else "",
        "auditSummary": audit_report.get("summary") or {},
        "businessQualityIssues": business_quality_issues,
        "ruleGovernanceIssues": rule_governance_issues,
        "ruleCatalogPath": str(DEFAULT_RULES_PATH),
        "ruleCatalogCount": len(rule_catalog),
        "persistenceLookbackRuns": PERSISTENCE_LOOKBACK_RUNS,
        "issues": issues,
    }
    write_hook_summary(output_dir, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if status == "error":
        raise SystemExit(2)
    if args.fail_on_warn and status == "warn":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
