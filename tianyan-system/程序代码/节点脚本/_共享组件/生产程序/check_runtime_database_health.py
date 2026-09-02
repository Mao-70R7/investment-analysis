from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"

REQUIRED_TABLES = [
    "\u7b56\u7565\u4fe1\u606f",
    "\u7b56\u7565\u65e5\u5ea6\u4e1a\u7ee9",
    "\u7b56\u7565\u8c03\u4ed3\u4e8b\u4ef6",
    "\u7b56\u7565\u8c03\u4ed3\u660e\u7ec6",
    "\u57fa\u91d1\u65e5\u5ea6\u51c0\u503c",
    "\u57fa\u91d1\u7ecf\u6d4e\u66b4\u9732\u5feb\u7167",
    "\u6307\u6570\u65e5\u5ea6\u884c\u60c5",
]

DATE_QUERIES = {
    "strategy_performance_max_trade_date": 'SELECT MAX("\u4ea4\u6613\u65e5\u671f") FROM "\u7b56\u7565\u65e5\u5ea6\u4e1a\u7ee9"',
    "rebalance_event_max_date": 'SELECT MAX("\u8c03\u4ed3\u65e5\u671f") FROM "\u7b56\u7565\u8c03\u4ed3\u4e8b\u4ef6"',
    "rebalance_detail_max_date": 'SELECT MAX("\u8c03\u4ed3\u65e5\u671f") FROM "\u7b56\u7565\u8c03\u4ed3\u660e\u7ec6"',
    "fund_nav_max_trade_date": 'SELECT MAX("\u4ea4\u6613\u65e5\u671f") FROM "\u57fa\u91d1\u65e5\u5ea6\u51c0\u503c"',
    "fund_exposure_max_report_date": 'SELECT MAX("\u62a5\u544a\u671f") FROM "\u57fa\u91d1\u7ecf\u6d4e\u66b4\u9732\u5feb\u7167"',
    "index_quote_max_trade_date": 'SELECT MAX("\u4ea4\u6613\u65e5\u671f") FROM "\u6307\u6570\u65e5\u5ea6\u884c\u60c5"',
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check runtime SQLite health before unattended advisor updates.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--repair-empty",
        action="store_true",
        help="Restore a missing or zero-byte database from the newest valid data/backups analysis_zh_current*.sqlite file.",
    )
    parser.add_argument("--backup-dir", type=Path, default=PROJECT_ROOT / "data" / "backups")
    parser.add_argument(
        "--integrity-mode",
        choices=("auto", "light", "full"),
        default="auto",
        help="Daily runs use auto: full quick_check every N days or after an abnormal-exit flag, light checks otherwise.",
    )
    parser.add_argument("--full-check-interval-days", type=int, default=7)
    parser.add_argument("--full-check-marker", type=Path)
    parser.add_argument("--abnormal-exit-flag", type=Path)
    parser.add_argument("--heartbeat-seconds", type=int, default=30)
    parser.add_argument("--json", action="store_true", help="Print compact JSON only.")
    return parser.parse_args()


def file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def sibling_runtime_files(db_path: Path) -> list[dict[str, Any]]:
    suffixes = [
        f"{db_path.name}-wal",
        f"{db_path.name}-shm",
        f"{db_path.name}-journal",
    ]
    found: list[dict[str, Any]] = []
    parent = db_path.parent
    for name in suffixes:
        path = parent / name
        if path.exists():
            found.append(file_info(path))
    for path in sorted(parent.glob("*sync-conflict*")):
        if path.is_file():
            found.append(file_info(path))
    return found


def cleanup_stale_runtime_files(db_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"deleted": [], "warnings": []}
    wal_path = db_path.parent / f"{db_path.name}-wal"
    shm_path = db_path.parent / f"{db_path.name}-shm"
    journal_path = db_path.parent / f"{db_path.name}-journal"
    should_cleanup_pair = False
    try:
        if wal_path.exists():
            should_cleanup_pair = wal_path.stat().st_size == 0
        elif shm_path.exists():
            should_cleanup_pair = True
    except OSError as exc:
        result["warnings"].append(f"runtime_file_stat_failed: {exc}")
    if should_cleanup_pair:
        for path in (wal_path, shm_path):
            if not path.exists():
                continue
            try:
                path.unlink()
                result["deleted"].append(str(path))
            except OSError as exc:
                result["warnings"].append(f"runtime_file_delete_failed: {path}: {exc}")
    if journal_path.exists():
        try:
            if journal_path.stat().st_size == 0:
                journal_path.unlink()
                result["deleted"].append(str(journal_path))
        except OSError as exc:
            result["warnings"].append(f"runtime_file_delete_failed: {journal_path}: {exc}")
    return result


def connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def read_marker_time(path: Path) -> datetime | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        value = str(payload.get("finished_at") or "").strip()
        return datetime.fromisoformat(value) if value else None
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        return None


