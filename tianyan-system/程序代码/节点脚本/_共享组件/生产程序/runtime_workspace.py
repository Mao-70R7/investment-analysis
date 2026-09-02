from __future__ import annotations

import json
import contextlib
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


CONFIG_RELATIVE_PATH = Path("本机配置") / "runtime.local.json"
TTFUND_PACKAGE = "com.eastmoney.android.fund"
TTFUND_CACHE_DIR = f"/sdcard/Android/data/{TTFUND_PACKAGE}/files/.ttjj_cache"

DEFAULT_CONFIG: dict[str, Any] = {
    "formatVersion": 1,
    "codeRoot": "程序代码",
    "databaseRoot": "数据库",
    "rawRoot": "采集数据/raw",
    "normalizedRoot": "采集数据/normalized",
    "logRoot": "运行状态/logs",
    "outputRoot": "运行状态/outputs",
    "lockRoot": "运行状态/locks",
    "tempRoot": "运行状态/temp",
    "reportRoot": "结果文件/全市场投顾分析平台",
    "publishRoot": "结果文件/最小发布集",
    "backupRoot": "数据库备份",
    "baselineRoot": "迁移基线",
    "devicePriority": ["physical"],
    "physicalDeviceId": "",
    "historyMode": "latest_only",
    "pagesBaseUrl": "https://mao-70r7.github.io/invest",
    "codeRemote": "https://github.com/Mao-70R7/investment-advisor-monitor.git",
    "codeBranch": "code",
    "codeUpdateMode": "git",
    "publishRemote": "https://github.com/Mao-70R7/invest.git",
    "publishBranch": "main",
    "publishGitUserName": "天眼系统自动更新",
    "publishGitUserEmail": "tianyan-system@local.invalid",
    "backupRetain": 1,
    "minimumFreeGiB": 45,
    "requiredPythonMajorMinor": [3, 12],
}

PATH_KEYS = (
    "codeRoot",
    "databaseRoot",
    "rawRoot",
    "normalizedRoot",
    "logRoot",
    "outputRoot",
    "lockRoot",
    "tempRoot",
    "reportRoot",
    "publishRoot",
    "backupRoot",
    "baselineRoot",
)


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def normalize_relative_path(value: object, key: str) -> PurePosixPath:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        raise ValueError(f"{key} must not be empty")
    if text.startswith("/") or text.startswith("//"):
        raise ValueError(f"{key} must be relative to the workspace root: {text}")
    if len(text) >= 2 and text[1] == ":":
        raise ValueError(f"{key} must not contain a drive letter: {text}")
    relative = PurePosixPath(text)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"{key} contains an unsafe relative path: {text}")
    return relative


def resolve_workspace_path(root: Path, value: object, key: str) -> Path:
    relative = normalize_relative_path(value, key)
    candidate = root.joinpath(*relative.parts).resolve(strict=False)
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError(f"{key} escapes the workspace root: {value}")
    return candidate


@dataclass(frozen=True)
class WorkspaceLayout:
    workspace_root: Path
    config_path: Path
    config: dict[str, Any]
    code_root: Path
    database_root: Path
    raw_root: Path
    normalized_root: Path
    log_root: Path
    output_root: Path
    lock_root: Path
    temp_root: Path
    report_root: Path
    publish_root: Path
    backup_root: Path
    baseline_root: Path

    @property
    def main_db(self) -> Path:
        return self.database_root / "analysis_zh_current.sqlite"

    @property
    def monitor_db(self) -> Path:
        return self.database_root / "advisor_monitor.sqlite"

    @property
    def state_db(self) -> Path:
        return self.database_root / "update_state.sqlite"

    @property
    def adb_path(self) -> Path:
        return self.code_root / "tools" / "platform-tools" / "adb.exe"

    @property
    def daily_lock(self) -> Path:
        return self.database_root / "daily_update.lock"

    @property
    def code_update_state_dir(self) -> Path:
        return self.output_root / "code_update"

    def relative_text(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.workspace_root).as_posix()


