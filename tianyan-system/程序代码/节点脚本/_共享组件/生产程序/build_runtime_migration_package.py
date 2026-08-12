from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import ctypes
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from check_runtime_path_portability import scan as scan_portability
from runtime_workspace import DEFAULT_CONFIG, atomic_write_json, sqlite_backup
from report_periods import monthly_rebalance_asset_directory, monthly_rebalance_report_page, previous_completed_month


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
CODE_BRANCH_DEFAULT = "code"
PUBLISH_REMOTE_DEFAULT = "https://github.com/Mao-70R7/invest.git"
PUBLISH_BRANCH_DEFAULT = "main"
MONTHLY_REBALANCE_REPORT_MONTH = previous_completed_month()
STATIC_REPORT_PAGE = monthly_rebalance_report_page(MONTHLY_REBALANCE_REPORT_MONTH)
STATIC_REPORT_ASSET_DIR = monthly_rebalance_asset_directory(MONTHLY_REBALANCE_REPORT_MONTH)
CHECKSUM_EXCLUDED_PATHS = {
    "数据库/update_state.sqlite",
    "数据库/update_state.sqlite-shm",
    "数据库/update_state.sqlite-wal",
    "数据库/analysis_zh_current.sqlite-shm",
    "数据库/analysis_zh_current.sqlite-wal",
}
CHECKSUM_EXCLUDED_PREFIXES = (
    "运行状态/",
    "运行环境/",
    "数据库备份/",
    "采集数据/",
    "结果文件/",
    "工具与归档/",
    "数据库/runtime_health/",
)

RUNTIME_ROOT_FILES = (
    ".gitattributes",
    ".gitignore",
    "00_每日数据更新并发布_唯一入口.bat",
    "AGENTS.md",
    "README_AI.md",
)

RUNTIME_PREFIXES = (
    "节点脚本/",
    "文档/",
    "config/",
    "schemas/",
    "official_apps/",
    "tools/platform-tools/",
    "basic_data/assets/",
    "basic_data/config/",
    "业务基线/",
)

SPARSE_PATTERNS = (
    *('/' + name for name in RUNTIME_ROOT_FILES),
    "/节点脚本/",
    "/文档/",
    "/config/",
    "/schemas/",
    "/official_apps/",
    "/tools/platform-tools/",
    "/basic_data/*.html",
    "/basic_data/assets/",
    "/basic_data/config/",
    "/业务基线/",
)


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the relocatable advisor-monitoring runtime workspace.")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--code-remote", default="")
    parser.add_argument("--code-branch", default=CODE_BRANCH_DEFAULT)
    parser.add_argument("--publish-remote", default=PUBLISH_REMOTE_DEFAULT)
    parser.add_argument("--publish-branch", default=PUBLISH_BRANCH_DEFAULT)
    parser.add_argument("--physical-device-id", default=os.environ.get("ADVISOR_DEVICE_ID") or "")
    parser.add_argument(
        "--code-source",
        choices=("git", "working-tree"),
        default="git",
        help="Package a clean Git checkout or the current controlled runtime working-tree snapshot.",
    )
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--skip-checksums", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        input=input_text,
        check=False,
    )
    if completed.returncode:
        detail = (completed.stdout + completed.stderr).strip()
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{detail}")
    return completed


def git_text(*args: str) -> str:
    return run(["git", *args], cwd=PROJECT_ROOT, timeout=300).stdout.strip()


def runtime_path(path_text: str) -> bool:
    normalized = path_text.replace("\\", "/").strip('"')
    if normalized in RUNTIME_ROOT_FILES:
        return True
    if normalized.startswith("basic_data/") and normalized.count("/") == 1 and normalized.endswith(".html"):
        return True
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in RUNTIME_PREFIXES)


def parse_status_path(line: str) -> str:
    path = line[3:].strip() if len(line) > 3 else ""
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip('"')


