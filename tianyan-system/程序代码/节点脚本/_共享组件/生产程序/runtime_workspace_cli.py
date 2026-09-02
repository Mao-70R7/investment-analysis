from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import py_compile
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from check_runtime_path_portability import scan as scan_portability
from runtime_workspace import (
    TTFUND_CACHE_DIR,
    TTFUND_PACKAGE,
    WorkspaceLayout,
    atomic_write_json,
    create_default_config,
    ensure_runtime_bridges,
    inspect_workspace,
    load_workspace,
    normalize_relative_path,
    now_text,
    sqlite_backup,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the relocatable advisor monitoring runtime workspace.")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("initialize", help="Create runtime bridges and validate the new PC.")
    initialize.add_argument("--skip-baseline-backup", action="store_true")
    initialize.add_argument("--quick-check", action="store_true")
    initialize.add_argument("--skip-device-check", action="store_true")
    initialize.add_argument("--skip-network-check", action="store_true")
    initialize.add_argument("--skip-checksums", action="store_true")

    check = subparsers.add_parser("check", help="Validate the workspace without running a daily update.")
    check.add_argument("--quick-check", action="store_true")
    check.add_argument("--check-devices", action="store_true")
    check.add_argument("--check-network", action="store_true")

    daily = subparsers.add_parser("daily", help="Select a device and run the complete daily workflow.")
    daily.add_argument("--dry-run", action="store_true")
    daily.add_argument("--skip-readiness", action="store_true")
    daily.add_argument("--skip-backup", action="store_true")
    daily.add_argument("--skip-publish", action="store_true")
    daily.add_argument("--skip-wait", action="store_true")
    daily.add_argument("--device-id", default="")
    daily.add_argument("--device-type", choices=("auto", "physical"), default="physical")
    daily.add_argument("--resume-run-id", default="")

    update = subparsers.add_parser("update-code", help="Fast-forward the code-only Git checkout.")
    update.add_argument("--skip-tests", action="store_true")

    subparsers.add_parser("rollback-code", help="Restore the previous validated code commit.")
    return parser.parse_args()


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
        timeout=timeout,
        check=False,
    )


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


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


def verify_workspace_checksums(layout: WorkspaceLayout) -> dict[str, Any]:
    manifest = layout.baseline_root / "checksums.sha256"
    if not manifest.is_file():
        return {"status": "blocked", "manifest": str(manifest), "errors": ["checksum_manifest_missing"]}
    entries: list[tuple[str, str]] = []
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            return {
                "status": "blocked",
                "manifest": str(manifest),
                "errors": [f"invalid_checksum_line:{line_number}"],
            }
        entries.append((parts[0].lower(), parts[1]))
    errors: list[str] = []
    checked_bytes = 0
    for index, (expected, relative_text) in enumerate(entries, 1):
        relative = Path(relative_text.replace("/", os.sep))
        target = (layout.workspace_root / relative).resolve(strict=False)
        if target != layout.workspace_root and layout.workspace_root not in target.parents:
            errors.append(f"checksum_path_escape:{relative_text}")
            continue
        if not target.is_file():
            errors.append(f"checksum_file_missing:{relative_text}")
            continue
        target_size = target.stat().st_size
        next_report = 1024**3

        def report_large_file(completed: int) -> None:
            nonlocal next_report
            if target_size < 1024**3 or completed < next_report:
                return
            print(
                f"[文件校验] {index}/{len(entries)}，{relative_text}，"
                f"已读取 {completed / (1024**3):.1f}/{target_size / (1024**3):.1f} GiB",
                flush=True,
            )
            next_report += 1024**3

        actual = sha256_file(target, report_large_file)
        checked_bytes += target.stat().st_size
        if actual != expected:
            errors.append(f"checksum_mismatch:{relative_text}")
        if index == 1 or index % 1000 == 0 or index == len(entries):
            print(
                f"[文件校验] {index}/{len(entries)}，已读取 {checked_bytes / (1024**3):.2f} GiB",
                flush=True,
            )
    return {
        "status": "ready" if not errors else "blocked",
        "manifest": str(manifest),
        "checkedFileCount": len(entries),
        "checkedBytes": checked_bytes,
        "errors": errors[:200],
        "errorCount": len(errors),
    }


def _safe_manifest_target(root: Path, relative_text: object, key: str) -> Path:
    relative = normalize_relative_path(relative_text, key)
    target = root.joinpath(*relative.parts).resolve(strict=False)
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError(f"{key} escapes runtime root: {relative_text}")
    return target


def _recursive_file_count(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file())


NORMALIZED_REQUIRED_NONEMPTY_ENTITIES = frozenset(
    {
        "collection_summary",
        "strategy_master",
        "strategy_performance_daily",
    }
)


def _normalized_entity_name(relative_text: object) -> str:
    parts = str(relative_text or "").replace("\\", "/").split("/")
    return parts[1] if len(parts) > 1 else ""