def load_workspace(
    workspace_root: Path,
    config_path: Path | None = None,
    *,
    require_config: bool = True,
) -> WorkspaceLayout:
    root = workspace_root.resolve()
    path = (config_path or (root / CONFIG_RELATIVE_PATH)).resolve(strict=False)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"workspace config must be a JSON object: {path}")
    elif require_config:
        raise FileNotFoundError(f"workspace config not found: {path}")
    else:
        payload = {}
    config = {**DEFAULT_CONFIG, **payload}
    paths = {key: resolve_workspace_path(root, config[key], key) for key in PATH_KEYS}
    priority = config.get("devicePriority")
    if priority != ["physical"]:
        raise ValueError("devicePriority must be exactly ['physical']; emulator devices are not supported")
    if str(config.get("historyMode") or "") not in {"latest_only", "all_missing", "none"}:
        raise ValueError("historyMode must be latest_only, all_missing or none")
    python_version = config.get("requiredPythonMajorMinor")
    if not isinstance(python_version, list) or len(python_version) != 2 or not all(
        isinstance(value, int) and value >= 0 for value in python_version
    ):
        raise ValueError("requiredPythonMajorMinor must contain two non-negative integers")
    return WorkspaceLayout(
        workspace_root=root,
        config_path=path,
        config=config,
        code_root=paths["codeRoot"],
        database_root=paths["databaseRoot"],
        raw_root=paths["rawRoot"],
        normalized_root=paths["normalizedRoot"],
        log_root=paths["logRoot"],
        output_root=paths["outputRoot"],
        lock_root=paths["lockRoot"],
        temp_root=paths["tempRoot"],
        report_root=paths["reportRoot"],
        publish_root=paths["publishRoot"],
        backup_root=paths["backupRoot"],
        baseline_root=paths["baselineRoot"],
    )


def create_default_config(workspace_root: Path, *, overwrite: bool = False) -> Path:
    path = workspace_root.resolve() / CONFIG_RELATIVE_PATH
    if path.exists() and not overwrite:
        return path
    atomic_write_json(path, DEFAULT_CONFIG)
    return path


def _same_target(link: Path, target: Path) -> bool:
    try:
        return link.resolve(strict=True) == target.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return False