def validate_source_code(args: argparse.Namespace) -> dict[str, Any]:
    portability = scan_portability(PROJECT_ROOT)
    if portability["status"] != "ready":
        details = "\n".join(
            f"{issue['file']}:{issue['line']} {issue['text']}" for issue in portability["issues"]
        )
        raise RuntimeError(f"runtime path portability scan failed:\n{details}")
    branch = git_text("branch", "--show-current")
    if branch != args.code_branch:
        raise RuntimeError(f"source branch must be {args.code_branch}; current branch is {branch or 'detached'}")
    dirty_lines = git_text("status", "--porcelain", "--untracked-files=all").splitlines()
    runtime_dirty = [line for line in dirty_lines if runtime_path(parse_status_path(line))]
    if runtime_dirty and args.code_source == "git":
        raise RuntimeError(
            "runtime code has uncommitted changes and cannot be packaged for Git-based updates:\n"
            + "\n".join(runtime_dirty)
        )
    remote = args.code_remote or git_text("remote", "get-url", "origin")
    if args.code_source == "git" and not args.skip_fetch:
        run(["git", "fetch", "--prune", "origin", args.code_branch], cwd=PROJECT_ROOT, timeout=300)
    head = git_text("rev-parse", "HEAD")
    remote_head = ""
    remote_ref = subprocess.run(
        ["git", "rev-parse", "--verify", f"origin/{args.code_branch}"],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )
    if remote_ref.returncode == 0:
        remote_head = remote_ref.stdout.strip()
    if args.code_source == "git" and head != remote_head:
        raise RuntimeError(
            f"local code commit {head[:12]} is not the published origin/{args.code_branch} {remote_head[:12]}"
        )
    return {
        "sourceMode": args.code_source,
        "remote": remote,
        "branch": branch,
        "commit": head,
        "remoteCommit": remote_head,
        "runtimeDirtyPaths": [parse_status_path(line) for line in runtime_dirty],
    }