def verify_declared_runtime_baselines(layout: WorkspaceLayout) -> dict[str, Any]:
    manifest_path = layout.baseline_root / "migration_manifest.json"
    if not manifest_path.is_file():
        return {
            "status": "not_applicable",
            "manifest": str(manifest_path),
            "errors": [],
            "errorCount": 0,
        }
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "blocked",
            "manifest": str(manifest_path),
            "errors": [f"migration_manifest_invalid:{type(exc).__name__}:{exc}"],
            "errorCount": 1,
        }

    errors: list[str] = []
    normalized_declared = 0
    normalized_present = 0
    normalized_optional_empty = 0
    for baseline_index, baseline in enumerate(payload.get("normalizedBaselines") or []):
        for file_index, relative_text in enumerate(baseline.get("files") or []):
            normalized_declared += 1
            try:
                target = _safe_manifest_target(
                    layout.normalized_root,
                    relative_text,
                    f"normalizedBaselines[{baseline_index}].files[{file_index}]",
                )
            except ValueError as exc:
                errors.append(f"normalized_baseline_path_invalid:{exc}")
                continue
            if not target.is_file():
                errors.append(f"normalized_baseline_missing:{relative_text}")
            elif target.stat().st_size <= 0:
                entity = _normalized_entity_name(relative_text)
                if entity in NORMALIZED_REQUIRED_NONEMPTY_ENTITIES:
                    errors.append(f"normalized_core_baseline_empty:{relative_text}")
                else:
                    normalized_present += 1
                    normalized_optional_empty += 1
            else:
                normalized_present += 1

    raw_selection = payload.get("rawSelection") or {}
    tree_results: list[dict[str, Any]] = []
    for index, item in enumerate(raw_selection.get("trees") or []):
        relative_text = item.get("path")
        declared = max(0, int(item.get("fileCount") or 0))
        try:
            target = _safe_manifest_target(layout.raw_root, relative_text, f"rawSelection.trees[{index}].path")
            actual = _recursive_file_count(target)
        except (OSError, ValueError) as exc:
            actual = 0
            errors.append(f"raw_tree_invalid:{relative_text}:{type(exc).__name__}:{exc}")
        if actual < declared:
            errors.append(f"raw_tree_incomplete:{relative_text}:declared={declared}:actual={actual}")
        tree_results.append({"path": relative_text, "declaredFileCount": declared, "actualFileCount": actual})

    latest_run_results: list[dict[str, Any]] = []
    for index, item in enumerate(raw_selection.get("latestRuns") or []):
        relative_text = item.get("path")
        run_name = str(item.get("run") or "").strip()
        declared = max(0, int(item.get("fileCount") or 0))
        target: Path | None = None
        try:
            if item.get("relativePath"):
                target = _safe_manifest_target(
                    layout.raw_root,
                    item["relativePath"],
                    f"rawSelection.latestRuns[{index}].relativePath",
                )
            else:
                parent = _safe_manifest_target(
                    layout.raw_root,
                    relative_text,
                    f"rawSelection.latestRuns[{index}].path",
                )
                if parent.is_dir() and run_name:
                    target = next((path for path in parent.rglob(run_name) if path.is_dir()), None)
            actual = _recursive_file_count(target) if target else 0
        except (OSError, ValueError) as exc:
            actual = 0
            errors.append(f"raw_latest_run_invalid:{relative_text}:{run_name}:{type(exc).__name__}:{exc}")
        if actual < declared:
            errors.append(
                f"raw_latest_run_incomplete:{relative_text}:{run_name}:declared={declared}:actual={actual}"
            )
        latest_run_results.append(
            {
                "path": relative_text,
                "run": run_name,
                "relativePath": item.get("relativePath"),
                "declaredFileCount": declared,
                "actualFileCount": actual,
            }
        )

    file_results: list[dict[str, Any]] = []
    for index, relative_text in enumerate(raw_selection.get("files") or []):
        try:
            target = _safe_manifest_target(layout.raw_root, relative_text, f"rawSelection.files[{index}]")
            present = target.is_file() and target.stat().st_size > 0
        except (OSError, ValueError) as exc:
            present = False
            errors.append(f"raw_file_invalid:{relative_text}:{type(exc).__name__}:{exc}")
        if not present:
            errors.append(f"raw_file_missing:{relative_text}")
        file_results.append({"path": relative_text, "present": present})

    return {
        "status": "ready" if not errors else "blocked",
        "manifest": str(manifest_path),
        "normalizedDeclaredFileCount": normalized_declared,
        "normalizedPresentFileCount": normalized_present,
        "normalizedOptionalEmptyFileCount": normalized_optional_empty,
        "rawTrees": tree_results,
        "rawLatestRuns": latest_run_results,
        "rawFiles": file_results,
        "errors": errors[:200],
        "errorCount": len(errors),
    }