def ensure_junction(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        if _same_target(link, target):
            return
        if link.is_dir() and not any(link.iterdir()):
            link.rmdir()
        else:
            raise RuntimeError(f"cannot replace non-empty runtime bridge: {link}")
    link.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0 or not _same_target(link, target):
        detail = (completed.stdout + completed.stderr).strip()
        raise RuntimeError(f"failed to create runtime junction {link} -> {target}: {detail}")


def ensure_workspace_directories(layout: WorkspaceLayout) -> None:
    for path in (
        layout.database_root,
        layout.raw_root,
        layout.normalized_root,
        layout.log_root,
        layout.output_root,
        layout.lock_root,
        layout.temp_root,
        layout.report_root,
        layout.publish_root.parent,
        layout.backup_root,
        layout.baseline_root,
    ):
        path.mkdir(parents=True, exist_ok=True)


def render_legacy_local_bat(layout: WorkspaceLayout) -> str:
    workspace_from_code = Path(os.path.relpath(layout.workspace_root, layout.code_root))
    workspace_expr = str(workspace_from_code).replace("/", "\\")
    if workspace_expr == ".":
        workspace_expr = ""
    elif not workspace_expr.endswith("\\"):
        workspace_expr += "\\"

    def env_path(path: Path) -> str:
        relative = layout.relative_text(path).replace("/", "\\")
        return f"%ADVISOR_WORKSPACE_ROOT%\\{relative}"

    lines = [
        "@echo off",
        'for %%I in ("%~dp0.") do set "ADVISOR_MONITOR_ROOT=%%~fI"',
        f'for %%I in ("%~dp0{workspace_expr}.") do set "ADVISOR_WORKSPACE_ROOT=%%~fI"',
        f'set "ADVISOR_REPORT_ROOT={env_path(layout.report_root)}"',
        'set "ADVISOR_DEPLOY_SITE_DIR=%ADVISOR_REPORT_ROOT%"',
        f'set "ADVISOR_ADB_EXE={env_path(layout.adb_path)}"',
        f'set "ADVISOR_DEVICE_ID={str(layout.config.get("physicalDeviceId") or "")}"',
        f'set "ADVISOR_HISTORY_MODE={str(layout.config.get("historyMode") or "latest_only")}"',
        'set "ADVISOR_PYTHON_EXE=%ADVISOR_WORKSPACE_ROOT%\\运行环境\\python\\Scripts\\python.exe"',
        'set "ADVISOR_NODE_EXE=node"',
        'set "ADVISOR_UNATTENDED=1"',
        'set "ADVISOR_DEPLOY_PAGE_SET=all"',
        'set "ADVISOR_INDEX_QUOTE_LOOKBACK_DAYS=30"',
        "",
    ]
    return "\r\n".join(lines)


def ensure_runtime_bridges(layout: WorkspaceLayout) -> None:
    ensure_workspace_directories(layout)
    if layout.code_root.resolve() == layout.workspace_root.resolve():
        return
    if not layout.code_root.exists():
        raise FileNotFoundError(f"code root not found: {layout.code_root}")
    ensure_junction(layout.database_root / "raw", layout.raw_root)
    ensure_junction(layout.database_root / "normalized", layout.normalized_root)
    ensure_junction(layout.database_root / "tmp", layout.temp_root / "data")
    ensure_junction(layout.database_root / "backups", layout.backup_root)
    ensure_junction(layout.code_root / "data", layout.database_root)
    ensure_junction(layout.code_root / "logs", layout.log_root)
    ensure_junction(layout.code_root / "outputs", layout.output_root)
    atomic_write_text(
        layout.workspace_root / "本机配置" / "advisor_update.local.bat",
        render_legacy_local_bat(layout),
        encoding="utf-8-sig",
    )


def sqlite_metadata(path: Path, *, quick_check: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.is_file(), "bytes": path.stat().st_size if path.exists() else 0}
    if not path.is_file():
        return result
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=30)) as connection:
            result["userVersion"] = int(connection.execute("PRAGMA user_version").fetchone()[0])
            result["tableCount"] = int(
                connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            )
            if quick_check:
                result["quickCheck"] = connection.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    return result