def ensure_source_idle() -> None:
    locks = [
        PROJECT_ROOT / "data" / "daily_update.lock",
        PROJECT_ROOT / "data" / "run_daily_incremental.lock",
        PROJECT_ROOT / "data" / "update.lock",
    ]
    active = [str(path) for path in locks if path.exists()]
    if active:
        raise RuntimeError(f"source update lock exists; package creation is blocked: {active}")
    for name in ("analysis_zh_current.sqlite", "advisor_monitor.sqlite", "update_state.sqlite"):
        path = PROJECT_ROOT / "data" / name
        if not path.is_file():
            continue
        connection = sqlite3.connect(path, timeout=0, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(f"SQLite database is currently write-locked: {path}: {exc}") from exc
        finally:
            connection.close()


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextlib.contextmanager
def migration_package_lock():
    path = PROJECT_ROOT / "data" / "migration_package.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            current = {}
        if process_exists(int(current.get("pid") or 0)):
            raise RuntimeError(f"another migration package build is active: {path}")
        path.unlink(missing_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(descriptor, json.dumps({"pid": os.getpid(), "startedAt": now_local().isoformat()}).encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        yield
    finally:
        try:
            current = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            current = {}
        if int(current.get("pid") or 0) == os.getpid():
            path.unlink(missing_ok=True)


def safe_destination(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    if resolved.exists() and any(resolved.iterdir()):
        raise RuntimeError(f"destination must not exist or must be empty: {resolved}")
    if resolved == PROJECT_ROOT or PROJECT_ROOT in resolved.parents and resolved.name in {"data", "节点脚本"}:
        raise RuntimeError(f"unsafe migration destination: {resolved}")
    return resolved


def clone_sparse_code(target: Path, git_info: dict[str, str]) -> None:
    run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--no-checkout",
            "--branch",
            git_info["branch"],
            git_info["remote"],
            str(target),
        ],
        timeout=1200,
    )
    run(["git", "sparse-checkout", "init", "--no-cone"], cwd=target)
    run(
        ["git", "sparse-checkout", "set", "--no-cone", "--stdin"],
        cwd=target,
        input_text="\n".join(SPARSE_PATTERNS) + "\n",
    )
    run(["git", "checkout", git_info["branch"]], cwd=target, timeout=600)
    actual = run(["git", "rev-parse", "HEAD"], cwd=target).stdout.strip()
    if actual != git_info["commit"]:
        raise RuntimeError(f"sparse code checkout commit mismatch: {actual} != {git_info['commit']}")


def copy_working_tree_code(target: Path) -> dict[str, Any]:
    copied: list[str] = []

    def copy_runtime_file(source: Path, relative: Path) -> None:
        if source.suffix.lower() in {".pyc", ".pyo"} or "__pycache__" in relative.parts:
            return
        copy_file(source, target / relative)
        copied.append(relative.as_posix())

    for name in RUNTIME_ROOT_FILES:
        source = PROJECT_ROOT / name
        if source.is_file():
            copy_runtime_file(source, Path(name))
    for prefix in RUNTIME_PREFIXES:
        relative_root = Path(prefix.rstrip("/"))
        source_root = PROJECT_ROOT / relative_root
        if source_root.is_file():
            copy_runtime_file(source_root, relative_root)
            continue
        if not source_root.is_dir():
            continue
        for source in source_root.rglob("*"):
            if source.is_file():
                copy_runtime_file(source, relative_root / source.relative_to(source_root))
    basic_data_root = PROJECT_ROOT / "basic_data"
    if basic_data_root.is_dir():
        for source in basic_data_root.glob("*.html"):
            copy_runtime_file(source, Path("basic_data") / source.name)
    required = (
        target / "00_每日数据更新并发布_唯一入口.bat",
        target / "AGENTS.md",
        target / "README_AI.md",
        target / "节点脚本" / "pipeline.json",
        target / "节点脚本" / "00_调度框架" / "启动.ps1",
        target / "节点脚本" / "00_调度框架" / "orchestrator.py",
        target / "节点脚本" / "_共享组件" / "生产程序" / "runtime_workspace_cli.py",
        target / "config" / "runtime" / "requirements-runtime.txt",
        target / "config" / "runtime" / "runtime_compatibility.json",
    )
    missing = [str(path.relative_to(target)) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"working-tree runtime snapshot is incomplete: {missing}")
    return {"fileCount": len(copied), "gitMetadataIncluded": False}


def copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_tree(source: Path, target: Path) -> int:
    if not source.is_dir():
        return 0
    count = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        count += 1
    return count


def complete_collection_summary(channel_root: Path, channel: str) -> tuple[Path, dict[str, Any]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in (channel_root / "collection_summary").rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if channel == "ttfund":
            complete = bool(payload.get("run_id") and payload.get("strategy_master_rows") and payload.get("daily_rows_total"))
        else:
            complete = payload.get("collection_status") == "success" and bool(
                payload.get("run_id") and payload.get("strategy_total") and payload.get("daily_rows_total")
            )
        if complete:
            candidates.append((path, payload))
    if not candidates:
        raise RuntimeError(f"no complete normalized collection summary found for {channel}")
    return max(candidates, key=lambda item: item[0].stat().st_mtime_ns)


def copy_normalized_baseline(source_root: Path, target_root: Path, channel: str) -> dict[str, Any]:
    channel_root = source_root / channel
    summary_path, summary = complete_collection_summary(channel_root, channel)
    run_id = str(summary["run_id"])
    day = summary_path.parent.name
    copied: list[str] = []
    for entity_dir in channel_root.iterdir():
        if not entity_dir.is_dir():
            continue
        exact = list(entity_dir.rglob(f"{run_id}.*"))
        files = [path for path in exact if path.is_file()]
        if not files:
            candidates = [path for path in entity_dir.rglob("*") if path.is_file()]
            files = [max(candidates, key=lambda path: path.stat().st_mtime_ns)] if candidates else []
        for source in files:
            destination = target_root / channel / source.relative_to(channel_root)
            copy_file(source, destination)
            copied.append(str(destination.relative_to(target_root)))
    return {"channel": channel, "day": day, "runId": run_id, "files": copied}


def latest_run_directory(root: Path) -> Path | None:
    candidates = [path for path in root.glob("*/*") if path.is_dir()]
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def copy_selected_raw(source_raw: Path, target_raw: Path) -> dict[str, Any]:
    copied: dict[str, Any] = {"trees": [], "latestRuns": []}
    for relative in (
        Path("device_cache"),
        Path("fund_lookthrough"),
        Path("gffunds") / "protocol_cache",
    ):
        source = source_raw / relative
        count = copy_tree(source, target_raw / relative)
        if count:
            copied["trees"].append({"path": relative.as_posix(), "fileCount": count})
    for relative in (
        Path("ttfund") / "loggedin_cache",
        Path("ttfund") / "incremental_update_runs",
        Path("ttfund") / "official_performance_curve",
        Path("ttfund") / "interface_probe",
        Path("gffunds") / "strategy_metadata",
    ):
        source = latest_run_directory(source_raw / relative)
        if source:
            destination = target_raw / relative / source.relative_to(source_raw / relative)
            count = copy_tree(source, destination)
            copied["latestRuns"].append(
                {
                    "path": relative.as_posix(),
                    "run": source.name,
                    "relativePath": source.relative_to(source_raw).as_posix(),
                    "fileCount": count,
                }
            )
    latest_files = (
        Path("fof_f10_benchmark") / "latest_fof_f10_benchmarks.json",
        Path("fund_f10_benchmark") / "latest_fund_f10_benchmarks.json",
    )
    for relative in latest_files:
        source = source_raw / relative
        if source.is_file():
            copy_file(source, target_raw / relative)
            copied.setdefault("files", []).append(relative.as_posix())
    return copied


def find_latest(root: Path, name: str) -> Path | None:
    if not root.exists():
        return None
    candidates = list(root.rglob(name))
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def sanitize_baseline_payload(value: Any, report_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_baseline_payload(item, report_root) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_baseline_payload(item, report_root) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    replacements = (
        (PROJECT_ROOT / "data" / "raw", "采集数据/raw"),
        (PROJECT_ROOT / "data" / "normalized", "采集数据/normalized"),
        (PROJECT_ROOT / "data" / "backups", "数据库备份"),
        (PROJECT_ROOT / "data", "数据库"),
        (PROJECT_ROOT / "logs", "运行状态/logs"),
        (PROJECT_ROOT / "outputs", "运行状态/outputs"),
        (report_root, "结果文件/全市场投顾分析平台"),
        (PROJECT_ROOT, "程序代码"),
    )
    for source, relative in replacements:
        text = text.replace(str(source), relative).replace(source.as_posix(), relative)
    if re.search(r"(?i)\b[A-Z]:[\\/]", text):
        return "<source-local-path-redacted>"
    return text.replace("\\", "/") if any(marker in text for marker in ("程序代码", "数据库", "采集数据", "运行状态", "结果文件")) else text


def copy_baseline_reports(target: Path, report_root: Path) -> dict[str, str]:
    sources = {
        "latest_daily_summary.json": find_latest(PROJECT_ROOT / "logs" / "daily_update", "summary.json"),
        "latest_audit_report.json": find_latest(PROJECT_ROOT / "outputs" / "data_audit", "data_audit_report.json"),
        "latest_hook_summary.json": find_latest(PROJECT_ROOT / "outputs" / "data_audit_hook", "hook_summary.json"),
    }
    copied: dict[str, str] = {}
    for name, source in sources.items():
        if source:
            payload = json.loads(source.read_text(encoding="utf-8-sig"))
            atomic_write_json(target / name, sanitize_baseline_payload(payload, report_root))
            copied[name] = source.relative_to(PROJECT_ROOT).as_posix()
    return copied


def copy_report_seed(source_root: Path, target_root: Path) -> dict[str, Any]:
    page = source_root / "basic_data" / STATIC_REPORT_PAGE
    assets = source_root / "basic_data" / "assets" / STATIC_REPORT_ASSET_DIR
    copy_file(page, target_root / "basic_data" / STATIC_REPORT_PAGE)
    asset_count = copy_tree(assets, target_root / "basic_data" / "assets" / STATIC_REPORT_ASSET_DIR)
    if not asset_count:
        raise RuntimeError(f"static report assets are missing: {assets}")
    return {"page": f"basic_data/{STATIC_REPORT_PAGE}", "assetDirectory": f"basic_data/assets/{STATIC_REPORT_ASSET_DIR}", "assetFileCount": asset_count}


def database_summary(path: Path) -> dict[str, Any]:
    with contextlib.closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=60)) as connection:
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts: dict[str, int] = {}
        for name in (
            "策略信息",
            "策略标准业绩净值",
            "策略当前持仓",
            "策略调仓事件",
            "基金信息",
            "基金日度净值",
        ):
            if name in tables:
                counts[name] = int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        watermarks: dict[str, str | None] = {}
        channel_watermarks: dict[str, dict[str, str | None]] = {}
        for name, column in (
            ("策略标准业绩净值", "交易日期"),
            ("策略当前持仓", "持仓日期"),
            ("策略调仓事件", "调仓日期"),
            ("基金信息", "最新净值日期"),
            ("基金日度净值", "交易日期"),
        ):
            if name in tables:
                columns = {
                    str(row[1])
                    for row in connection.execute(f'PRAGMA table_info("{name}")').fetchall()
                }
                if column not in columns:
                    raise RuntimeError(f"watermark column is missing: {name}.{column}")
                row = connection.execute(
                    f'''SELECT MAX(CASE WHEN "{column}" GLOB '????-??-??' THEN "{column}" END) FROM "{name}"'''
                ).fetchone()
                watermarks[name] = str(row[0]) if row and row[0] is not None else None
                if name.startswith("策略"):
                    if "渠道ID" not in columns:
                        raise RuntimeError(f"channel watermark column is missing: {name}.渠道ID")
                    rows = connection.execute(
                        f'''SELECT "渠道ID", MAX(CASE WHEN "{column}" GLOB '????-??-??' THEN "{column}" END)
                            FROM "{name}" GROUP BY "渠道ID"'''
                    ).fetchall()
                    channel_watermarks[name] = {
                        str(channel): str(value) if value is not None else None
                        for channel, value in rows
                    }
        return {
            "path": "数据库/analysis_zh_current.sqlite",
            "bytes": path.stat().st_size,
            "quickCheck": quick,
            "userVersion": int(connection.execute("PRAGMA user_version").fetchone()[0]),
            "tableCount": len(tables),
            "rowCounts": counts,
            "latestBusinessDates": watermarks,
            "channelLatestBusinessDates": channel_watermarks,
        }


def sha256_file(path: Path, progress: Callable[[int], None] | None = None) -> str:
    digest = hashlib.sha256()
    completed = 0
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
            completed += len(chunk)
            if progress:
                progress(completed)
    return digest.hexdigest()


def checksum_prefix_excluded(relative: str) -> bool:
    return any(
        relative == prefix.rstrip("/") or relative.startswith(prefix)
        for prefix in CHECKSUM_EXCLUDED_PREFIXES
    )


def checksum_directory_excluded(path: Path, root: Path) -> bool:
    relative = path.relative_to(root).as_posix()
    is_junction = bool(getattr(path, "is_junction", lambda: False)())
    return is_junction or checksum_prefix_excluded(relative)


def write_checksums(root: Path) -> Path:
    output = root / "迁移基线" / "checksums.sha256"
    output.parent.mkdir(parents=True, exist_ok=True)
    candidates: list[Path] = []
    for current_root, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_root)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in {".git", "__pycache__", ".pytest_cache"}
            and not checksum_directory_excluded(current / name, root)
        )
        candidates.extend(current / name for name in sorted(file_names))
    lines: list[str] = []
    candidates = sorted(candidates, key=lambda item: item.as_posix())
    for index, path in enumerate(candidates, 1):
        relative = path.relative_to(root).as_posix()
        if (
            ".git" in path.parts
            or "__pycache__" in path.parts
            or ".pytest_cache" in path.parts
            or path.suffix.lower() in {".pyc", ".pyo"}
            or path.name.startswith(".") and ".tmp-" in path.name
            or path == output
            or relative in CHECKSUM_EXCLUDED_PATHS
            or checksum_prefix_excluded(relative)
        ):
            continue
        size = path.stat().st_size
        next_report = 1024**3

        def report_file_progress(completed: int) -> None:
            nonlocal next_report
            if size < 1024**3 or completed < next_report:
                return
            print(
                f"[迁移校验清单] 文件 {index}/{len(candidates)}，{relative}，"
                f"已读取 {completed / (1024**3):.1f}/{size / (1024**3):.1f} GiB",
                flush=True,
            )
            next_report += 1024**3

        lines.append(f"{sha256_file(path, report_file_progress)}  {relative}")
        if index == 1 or index % 100 == 0 or index == len(candidates):
            print(f"[迁移校验清单] 已完成 {index}/{len(candidates)} 个候选文件", flush=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def package_templates(code_root: Path, workspace_root: Path, *, code_source: str) -> None:
    del code_source
    for name in ("00_每日数据更新并发布_唯一入口.bat", "AGENTS.md", "README_AI.md"):
        copy_file(code_root / name, workspace_root / name)


def build_locked(args: argparse.Namespace) -> Path:
    ensure_source_idle()
    git_info = validate_source_code(args)
    report_root = args.report_root.resolve()
    if not (report_root / "basic_data").is_dir():
        raise FileNotFoundError(f"formal report basic_data is missing: {report_root / 'basic_data'}")
    destination = args.destination or (
        PROJECT_ROOT
        / "迁移产物"
        / f"投顾监控运行工作区_{now_local().strftime('%Y%m%d_%H%M%S')}"
    )
    destination = safe_destination(destination)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "ready_to_build",
                    "destination": str(destination),
                    "reportRoot": str(report_root),
                    "git": git_info,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return destination

    stage = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex[:10]}"
    if stage.exists():
        raise RuntimeError(f"unexpected staging directory exists: {stage}")
    stage.mkdir(parents=True)
    try:
        code_root = stage / "程序代码"
        if args.code_source == "git":
            clone_sparse_code(code_root, git_info)
            code_snapshot = {"fileCount": None, "gitMetadataIncluded": True}
        else:
            code_snapshot = copy_working_tree_code(code_root)
        package_templates(code_root, stage, code_source=args.code_source)
        documentation_file_count = copy_tree(code_root / "文档", stage / "文档")

        database_root = stage / "数据库"
        database_root.mkdir(parents=True)
        database_files: dict[str, Any] = {}
        for name in ("analysis_zh_current.sqlite", "advisor_monitor.sqlite", "update_state.sqlite"):
            source = PROJECT_ROOT / "data" / name
            if source.is_file():
                target = database_root / name
                sqlite_backup(source, target)
                database_files[name] = {"bytes": target.stat().st_size}

        normalized_root = stage / "采集数据" / "normalized"
        normalized = [
            copy_normalized_baseline(PROJECT_ROOT / "data" / "normalized", normalized_root, channel)
            for channel in ("ttfund", "gffunds")
        ]
        raw = copy_selected_raw(PROJECT_ROOT / "data" / "raw", stage / "采集数据" / "raw")

        report_seed = copy_report_seed(
            report_root,
            stage / "结果文件" / "全市场投顾分析平台",
        )

        publish_root = stage / "结果文件" / "最小发布集"
        run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                args.publish_branch,
                args.publish_remote,
                str(publish_root),
            ],
            timeout=1200,
        )

        config = {
            **DEFAULT_CONFIG,
            "physicalDeviceId": args.physical_device_id,
            "codeRemote": git_info["remote"],
            "codeBranch": git_info["branch"],
            "codeUpdateMode": "git" if args.code_source == "git" else "syncthing_snapshot",
            "publishRemote": args.publish_remote,
            "publishBranch": args.publish_branch,
        }
        atomic_write_json(stage / "本机配置" / "runtime.local.json", config)
        baseline_root = stage / "迁移基线"
        baseline_root.mkdir(parents=True, exist_ok=True)
        baseline_reports = copy_baseline_reports(baseline_root, report_root)
        deployment_manifest = report_root / "deployment_manifest.json"
        if deployment_manifest.is_file():
            deployment_payload = json.loads(deployment_manifest.read_text(encoding="utf-8-sig"))
            deployment_payload["deployDir"] = "结果文件/全市场投顾分析平台"
            atomic_write_json(baseline_root / "latest_deployment_manifest.json", deployment_payload)
            baseline_reports["latest_deployment_manifest.json"] = "external_formal_report_baseline"
        db_summary = database_summary(database_root / "analysis_zh_current.sqlite")
        manifest = {
            "formatVersion": 1,
            "status": "ready",
            "generatedAt": now_local().isoformat(timespec="seconds"),
            "workspaceRoot": ".",
            "code": git_info,
            "codeSnapshot": code_snapshot,
            "publish": {"remote": args.publish_remote, "branch": args.publish_branch},
            "database": db_summary,
            "databaseFiles": database_files,
            "normalizedBaselines": normalized,
            "rawSelection": raw,
            "reportSeed": report_seed,
            "baselineReports": baseline_reports,
            "reportSource": "external_formal_report_baseline",
            "documentationFileCount": documentation_file_count,
        }
        atomic_write_json(baseline_root / "migration_manifest.json", manifest)
        if not args.skip_checksums:
            write_checksums(stage)
        if destination.exists():
            destination.rmdir()
        os.replace(stage, destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return destination


def build(args: argparse.Namespace) -> Path:
    with migration_package_lock():
        return build_locked(args)


def main() -> None:
    args = parse_args()
    try:
        destination = build(args)
        print(json.dumps({"status": "ready", "destination": str(destination)}, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"[FAILED] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