def resolve_integrity_mode(
    requested: str,
    *,
    marker_path: Path,
    abnormal_exit_flag: Path,
    interval_days: int,
) -> tuple[str, str]:
    if requested in {"light", "full"}:
        return requested, "explicit"
    if abnormal_exit_flag.exists():
        return "full", "abnormal_exit_flag"
    last_full = read_marker_time(marker_path)
    if last_full is None:
        return "full", "no_previous_full_check"
    if datetime.now() - last_full >= timedelta(days=max(1, interval_days)):
        return "full", "full_check_interval_elapsed"
    return "light", "recent_full_check_available"


def run_quick_check_with_heartbeat(
    db_path: Path,
    *,
    heartbeat_seconds: int,
    quiet: bool,
) -> tuple[str, int]:
    started = time.monotonic()

    def worker() -> str:
        conn = connect_readonly(db_path)
        try:
            return str(conn.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            conn.close()

    if not quiet:
        print(f"[DB-CHECK] full quick_check started: {db_path}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker)
        while True:
            try:
                result = future.result(timeout=max(1, heartbeat_seconds))
                break
            except concurrent.futures.TimeoutError:
                if not quiet:
                    elapsed = int(time.monotonic() - started)
                    print(f"[DB-CHECK] full quick_check running, elapsed={elapsed}s", flush=True)
    elapsed = int(time.monotonic() - started)
    if not quiet:
        print(f"[DB-CHECK] full quick_check finished, elapsed={elapsed}s result={result}", flush=True)
    return result, elapsed


def check_database(
    db_path: Path,
    *,
    integrity_mode: str = "full",
    heartbeat_seconds: int = 30,
    quiet: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    runtime_cleanup = cleanup_stale_runtime_files(db_path)
    warnings.extend(runtime_cleanup.get("warnings") or [])
    info: dict[str, Any] = {
        "database": file_info(db_path),
        "integrity_mode": integrity_mode,
        "required_tables": {},
        "dates": {},
        "runtime_cleanup": runtime_cleanup,
        "runtime_files": sibling_runtime_files(db_path),
    }

    if not db_path.exists():
        errors.append(f"database_missing: {db_path}")
        info["errors"] = errors
        info["warnings"] = warnings
        return info
    if db_path.stat().st_size <= 0:
        errors.append(f"database_empty: {db_path}")
        info["errors"] = errors
        info["warnings"] = warnings
        return info

    runtime_names = {Path(item["path"]).name for item in info["runtime_files"]}
    if any("sync-conflict" in name for name in runtime_names):
        errors.append("syncthing_conflict_file_found_next_to_database")
    if any(name.endswith("-wal") or name.endswith("-shm") for name in runtime_names):
        warnings.append("sqlite_wal_or_shm_file_present; do not sync while the database is being written")

    try:
        conn = connect_readonly(db_path)
    except sqlite3.Error as exc:
        errors.append(f"sqlite_open_failed: {type(exc).__name__}: {exc}")
        info["errors"] = errors
        info["warnings"] = warnings
        return info

    try:
        if integrity_mode == "full":
            conn.close()
            conn = None
            quick_check, quick_check_seconds = run_quick_check_with_heartbeat(
                db_path,
                heartbeat_seconds=heartbeat_seconds,
                quiet=quiet,
            )
            info["quick_check"] = quick_check
            info["quick_check_seconds"] = quick_check_seconds
            if quick_check != "ok":
                errors.append(f"sqlite_quick_check_failed: {quick_check}")
            conn = connect_readonly(db_path)
        else:
            info["quick_check"] = "skipped_recent_full_check"
            info["schema_version"] = conn.execute("PRAGMA schema_version").fetchone()[0]
        info["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        info["page_count"] = conn.execute("PRAGMA page_count").fetchone()[0]
        info["freelist_count"] = conn.execute("PRAGMA freelist_count").fetchone()[0]

        existing_tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        }
        for table in REQUIRED_TABLES:
            item: dict[str, Any] = {"exists": table in existing_tables}
            if not item["exists"]:
                errors.append(f"required_table_missing: {table}")
            else:
                item["has_rows"] = conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None
            info["required_tables"][table] = item

        for name, query in DATE_QUERIES.items():
            try:
                info["dates"][name] = conn.execute(query).fetchone()[0]
            except sqlite3.Error as exc:
                info["dates"][name] = {"error": str(exc)}
                errors.append(f"date_query_failed: {name}: {exc}")
    except Exception as exc:  # noqa: BLE001 - preflight must always return a structured failure.
        errors.append(f"sqlite_health_check_failed: {type(exc).__name__}: {exc}")
    finally:
        if conn is not None:
            conn.close()

    info["errors"] = errors
    info["warnings"] = warnings
    return info


def database_needs_empty_repair(result: dict[str, Any]) -> bool:
    errors = [str(item) for item in result.get("errors") or []]
    return any(item.startswith("database_missing:") or item.startswith("database_empty:") for item in errors)


def validate_backup(path: Path) -> tuple[bool, str, dict[str, Any]]:
    details: dict[str, Any] = {"path": str(path), "size": path.stat().st_size if path.exists() else None}
    if not path.exists() or not path.is_file():
        return False, "not_a_file", details
    if path.stat().st_size <= 1024 * 1024:
        return False, "too_small", details
    try:
        conn = connect_readonly(path)
    except sqlite3.Error as exc:
        return False, f"open_failed: {type(exc).__name__}: {exc}", details
    try:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        details["quick_check"] = quick_check
        if quick_check != "ok":
            return False, f"quick_check_failed: {quick_check}", details
        existing_tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        }
        missing = [table for table in REQUIRED_TABLES if table not in existing_tables]
        details["missing_required_tables"] = missing
        if missing:
            return False, "missing_required_tables", details
        strategy_rows = conn.execute('SELECT COUNT(*) FROM "\u7b56\u7565\u4fe1\u606f"').fetchone()[0]
        latest_trade_date = conn.execute(
            'SELECT MAX("\u4ea4\u6613\u65e5\u671f") FROM "\u7b56\u7565\u65e5\u5ea6\u4e1a\u7ee9"'
        ).fetchone()[0]
        details["strategy_rows"] = strategy_rows
        details["latest_trade_date"] = latest_trade_date
        if int(strategy_rows or 0) <= 0:
            return False, "empty_strategy_table", details
    finally:
        conn.close()
    return True, "ok", details


def find_latest_valid_backup(backup_dir: Path) -> tuple[Path | None, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    if not backup_dir.exists():
        attempts.append({"backup_dir": str(backup_dir), "valid": False, "reason": "backup_dir_missing"})
        return None, attempts
    candidates = sorted(
        backup_dir.glob("analysis_zh_current*.sqlite"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        valid, reason, details = validate_backup(path)
        details["valid"] = valid
        details["reason"] = reason
        attempts.append(details)
        if valid:
            return path, attempts
    return None, attempts


def restore_from_backup(db_path: Path, backup_dir: Path) -> dict[str, Any]:
    backup_path, attempts = find_latest_valid_backup(backup_dir)
    repair: dict[str, Any] = {"backup_dir": str(backup_dir), "attempts": attempts}
    if backup_path is None:
        repair["status"] = "failed"
        repair["error"] = "no_valid_backup_found"
        return repair

    db_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    quarantine_dir = backup_dir / "runtime_repair_quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    moved_to = None
    if db_path.exists():
        moved_to = quarantine_dir / f"{db_path.name}.bad_{stamp}"
        shutil.move(str(db_path), str(moved_to))
    for suffix in ("-wal", "-shm", "-journal"):
        runtime_path = db_path.parent / f"{db_path.name}{suffix}"
        if runtime_path.exists():
            runtime_path.unlink()
    shutil.copy2(backup_path, db_path)
    repair.update(
        {
            "status": "restored",
            "source_backup": str(backup_path),
            "restored_db": str(db_path),
            "quarantined_original": str(moved_to) if moved_to else None,
        }
    )
    return repair


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    db_path = args.db_path
    if not db_path.is_absolute():
        db_path = project_root / db_path
    db_path = db_path.resolve()
    backup_dir = args.backup_dir
    if not backup_dir.is_absolute():
        backup_dir = project_root / backup_dir
    marker_path = args.full_check_marker or (project_root / "data" / "runtime_health" / "last_full_quick_check.json")
    if not marker_path.is_absolute():
        marker_path = project_root / marker_path
    abnormal_exit_flag = args.abnormal_exit_flag or (
        project_root / "data" / "runtime_health" / "require_full_quick_check.flag"
    )
    if not abnormal_exit_flag.is_absolute():
        abnormal_exit_flag = project_root / abnormal_exit_flag
    integrity_mode, integrity_reason = resolve_integrity_mode(
        args.integrity_mode,
        marker_path=marker_path,
        abnormal_exit_flag=abnormal_exit_flag,
        interval_days=args.full_check_interval_days,
    )
    if not args.json:
        print(
            f"[DB-CHECK] integrity_mode={integrity_mode} reason={integrity_reason} "
            f"full_interval_days={max(1, args.full_check_interval_days)}",
            flush=True,
        )
    result = check_database(
        db_path,
        integrity_mode=integrity_mode,
        heartbeat_seconds=args.heartbeat_seconds,
        quiet=args.json,
    )
    result["integrity_reason"] = integrity_reason
    result["full_check_marker"] = str(marker_path)
    result["abnormal_exit_flag"] = str(abnormal_exit_flag)
    if args.repair_empty and database_needs_empty_repair(result):
        repair_result = restore_from_backup(db_path, backup_dir.resolve())
        result["repair"] = repair_result
        if repair_result.get("status") == "restored":
            result = check_database(
                db_path,
                integrity_mode="full",
                heartbeat_seconds=args.heartbeat_seconds,
                quiet=args.json,
            )
            result["repair"] = repair_result
            result["integrity_reason"] = "database_restored_from_backup"
            integrity_mode = "full"
    if integrity_mode == "full" and not result.get("errors") and result.get("quick_check") == "ok":
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps(
                {
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "database": str(db_path),
                    "database_size": db_path.stat().st_size,
                    "quick_check_seconds": result.get("quick_check_seconds"),
                    "reason": result.get("integrity_reason"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        abnormal_exit_flag.unlink(missing_ok=True)
    result["status"] = "error" if result["errors"] else ("warn" if result["warnings"] else "ok")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