def command_version(command: str, args: list[str]) -> dict[str, Any]:
    resolved = shutil.which(command)
    result: dict[str, Any] = {"command": command, "resolved": resolved, "available": bool(resolved)}
    if not resolved:
        return result
    try:
        completed = subprocess.run(
            [resolved, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        result["returncode"] = completed.returncode
        result["version"] = (completed.stdout or completed.stderr).strip().splitlines()[0]
    except Exception as exc:
        result["error"] = str(exc)
    return result


def inspect_workspace(layout: WorkspaceLayout, *, quick_check: bool = False) -> dict[str, Any]:
    usage = shutil.disk_usage(layout.workspace_root)
    minimum_free = float(layout.config.get("minimumFreeGiB") or 45)
    checks = {
        "generatedAt": now_text(),
        "workspaceRoot": str(layout.workspace_root),
        "configPath": str(layout.config_path),
        "paths": {key: layout.relative_text(getattr(layout, _attribute_name(key))) for key in PATH_KEYS},
        "commands": {
            "python": command_version("python", ["--version"]),
            "node": command_version("node", ["--version"]),
            "git": command_version("git", ["--version"]),
        },
        "pythonRuntime": {
            "version": list(os.sys.version_info[:3]),
            "requiredMajorMinor": layout.config.get("requiredPythonMajorMinor") or [3, 12],
        },
        "pythonDependencies": {
            name: bool(importlib.util.find_spec(name))
            for name in (
                "requests",
                "pandas",
                "numpy",
                "openpyxl",
                "matplotlib",
                "seaborn",
                "pdfplumber",
                "playwright",
                "bs4",
                "lxml",
                "PIL",
                "reportlab",
                "akshare",
                "pypdf",
                "yaml",
                "xlsxwriter",
                "mitmproxy",
            )
        },
        "adb": {"path": str(layout.adb_path), "exists": layout.adb_path.is_file()},
        "databases": {
            "analysis": sqlite_metadata(layout.main_db, quick_check=quick_check),
            "monitor": sqlite_metadata(layout.monitor_db),
            "state": sqlite_metadata(layout.state_db),
        },
        "disk": {
            "freeGiB": round(usage.free / (1024**3), 2),
            "totalGiB": round(usage.total / (1024**3), 2),
            "minimumFreeGiB": minimum_free,
            "enough": usage.free >= minimum_free * (1024**3),
        },
    }
    errors: list[str] = []
    if not layout.code_root.is_dir():
        errors.append("code_root_missing")
    required_python = tuple(int(value) for value in checks["pythonRuntime"]["requiredMajorMinor"])
    if tuple(os.sys.version_info[:2]) != required_python:
        errors.append(f"python_version_mismatch:required={required_python[0]}.{required_python[1]}")
    for name, command in checks["commands"].items():
        if not command["available"]:
            errors.append(f"command_missing:{name}")
    for name, available in checks["pythonDependencies"].items():
        if not available:
            errors.append(f"python_dependency_missing:{name}")
    if not layout.adb_path.is_file():
        errors.append("bundled_adb_missing")
    if not checks["databases"]["analysis"]["exists"]:
        errors.append("analysis_database_missing")
    if checks["databases"]["analysis"].get("error"):
        errors.append("analysis_database_unreadable")
    if quick_check and checks["databases"]["analysis"].get("quickCheck") != "ok":
        errors.append("analysis_database_quick_check_failed")
    if not checks["disk"]["enough"]:
        errors.append("insufficient_free_disk")
    checks["errors"] = errors
    checks["status"] = "ready" if not errors else "blocked"
    return checks


def _attribute_name(key: str) -> str:
    mapping = {
        "codeRoot": "code_root",
        "databaseRoot": "database_root",
        "rawRoot": "raw_root",
        "normalizedRoot": "normalized_root",
        "logRoot": "log_root",
        "outputRoot": "output_root",
        "lockRoot": "lock_root",
        "tempRoot": "temp_root",
        "reportRoot": "report_root",
        "publishRoot": "publish_root",
        "backupRoot": "backup_root",
        "baselineRoot": "baseline_root",
    }
    return mapping[key]


def sqlite_backup(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temp_sidecars = (Path(f"{temp}-shm"), Path(f"{temp}-wal"))
    for path in (temp, *temp_sidecars):
        path.unlink(missing_ok=True)
    try:
        source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=60)
        target_conn = sqlite3.connect(temp, timeout=60)
        try:
            last_percent = -5

            def report_progress(_status: int, remaining: int, total: int) -> None:
                nonlocal last_percent
                percent = int((total - remaining) * 100 / max(1, total))
                if percent >= last_percent + 5 or remaining == 0:
                    print(
                        f"[SQLite备份] {target.name}：{percent}% "
                        f"({total - remaining}/{total} 页)",
                        flush=True,
                    )
                    last_percent = percent

            source_conn.backup(target_conn, pages=8192, sleep=0.05, progress=report_progress)
            target_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            target_conn.close()
            source_conn.close()
        check_conn = sqlite3.connect(
            f"file:{temp.as_posix()}?mode=ro&immutable=1",
            uri=True,
            timeout=60,
        )
        try:
            result = check_conn.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"SQLite backup quick_check failed: {result}")
        finally:
            check_conn.close()
        os.replace(temp, target)
    finally:
        for path in (temp, *temp_sidecars):
            path.unlink(missing_ok=True)