def check_active_run(layout: WorkspaceLayout) -> None:
    candidates = (layout.daily_lock, layout.lock_root / "daily_update.lock")
    active = [str(path) for path in candidates if path.exists()]
    if active:
        raise RuntimeError(f"daily update lock exists; code/runtime changes are blocked: {active}")


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
def code_update_lock(layout: WorkspaceLayout):
    layout.lock_root.mkdir(parents=True, exist_ok=True)
    path = layout.lock_root / "code_update.lock"
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            current = {}
        pid = int(current.get("pid") or 0)
        if process_exists(pid):
            raise RuntimeError(f"another code update is active: pid={pid}")
        path.unlink(missing_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        payload = json.dumps({"pid": os.getpid(), "startedAt": now_text()}, ensure_ascii=False).encode("utf-8")
        os.write(descriptor, payload)
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


def git_output(layout: WorkspaceLayout, *args: str) -> str:
    completed = run_command(["git", *args], cwd=layout.code_root)
    if completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {(completed.stdout + completed.stderr).strip()}")
    return completed.stdout.strip()


def verify_code_compatibility(layout: WorkspaceLayout) -> dict[str, Any]:
    path = layout.code_root / "config" / "runtime" / "runtime_compatibility.json"
    if not path.is_file():
        raise RuntimeError(f"runtime compatibility manifest is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    required_python = tuple(int(value) for value in payload.get("requiredPythonMajorMinor") or (3, 12))
    if tuple(sys.version_info[:2]) != required_python:
        raise RuntimeError(
            f"code requires Python {required_python[0]}.{required_python[1]}, current runtime is "
            f"{sys.version_info.major}.{sys.version_info.minor}"
        )
    with contextlib.closing(
        sqlite3.connect(f"file:{layout.main_db.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    minimum = int(payload.get("minimumDatabaseUserVersion") or 0)
    maximum = int(payload.get("maximumDatabaseUserVersion") or 0)
    if current < minimum or (maximum and current > maximum):
        raise RuntimeError(
            f"database user_version {current} is outside supported range {minimum}..{maximum or 'open'}"
        )
    return {"databaseUserVersion": current, "minimum": minimum, "maximum": maximum}


def plan_database_migrations(payload: dict[str, Any], current_version: int) -> list[dict[str, Any]]:
    minimum = int(payload.get("minimumDatabaseUserVersion") or 0)
    maximum = int(payload.get("maximumDatabaseUserVersion") or 0)
    if current_version >= minimum and (not maximum or current_version <= maximum):
        return []
    migrations = payload.get("databaseMigrations") or []
    if not isinstance(migrations, list):
        raise RuntimeError("databaseMigrations must be a list")
    by_source: dict[int, dict[str, Any]] = {}
    for item in migrations:
        if not isinstance(item, dict):
            raise RuntimeError("database migration entries must be objects")
        source = int(item.get("fromVersion") or 0)
        target = int(item.get("toVersion") or 0)
        if source in by_source or target <= source:
            raise RuntimeError(f"invalid or duplicate database migration: {item}")
        if item.get("transactional") is not True or item.get("idempotent") is not True:
            raise RuntimeError(f"database migration must be transactional and idempotent: {item}")
        by_source[source] = item
    chain: list[dict[str, Any]] = []
    version = current_version
    seen: set[int] = set()
    while not (version >= minimum and (not maximum or version <= maximum)):
        if version in seen or version not in by_source:
            raise RuntimeError(
                f"database user_version {current_version} is incompatible and no complete migration chain reaches "
                f"{minimum}..{maximum or 'open'}"
            )
        seen.add(version)
        item = by_source[version]
        chain.append(item)
        version = int(item["toVersion"])
    return chain


def latest_successful_backup(layout: WorkspaceLayout) -> Path | None:
    candidates: list[tuple[Path, Path]] = []
    for metadata_path in layout.backup_root.glob("analysis_zh_current_*.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") != "success":
            continue
        filename = str(payload.get("backup_file") or "").strip()
        backup = layout.backup_root / filename
        if backup.is_file():
            candidates.append((metadata_path, backup))
    return max(candidates, key=lambda item: item[0].stat().st_mtime_ns)[1] if candidates else None


def _sql_statements(text: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in text.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise RuntimeError("database migration SQL ends with an incomplete statement")
    return statements


def apply_database_migrations(layout: WorkspaceLayout) -> dict[str, Any]:
    compatibility_path = layout.code_root / "config" / "runtime" / "runtime_compatibility.json"
    payload = json.loads(compatibility_path.read_text(encoding="utf-8-sig"))
    with contextlib.closing(
        sqlite3.connect(f"file:{layout.main_db.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    chain = plan_database_migrations(payload, current)
    if not chain:
        return {"applied": False, "fromVersion": current, "toVersion": current, "steps": []}
    if payload.get("migrationPolicy") != "transactional-idempotent-only":
        raise RuntimeError("database migration policy must be transactional-idempotent-only")
    if latest_successful_backup(layout) is None:
        raise RuntimeError("database migration requires an existing successful rolling backup")
    snapshot = layout.temp_root / "code_update" / f"pre_migration_{current}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    sqlite_backup(layout.main_db, snapshot)
    applied: list[dict[str, Any]] = []
    try:
        connection = sqlite3.connect(layout.main_db, timeout=120, isolation_level=None)
        try:
            for item in chain:
                relative = normalize_relative_path(item.get("script"), "databaseMigration.script")
                script = layout.code_root.joinpath(*relative.parts).resolve(strict=True)
                if layout.code_root not in script.parents or script.suffix.lower() != ".sql":
                    raise RuntimeError(f"unsafe database migration script: {script}")
                sql = script.read_text(encoding="utf-8-sig")
                statements = _sql_statements(sql)
                if any(re.search(r"\b(BEGIN|COMMIT|ROLLBACK|VACUUM)\b", statement, re.IGNORECASE) for statement in statements):
                    raise RuntimeError(f"migration script contains forbidden transaction control: {script}")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in statements:
                        connection.execute(statement)
                    target = int(item["toVersion"])
                    connection.execute(f"PRAGMA user_version = {target}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                applied.append(
                    {
                        "fromVersion": int(item["fromVersion"]),
                        "toVersion": target,
                        "script": str(relative),
                    }
                )
        finally:
            connection.close()
        return {
            "applied": True,
            "fromVersion": current,
            "toVersion": int(chain[-1]["toVersion"]),
            "steps": applied,
            "rollbackSnapshot": str(snapshot),
        }
    except Exception:
        sqlite_backup(snapshot, layout.main_db)
        snapshot.unlink(missing_ok=True)
        raise


def validate_code(layout: WorkspaceLayout, *, run_tests: bool) -> dict[str, Any]:
    compile_targets = [
        layout.code_root / "节点脚本" / "_共享组件" / "生产程序" / "runtime_workspace.py",
        layout.code_root / "节点脚本" / "_共享组件" / "生产程序" / "runtime_workspace_cli.py",
        layout.code_root / "节点脚本" / "_共享组件" / "生产程序" / "build_runtime_migration_package.py",
        layout.code_root / "节点脚本" / "00_调度框架" / "orchestrator.py",
        layout.code_root / "节点脚本" / "_共享组件" / "生产程序" / "build_minimal_publish_set.py",
    ]
    for path in compile_targets:
        if not path.is_file():
            raise FileNotFoundError(path)
        py_compile.compile(str(path), doraise=True)
    portability = scan_portability(layout.code_root)
    if portability["status"] != "ready":
        raise RuntimeError(f"runtime path portability validation failed: {portability['issues']}")
    compatibility = verify_code_compatibility(layout)
    tests: dict[str, Any] = {"skipped": not run_tests}
    if run_tests:
        completed = run_command(
            [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "unittest",
                "discover",
                "-s",
                str(layout.code_root / "节点脚本" / "00_调度框架" / "tests"),
                "-p",
                "test_*.py",
            ],
            cwd=layout.code_root,
            timeout=180,
        )
        tests = {
            "returncode": completed.returncode,
            "output": (completed.stdout + completed.stderr).strip(),
        }
        if completed.returncode:
            raise RuntimeError(f"runtime workspace tests failed:\n{tests['output']}")
    return {"compatibility": compatibility, "portability": portability, "tests": tests}


def adb_device_health(adb: Path, serial: str, *, launch: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"deviceId": serial, "adbPath": str(adb)}

    def adb_run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return run_command([str(adb), "-s", serial, *args], timeout=timeout)

    state = adb_run("get-state")
    qemu = adb_run("shell", "getprop ro.kernel.qemu")
    package = adb_run("shell", f"pm path {TTFUND_PACKAGE}")
    if launch and package.returncode == 0:
        adb_run("shell", "monkey", "-p", TTFUND_PACKAGE, "1", timeout=45)
    cache = adb_run("shell", f"ls -d {TTFUND_CACHE_DIR}")
    cache_listing = adb_run("shell", f"ls -1 {TTFUND_CACHE_DIR}", timeout=45)
    cache_names = [line.strip() for line in cache_listing.stdout.splitlines() if line.strip()]
    login_markers = (
        "saveAllAdvisersInfokey",
        "layout_tougu-scroll-view",
        "strategyDetailPageData",
        "adjuseHouseList",
    )
    login_evidence = [name for name in cache_names if any(marker in name for marker in login_markers)]
    serial_lower = serial.strip().lower()
    is_emulator = (
        qemu.stdout.strip() == "1"
        or serial_lower.startswith("emulator-")
        or serial_lower.startswith("127.0.0.1:")
        or serial_lower.startswith("localhost:")
    )
    blocked_reasons: list[str] = []
    if is_emulator:
        blocked_reasons.append("emulator_device_not_allowed")
    result.update(
        {
            "stateReady": state.returncode == 0 and state.stdout.strip() == "device",
            "appInstalled": package.returncode == 0 and bool(package.stdout.strip()),
            "cacheAccessible": cache.returncode == 0,
            "cacheFileCount": len(cache_names),
            "loginCacheEvidenceReady": bool(login_evidence),
            "loginCacheEvidenceSample": login_evidence[:10],
            "isEmulator": is_emulator,
            "blockedReasons": blocked_reasons,
            "stateOutput": (state.stdout + state.stderr).strip(),
            "qemuOutput": (qemu.stdout + qemu.stderr).strip(),
            "packageOutput": (package.stdout + package.stderr).strip(),
            "cacheOutput": (cache.stdout + cache.stderr).strip(),
        }
    )
    result["ready"] = (
        result["stateReady"]
        and not result["isEmulator"]
        and result["appInstalled"]
        and result["cacheAccessible"]
        and result["loginCacheEvidenceReady"]
    )
    return result


def resolve_physical(layout: WorkspaceLayout) -> dict[str, Any]:
    serial = str(layout.config.get("physicalDeviceId") or "").strip()
    if not serial:
        return {"deviceType": "physical", "ready": False, "error": "physicalDeviceId is empty"}
    result = adb_device_health(layout.adb_path, serial, launch=True)
    result["deviceType"] = "physical"
    return result


def select_device(
    layout: WorkspaceLayout,
    *,
    explicit_device_id: str = "",
    requested_device_type: str = "auto",
) -> dict[str, Any]:
    output_dir = layout.output_root / "device_preflight" / datetime.now().astimezone().strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    attempts: list[dict[str, Any]] = []
    if explicit_device_id:
        selected = adb_device_health(layout.adb_path, explicit_device_id, launch=True)
        selected["deviceType"] = "explicit"
        attempts.append(selected)
        selected["deviceType"] = "physical"
    else:
        if requested_device_type not in {"auto", "physical"}:
            raise ValueError("only a physical device is supported")
        selected = resolve_physical(layout)
        attempts.append(selected)

    summary = {
        "generatedAt": now_text(),
        "status": "ready" if selected.get("ready") else "blocked",
        "deviceMode": "physical_only",
        "selected": selected,
        "fallback": None,
        "attempts": attempts,
    }
    summary_path = output_dir / f"{run_id}_selection.json"
    atomic_write_json(summary_path, summary)
    summary["summaryPath"] = str(summary_path)
    if not selected.get("ready"):
        raise RuntimeError(f"no configured device passed preflight; see {summary_path}")
    return summary


def inspect_all_devices(layout: WorkspaceLayout) -> dict[str, Any]:
    output_dir = layout.output_root / "device_preflight" / datetime.now().astimezone().strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    attempts = [resolve_physical(layout)]
    ready = [attempt for attempt in attempts if attempt.get("ready")]
    result = {
        "generatedAt": now_text(),
        "status": "ready" if ready else "blocked",
        "readyDeviceCount": len(ready),
        "deviceMode": "physical_only",
        "fallbackReady": False,
        "attempts": attempts,
        "warnings": [] if ready else ["physical_collection_device_is_not_ready"],
    }
    output = output_dir / f"{run_id}_all_devices.json"
    atomic_write_json(output, result)
    result["summaryPath"] = str(output)
    return result


def inspect_git_and_network(layout: WorkspaceLayout) -> dict[str, Any]:
    result: dict[str, Any] = {"code": {}, "publish": {}, "pages": {}}
    code_update_mode = str(layout.config.get("codeUpdateMode") or "git")
    checks = (("publish", layout.publish_root, str(layout.config.get("publishBranch") or "main"), True),)
    if code_update_mode == "git":
        checks = (("code", layout.code_root, str(layout.config.get("codeBranch") or "code"), False), *checks)
    else:
        result["code"] = {
            "path": str(layout.code_root),
            "updateMode": code_update_mode,
            "skipped": True,
            "reason": "program code is updated through the controlled Syncthing snapshot",
        }
    for name, root, branch, push_check in checks:
        item: dict[str, Any] = {"path": str(root), "branch": branch, "exists": (root / ".git").exists()}
        if item["exists"]:
            remote = run_command(["git", "remote", "get-url", "origin"], cwd=root, timeout=30)
            item["remote"] = remote.stdout.strip() if remote.returncode == 0 else ""
            ls_remote = run_command(["git", "ls-remote", "origin", f"refs/heads/{branch}"], cwd=root, timeout=90)
            item["remoteReachable"] = ls_remote.returncode == 0 and bool(ls_remote.stdout.strip())
            item["remoteOutput"] = (ls_remote.stdout + ls_remote.stderr).strip()[-2000:]
            if push_check:
                identity_name = run_command(["git", "config", "user.name"], cwd=root, timeout=30)
                identity_email = run_command(["git", "config", "user.email"], cwd=root, timeout=30)
                item["userName"] = identity_name.stdout.strip() if identity_name.returncode == 0 else ""
                item["userEmail"] = identity_email.stdout.strip() if identity_email.returncode == 0 else ""
                item["identityReady"] = bool(item["userName"] and item["userEmail"])
                push = run_command(["git", "push", "--dry-run", "origin", branch], cwd=root, timeout=120)
                item["pushDryRunReady"] = push.returncode == 0
                item["pushDryRunOutput"] = (push.stdout + push.stderr).strip()[-2000:]
        result[name] = item
    pages_url = str(layout.config.get("pagesBaseUrl") or "").rstrip("/") + "/version.json"
    try:
        import requests

        response = requests.get(pages_url, timeout=30, headers={"Cache-Control": "no-cache"})
        result["pages"] = {
            "url": pages_url,
            "statusCode": response.status_code,
            "reachable": response.status_code == 200,
        }
    except Exception as exc:
        result["pages"] = {"url": pages_url, "reachable": False, "error": str(exc)}
    errors: list[str] = []
    if code_update_mode == "git" and not result["code"].get("remoteReachable"):
        errors.append("code_remote_unreachable")
    if not result["publish"].get("remoteReachable"):
        errors.append("publish_remote_unreachable")
    if not result["publish"].get("pushDryRunReady"):
        errors.append("publish_git_credentials_not_ready")
    if not result["publish"].get("identityReady"):
        errors.append("publish_git_identity_missing")
    if not result["pages"].get("reachable"):
        errors.append("github_pages_unreachable")
    result["errors"] = errors
    result["status"] = "ready" if not errors else "blocked"
    return result


def ensure_publish_git_identity(layout: WorkspaceLayout) -> None:
    if not (layout.publish_root / ".git").is_dir():
        return
    defaults = {
        "user.name": str(layout.config.get("publishGitUserName") or "天眼系统自动更新").strip(),
        "user.email": str(layout.config.get("publishGitUserEmail") or "tianyan-system@local.invalid").strip(),
    }
    for key, value in defaults.items():
        current = run_command(["git", "config", "--local", "--get", key], cwd=layout.publish_root, timeout=30)
        if current.returncode == 0 and current.stdout.strip():
            continue
        if not value:
            raise RuntimeError(f"publish Git identity is missing: {key}")
        configured = run_command(["git", "config", "--local", key, value], cwd=layout.publish_root, timeout=30)
        if configured.returncode != 0:
            raise RuntimeError(f"failed to configure publish Git identity {key}: {configured.stderr.strip()}")


def run_daily(layout: WorkspaceLayout, args: argparse.Namespace) -> int:
    if (layout.lock_root / "code_update.lock").exists():
        raise RuntimeError("code update lock exists; daily workflow will not start")
    ensure_runtime_bridges(layout)
    ensure_publish_git_identity(layout)
    checks = inspect_workspace(layout)
    if checks["status"] != "ready":
        print_json(checks)
        return 2
    device = select_device(
        layout,
        explicit_device_id=args.device_id,
        requested_device_type=args.device_type,
    )
    selected = device["selected"]
    device_id = str(selected.get("device_id") or selected.get("deviceId") or "")
    adb_path = str(selected.get("adb_path") or selected.get("adbPath") or layout.adb_path)
    device_type = str(selected.get("deviceType") or "")
    env = dict(os.environ)
    env.update(
        {
            "ADVISOR_WORKSPACE_ROOT": str(layout.workspace_root),
            "ADVISOR_ADB_EXE": adb_path,
            "ADVISOR_DEVICE_ID": device_id,
            "TTFUND_DEVICE_ID": device_id,
            "ADVISOR_UNATTENDED": "1",
            "ADVISOR_PYTHON_EXE": sys.executable,
        }
    )
    mode = "resume" if args.resume_run_id else "daily"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(layout.code_root / "节点脚本" / "00_调度框架" / "启动.ps1"),
        "-WorkspaceRoot",
        str(layout.workspace_root),
        "-Mode",
        mode,
    ]
    if args.resume_run_id:
        command.extend(["-ModeArguments", args.resume_run_id])
    if args.dry_run:
        command.append("-DryRun")
    ignored = [
        name
        for enabled, name in (
            (args.skip_readiness, "--skip-readiness"),
            (args.skip_backup, "--skip-backup"),
            (args.skip_publish, "--skip-publish"),
            (args.skip_wait, "--skip-wait"),
        )
        if enabled
    ]
    if ignored:
        print(f"[兼容提示] 节点化调度不再接受跳过关键阶段参数，已忽略: {', '.join(ignored)}", flush=True)
    print(f"[设备] 本次固定使用 {device_type}: {device_id}", flush=True)
    print(f"[设备] 预检摘要: {device['summaryPath']}", flush=True)
    completed = subprocess.run(command, cwd=layout.code_root, env=env, check=False)
    return int(completed.returncode)


def ensure_initial_backup(layout: WorkspaceLayout) -> Path | None:
    if not layout.main_db.is_file():
        return None
    for metadata_path in layout.backup_root.glob("analysis_zh_current_*.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("status") == "success" and metadata.get("run_id") == "migration-baseline":
            existing = layout.backup_root / str(metadata.get("backup_file") or "")
            if existing.is_file():
                return existing
    command = [
        sys.executable,
        "-X",
        "utf8",
        str(layout.code_root / "节点脚本" / "_共享组件" / "生产程序" / "backup_successful_analysis_db.py"),
        "--db-path",
        str(layout.main_db),
        "--backup-dir",
        str(layout.backup_root),
        "--retain",
        str(int(layout.config.get("backupRetain") or 1)),
        "--run-id",
        "migration-baseline",
    ]
    print(f"[初始备份] 创建可轮换的成功基线备份: {layout.backup_root}", flush=True)
    completed = subprocess.run(command, cwd=layout.code_root, timeout=4 * 3600, check=False)
    if completed.returncode:
        raise RuntimeError(f"initial backup failed with exit code {completed.returncode}")
    matches: list[tuple[Path, dict[str, Any]]] = []
    for metadata_path in layout.backup_root.glob("analysis_zh_current_*.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("status") == "success" and metadata.get("run_id") == "migration-baseline":
            matches.append((metadata_path, metadata))
    if not matches:
        raise RuntimeError("initial backup completed without success metadata")
    _, payload = max(matches, key=lambda item: item[0].stat().st_mtime_ns)
    target = layout.backup_root / str(payload.get("backup_file") or "")
    if not target.is_file():
        raise RuntimeError("initial backup metadata does not reference a valid database file")
    return target


def initialize(layout: WorkspaceLayout, args: argparse.Namespace) -> int:
    ensure_runtime_bridges(layout)
    ensure_publish_git_identity(layout)
    report = inspect_workspace(layout, quick_check=args.quick_check)
    if report["status"] == "ready" and not args.skip_checksums:
        report["baselineIntegrity"] = verify_workspace_checksums(layout)
        if report["baselineIntegrity"]["status"] != "ready":
            report["errors"].extend(report["baselineIntegrity"]["errors"])
            report["status"] = "blocked"
    report["runtimeBaselineIntegrity"] = verify_declared_runtime_baselines(layout)
    if report["runtimeBaselineIntegrity"]["status"] == "blocked":
        report["errors"].extend(report["runtimeBaselineIntegrity"]["errors"])
        report["status"] = "blocked"
    if report["status"] == "ready" and not args.skip_network_check:
        report["gitAndNetwork"] = inspect_git_and_network(layout)
        if report["gitAndNetwork"]["status"] != "ready":
            report["errors"].extend(report["gitAndNetwork"]["errors"])
            report["status"] = "blocked"
    if report["status"] == "ready" and not args.skip_device_check:
        report["devices"] = inspect_all_devices(layout)
        if report["devices"]["status"] != "ready":
            report["errors"].append("no_collection_device_ready")
            report["status"] = "blocked"
    backup = None
    if report["status"] == "ready" and not args.skip_baseline_backup:
        backup = ensure_initial_backup(layout)
    report["baselineBackup"] = str(backup) if backup else None
    output = layout.output_root / "workspace_check" / "initialization.json"
    atomic_write_json(output, report)
    print_json(report)
    return 0 if report["status"] == "ready" else 2


def check(layout: WorkspaceLayout, args: argparse.Namespace) -> int:
    ensure_runtime_bridges(layout)
    ensure_publish_git_identity(layout)
    report = inspect_workspace(layout, quick_check=args.quick_check)
    report["runtimeBaselineIntegrity"] = verify_declared_runtime_baselines(layout)
    if report["runtimeBaselineIntegrity"]["status"] == "blocked":
        report["errors"].extend(report["runtimeBaselineIntegrity"]["errors"])
        report["status"] = "blocked"
    if args.check_devices and report["status"] == "ready":
        report["devices"] = inspect_all_devices(layout)
        if report["devices"]["status"] != "ready":
            report["errors"].append("device_preflight_failed")
            report["status"] = "blocked"
    if args.check_network and report["status"] == "ready":
        report["gitAndNetwork"] = inspect_git_and_network(layout)
        if report["gitAndNetwork"]["status"] != "ready":
            report["errors"].extend(report["gitAndNetwork"]["errors"])
            report["status"] = "blocked"
    output_dir = layout.output_root / "workspace_check"
    output = output_dir / f"check_{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')}.json"
    atomic_write_json(output, report)
    print_json(report)
    print(f"checkReport={output}")
    return 0 if report["status"] == "ready" else 2


def update_code(layout: WorkspaceLayout, *, run_tests: bool) -> int:
    with code_update_lock(layout):
        check_active_run(layout)
        if str(layout.config.get("codeUpdateMode") or "git") != "git":
            raise RuntimeError("当前迁移包使用 Syncthing 程序快照；请在每日任务停止时同步“程序代码”目录，再运行检查入口。")
        if not (layout.code_root / ".git").exists():
            raise RuntimeError(f"code root is not a Git checkout: {layout.code_root}")
        dirty = git_output(layout, "status", "--porcelain", "--untracked-files=all")
        if dirty:
            raise RuntimeError(f"code checkout contains local changes; update refused:\n{dirty}")
        branch = str(layout.config.get("codeBranch") or "code")
        old_commit = git_output(layout, "rev-parse", "HEAD")
        fetch = run_command(["git", "fetch", "--prune", "origin", branch], cwd=layout.code_root, timeout=300)
        if fetch.returncode:
            raise RuntimeError(f"git fetch failed: {(fetch.stdout + fetch.stderr).strip()}")
        new_commit = git_output(layout, "rev-parse", f"origin/{branch}")
        if old_commit == new_commit:
            print(f"[程序更新] 已是最新版本 {old_commit[:12]}")
            return 0
        ancestor = run_command(["git", "merge-base", "--is-ancestor", old_commit, new_commit], cwd=layout.code_root)
        if ancestor.returncode:
            raise RuntimeError("remote code is not a fast-forward of the current commit")
        state_dir = layout.code_update_state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        pending = {
            "startedAt": now_text(),
            "status": "updating",
            "previousCommit": old_commit,
            "targetCommit": new_commit,
        }
        atomic_write_json(state_dir / "latest.json", pending)
        try:
            merge = run_command(["git", "merge", "--ff-only", f"origin/{branch}"], cwd=layout.code_root, timeout=300)
            if merge.returncode:
                raise RuntimeError(f"git fast-forward failed: {(merge.stdout + merge.stderr).strip()}")
            migration = apply_database_migrations(layout)
            validation = validate_code(layout, run_tests=run_tests)
        except Exception as exc:
            recovery_errors: list[str] = []
            if "migration" in locals() and migration.get("rollbackSnapshot"):
                snapshot = Path(str(migration["rollbackSnapshot"]))
                if snapshot.is_file():
                    try:
                        sqlite_backup(snapshot, layout.main_db)
                        snapshot.unlink(missing_ok=True)
                    except Exception as recovery_exc:
                        recovery_errors.append(f"database restore failed: {recovery_exc}")
            rollback = run_command(["git", "reset", "--hard", old_commit], cwd=layout.code_root, timeout=300)
            if rollback.returncode:
                recovery_errors.append(f"code rollback failed: {(rollback.stdout + rollback.stderr).strip()}")
            pending.update(
                {
                    "finishedAt": now_text(),
                    "status": "rolled_back",
                    "error": str(exc),
                    "rollbackReturncode": rollback.returncode,
                    "rollbackOutput": (rollback.stdout + rollback.stderr).strip(),
                    "recoveryErrors": recovery_errors,
                }
            )
            atomic_write_json(state_dir / "latest.json", pending)
            if recovery_errors:
                raise RuntimeError(f"code update failed: {exc}; recovery also failed: {'; '.join(recovery_errors)}") from exc
            raise
        if migration.get("rollbackSnapshot"):
            Path(str(migration["rollbackSnapshot"])).unlink(missing_ok=True)
            migration.pop("rollbackSnapshot", None)
        pending.update({"finishedAt": now_text(), "status": "success", "validation": validation, "migration": migration})
        atomic_write_json(state_dir / "latest.json", pending)
        atomic_write_json(state_dir / "previous.json", {"commit": old_commit, "replacedBy": new_commit, "updatedAt": now_text()})
        print(f"[程序更新] {old_commit[:12]} -> {new_commit[:12]} 验证成功")
        return 0


def rollback_code(layout: WorkspaceLayout) -> int:
    if str(layout.config.get("codeUpdateMode") or "git") != "git":
        raise RuntimeError("当前迁移包使用 Syncthing 程序快照，不支持 Git 程序回退。")
    with code_update_lock(layout):
        check_active_run(layout)
        dirty = git_output(layout, "status", "--porcelain", "--untracked-files=all")
        if dirty:
            raise RuntimeError(f"code checkout contains local changes; rollback refused:\n{dirty}")
        previous_path = layout.code_update_state_dir / "previous.json"
        if not previous_path.is_file():
            raise RuntimeError("no previous validated code commit is recorded")
        previous = json.loads(previous_path.read_text(encoding="utf-8-sig"))
        target = str(previous.get("commit") or "")
        if not target:
            raise RuntimeError("previous code commit record is invalid")
        current = git_output(layout, "rev-parse", "HEAD")
        target_compatibility = json.loads(
            git_output(layout, "show", f"{target}:config/runtime/runtime_compatibility.json")
        )
        with contextlib.closing(
            sqlite3.connect(f"file:{layout.main_db.as_posix()}?mode=ro", uri=True, timeout=30)
        ) as connection:
            database_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        minimum = int(target_compatibility.get("minimumDatabaseUserVersion") or 0)
        maximum = int(target_compatibility.get("maximumDatabaseUserVersion") or 0)
        if database_version < minimum or (maximum and database_version > maximum):
            raise RuntimeError(
                f"previous code supports database user_version {minimum}..{maximum or 'open'}, "
                f"but the current database is {database_version}; code-only rollback is unsafe"
            )
        completed = run_command(["git", "reset", "--hard", target], cwd=layout.code_root, timeout=300)
        if completed.returncode:
            raise RuntimeError(f"code rollback failed: {(completed.stdout + completed.stderr).strip()}")
        try:
            validation = validate_code(layout, run_tests=False)
        except Exception:
            run_command(["git", "reset", "--hard", current], cwd=layout.code_root, timeout=300)
            raise
        atomic_write_json(
            layout.code_update_state_dir / "rollback_latest.json",
            {"rolledBackAt": now_text(), "fromCommit": current, "toCommit": target, "validation": validation},
        )
        atomic_write_json(
            previous_path,
            {"commit": current, "replacedBy": target, "updatedAt": now_text()},
        )
        print(f"[程序回退] {current[:12]} -> {target[:12]}")
        return 0


def main() -> None:
    args = parse_args()
    workspace_root = args.workspace_root.resolve()
    config_path = args.config
    if args.command == "initialize" and not (config_path or (workspace_root / "本机配置" / "runtime.local.json")).exists():
        create_default_config(workspace_root)
    layout = load_workspace(workspace_root, config_path)
    try:
        if args.command == "initialize":
            code = initialize(layout, args)
        elif args.command == "check":
            code = check(layout, args)
        elif args.command == "daily":
            code = run_daily(layout, args)
        elif args.command == "update-code":
            code = update_code(layout, run_tests=not args.skip_tests)
        elif args.command == "rollback-code":
            code = rollback_code(layout)
        else:
            raise RuntimeError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(f"[失败] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
