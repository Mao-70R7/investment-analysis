from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
REQUIRED_TABLES = (
    "\u7b56\u7565\u4fe1\u606f",
    "\u7b56\u7565\u65e5\u5ea6\u4e1a\u7ee9",
    "\u57fa\u91d1\u4fe1\u606f",
    "\u57fa\u91d1\u65e5\u5ea6\u51c0\u503c",
)
ORPHAN_FINAL_GRACE_SECONDS = 24 * 60 * 60
MAX_SUCCESSFUL_BACKUPS = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and rotate validated successful backups of the analysis SQLite database."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument(
        "--retain",
        type=int,
        default=MAX_SUCCESSFUL_BACKUPS,
        help="Requested successful backup retention. The script enforces a hard maximum of one version.",
    )
    parser.add_argument("--run-id", default=datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z"))
    parser.add_argument("--required-table", action="append", default=[])
    parser.add_argument("--state-db", type=Path)
    parser.add_argument("--require-stage", action="append", default=[])
    parser.add_argument("--minimum-free-gib", type=float, default=0)
    parser.add_argument("--result-path", type=Path)
    parser.add_argument(
        "--prune-only",
        action="store_true",
        help="Validate the latest successful backup, then remove every other database backup artifact.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    last_error: PermissionError | None = None
    for attempt in range(10):
        try:
            temp_path.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt < 9:
                time.sleep(min(0.05 * (2**attempt), 1.0))
    if last_error is not None:
        raise last_error


def validate_backup(path: Path, required_tables: tuple[str, ...]) -> dict[str, Any]:
    # A successful backup is immutable while it is being validated.  Without
    # immutable=1 SQLite may create -wal/-shm sidecars even for mode=ro when the
    # copied database header records WAL journal mode.
    with closing(
        sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True, timeout=60)
    ) as conn:
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        table_names = {
            str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing_tables = sorted(set(required_tables) - table_names)
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    return {
        "quick_check": quick_check,
        "missing_required_tables": missing_tables,
        "page_count": page_count,
        "page_size": page_size,
        "logical_bytes": page_count * page_size,
        "valid": quick_check == "ok" and not missing_tables,
    }


def successful_backups(backup_dir: Path) -> list[tuple[Path, Path, dict[str, Any]]]:
    backups: list[tuple[Path, Path, dict[str, Any]]] = []
    for metadata_path in backup_dir.glob("analysis_zh_current_*.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        db_path = backup_dir / str(payload.get("backup_file") or "")
        if payload.get("status") == "success" and db_path.is_file():
            backups.append((db_path, metadata_path, payload))
    return sorted(
        backups,
        key=lambda item: (
            str(item[2].get("completed_at") or ""),
            item[1].stat().st_mtime_ns,
        ),
        reverse=True,
    )


def successful_backup_for_run(backup_dir: Path, run_id: str) -> tuple[Path, Path, dict[str, Any]] | None:
    return next((item for item in successful_backups(backup_dir) if item[2].get("run_id") == run_id), None)


def cleanup_incomplete_artifacts(backup_dir: Path) -> list[str]:
    removed: list[str] = []
    for pattern in (
        "*.partial",
        "*.partial-journal",
        "*.partial-wal",
        "*.partial-shm",
        "*.tmp",
        "*.tmp-journal",
        "*.tmp-wal",
        "*.tmp-shm",
    ):
        for path in backup_dir.glob(pattern):
            path.unlink(missing_ok=True)
            removed.append(str(path))
    for metadata_path in backup_dir.glob("analysis_zh_current_*.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") == "success":
            continue
        backup_name = str(payload.get("backup_file") or "").strip()
        if backup_name:
            failed_backup = backup_dir / Path(backup_name).name
            failed_backup.unlink(missing_ok=True)
            removed.append(str(failed_backup))
        metadata_path.unlink(missing_ok=True)
        removed.append(str(metadata_path))
    now = time.time()
    for database_path in backup_dir.glob("analysis_zh_current_*.sqlite"):
        metadata_path = database_path.with_suffix(".json")
        if metadata_path.exists():
            continue
        try:
            age_seconds = now - database_path.stat().st_mtime
        except OSError:
            continue
        if age_seconds < ORPHAN_FINAL_GRACE_SECONDS:
            continue
        database_path.unlink(missing_ok=True)
        removed.append(str(database_path))
    return removed


def validate_run_prerequisites(state_db: Path | None, run_id: str, required_stages: list[str]) -> None:
    if not required_stages:
        return
    if state_db is None or not state_db.is_file():
        raise RuntimeError("backup prerequisite state database is unavailable")
    with closing(sqlite3.connect(f"file:{state_db.resolve().as_posix()}?mode=ro", uri=True, timeout=60)) as conn:
        run_row = conn.execute(
            "SELECT status, metadata_json FROM daily_update_run WHERE run_id=?",
            (run_id,),
        ).fetchone()
        rows = {
            str(row[0]): (str(row[1]), row[2])
            for row in conn.execute(
                "SELECT node_id, status, returncode FROM daily_update_node WHERE run_id=?",
                (run_id,),
            )
        }
    if run_row is None:
        raise RuntimeError(f"backup run state not found: {run_id}")
    run_status = str(run_row[0] or "")
    try:
        run_metadata = json.loads(str(run_row[1] or "{}"))
    except json.JSONDecodeError:
        run_metadata = {}
    if bool(run_metadata.get("dryRun")):
        raise RuntimeError("dry-run versions must not be backed up")
    if run_status != "running":
        raise RuntimeError(f"failed or finalized run must not create a new backup: status={run_status}")
    missing = [stage for stage in required_stages if stage not in rows]
    failed = [
        stage
        for stage in required_stages
        if stage in rows and not (rows[stage][0] in {"success", "warn"} and int(rows[stage][1] or 0) == 0)
    ]
    if missing or failed:
        raise RuntimeError(
            f"backup prerequisites not satisfied: missing={missing}, incomplete_or_failed={failed}"
        )


def rotate_backups(backup_dir: Path, retain: int, protected_path: Path | None = None) -> list[str]:
    removed: list[str] = []
    backups = successful_backups(backup_dir)
    if protected_path is not None:
        protected_resolved = protected_path.resolve()
        backups.sort(key=lambda item: item[0].resolve() == protected_resolved, reverse=True)
    for db_path, metadata_path, _ in backups[max(1, retain) :]:
        db_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        removed.append(str(db_path))
    return removed


def cleanup_unmanaged_backup_artifacts(
    backup_dir: Path,
    protected_path: Path,
) -> list[str]:
    """Remove database copies outside the single validated successful-backup set.

    This runs only after a successful backup has been validated. It covers one-off
    governance snapshots and stale SQLite sidecars that are intentionally not part
    of the managed successful-backup metadata chain.
    """

    removed: list[str] = []
    backup_root = backup_dir.resolve()
    protected_resolved = protected_path.resolve()
    managed = {db_path.resolve() for db_path, _, _ in successful_backups(backup_dir)}
    managed.add(protected_resolved)

    for database_path in backup_dir.glob("analysis_zh_current*.sqlite"):
        resolved = database_path.resolve()
        if resolved.parent != backup_root:
            raise RuntimeError(f"backup cleanup target escaped backup root: {resolved}")
        if resolved in managed:
            continue
        database_path.unlink(missing_ok=True)
        removed.append(str(database_path))

    for pattern in ("analysis_zh_current*.sqlite-wal", "analysis_zh_current*.sqlite-shm"):
        for sidecar_path in backup_dir.glob(pattern):
            resolved = sidecar_path.resolve()
            if resolved.parent != backup_root:
                raise RuntimeError(f"backup sidecar target escaped backup root: {resolved}")
            sidecar_path.unlink(missing_ok=True)
            removed.append(str(sidecar_path))
    return removed


def prune_backups(args: argparse.Namespace) -> dict[str, Any]:
    backup_dir = args.backup_dir.resolve()
    backups = successful_backups(backup_dir)
    if not backups:
        raise RuntimeError("no validated successful backup is available for pruning")
    latest_db, latest_metadata, latest_payload = backups[0]
    validation = validate_backup(latest_db, tuple(args.required_table) or REQUIRED_TABLES)
    if not validation["valid"]:
        raise RuntimeError(f"latest successful backup failed validation: {json.dumps(validation, ensure_ascii=False)}")
    if args.dry_run:
        return {
            "version": 1,
            "status": "dry_run",
            "action": "prune_only",
            "backup_path": str(latest_db),
            "metadata_path": str(latest_metadata),
            "run_id": latest_payload.get("run_id"),
            "validation": validation,
            "retain": MAX_SUCCESSFUL_BACKUPS,
        }

    removed_incomplete = cleanup_incomplete_artifacts(backup_dir)
    removed_backups = rotate_backups(backup_dir, MAX_SUCCESSFUL_BACKUPS, latest_db)
    removed_unmanaged = cleanup_unmanaged_backup_artifacts(backup_dir, latest_db)
    remaining = successful_backups(backup_dir)
    if len(remaining) != MAX_SUCCESSFUL_BACKUPS or remaining[0][0].resolve() != latest_db.resolve():
        raise RuntimeError("backup pruning did not leave exactly the latest successful version")
    return {
        "version": 1,
        "status": "success",
        "action": "prune_only",
        "backup_path": str(latest_db),
        "metadata_path": str(latest_metadata),
        "run_id": latest_payload.get("run_id"),
        "validation": validation,
        "retain": MAX_SUCCESSFUL_BACKUPS,
        "removed_incomplete_artifacts": removed_incomplete,
        "removed_backups": removed_backups,
        "removed_unmanaged_artifacts": removed_unmanaged,
        "remaining_successful_versions": len(remaining),
    }


def create_backup(args: argparse.Namespace) -> dict[str, Any]:
    source = args.db_path.resolve()
    backup_dir = args.backup_dir.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if args.retain < 1:
        raise ValueError("--retain must be at least 1")
    args.retain = min(int(args.retain), MAX_SUCCESSFUL_BACKUPS)

    validate_run_prerequisites(args.state_db, args.run_id, list(args.require_stage))
    if args.dry_run:
        return {
            "status": "dry_run",
            "source": str(source),
            "backup_dir": str(backup_dir),
            "retain": args.retain,
            "required_tables": list(tuple(args.required_table) or REQUIRED_TABLES),
            "required_stages": list(args.require_stage),
            "minimum_free_gib": args.minimum_free_gib,
        }

    backup_dir.mkdir(parents=True, exist_ok=True)
    cleanup_incomplete_artifacts(backup_dir)
    with closing(sqlite3.connect(source, timeout=120)) as checkpoint_conn:
        checkpoint_conn.execute("PRAGMA busy_timeout=120000")
        checkpoint_row = checkpoint_conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    checkpoint_status = {
        "busy": int(checkpoint_row[0] or 0),
        "log_frames": int(checkpoint_row[1] or 0),
        "checkpointed_frames": int(checkpoint_row[2] or 0),
    }
    source_stat = source.stat()
    latest_successes = successful_backups(backup_dir)
    if latest_successes:
        latest_db, _, latest_payload = latest_successes[0]
        unchanged = (
            int(latest_payload.get("source_bytes") or -1) == source_stat.st_size
            and int(latest_payload.get("source_mtime_ns") or -1) == source_stat.st_mtime_ns
        )
        fully_checkpointed = (
            checkpoint_status["busy"] == 0
            and checkpoint_status["log_frames"] == checkpoint_status["checkpointed_frames"]
        )
        if unchanged and fully_checkpointed:
            validation = validate_backup(latest_db, tuple(args.required_table) or REQUIRED_TABLES)
            if validation["valid"]:
                return {
                    **latest_payload,
                    "requested_run_id": args.run_id,
                    "reused_unchanged_source": True,
                    "validation": validation,
                    "source_checkpoint": checkpoint_status,
                    "retain": args.retain,
                    "removed_backups": rotate_backups(backup_dir, args.retain, latest_db),
                    "removed_unmanaged_artifacts": cleanup_unmanaged_backup_artifacts(
                        backup_dir, latest_db
                    ),
                }
    size_floor = int(source_stat.st_size * 1.05) + 1024 * 1024 * 1024
    configured_floor = int(max(0.0, float(args.minimum_free_gib)) * 1024**3)
    required_free_bytes = max(size_floor, configured_floor)
    free_bytes = shutil.disk_usage(backup_dir).free
    if free_bytes < required_free_bytes:
        raise RuntimeError(
            f"insufficient backup disk space: free={free_bytes}, required={required_free_bytes}"
        )
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    safe_run_id = "".join(ch for ch in args.run_id if ch.isalnum() or ch in "-_")
    stem = f"analysis_zh_current_{stamp}_{safe_run_id}"
    final_path = backup_dir / f"{stem}.sqlite"
    metadata_path = backup_dir / f"{stem}.json"
    partial_path = backup_dir / f".{stem}.{os.getpid()}.partial"
    required_tables = tuple(args.required_table) or REQUIRED_TABLES
    started_at = datetime.now().astimezone()

    committed = False
    try:
        with closing(sqlite3.connect(source, timeout=120)) as source_conn:
            source_conn.execute("PRAGMA busy_timeout=120000")
            source_conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            with closing(sqlite3.connect(partial_path, timeout=120)) as target_conn:
                pages_seen = 0

                def progress(status: int, remaining: int, total: int) -> None:
                    nonlocal pages_seen
                    copied = total - remaining
                    if copied - pages_seen >= 262144 or remaining == 0:
                        pages_seen = copied
                        print(
                            json.dumps(
                                {
                                    "event": "backup_progress",
                                    "copied_pages": copied,
                                    "total_pages": total,
                                    "remaining_pages": remaining,
                                    "sqlite_status": status,
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )

                source_conn.backup(target_conn, pages=8192, progress=progress, sleep=0.05)
                target_conn.commit()

        validation = validate_backup(partial_path, required_tables)
        if not validation["valid"]:
            raise RuntimeError(f"backup validation failed: {json.dumps(validation, ensure_ascii=False)}")
        partial_path.replace(final_path)
        completed_at = datetime.now().astimezone()
        payload = {
            "version": 1,
            "status": "success",
            "run_id": args.run_id,
            "source": str(source),
            "backup_file": final_path.name,
            "backup_path": str(final_path),
            "source_bytes": source.stat().st_size,
            "source_mtime_ns": source.stat().st_mtime_ns,
            "source_checkpoint": checkpoint_status,
            "backup_bytes": final_path.stat().st_size,
            "started_at": started_at.isoformat(timespec="microseconds"),
            "completed_at": completed_at.isoformat(timespec="microseconds"),
            "elapsed_seconds": round((completed_at - started_at).total_seconds(), 3),
            "validation": validation,
            "retain": args.retain,
        }
        atomic_write_json(metadata_path, payload)
        committed = True
    except Exception:
        partial_path.unlink(missing_ok=True)
        if not committed:
            final_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
        raise

    try:
        payload["removed_incomplete_artifacts"] = cleanup_incomplete_artifacts(backup_dir)
        payload["removed_backups"] = rotate_backups(backup_dir, args.retain, final_path)
        payload["removed_unmanaged_artifacts"] = cleanup_unmanaged_backup_artifacts(
            backup_dir, final_path
        )
        payload["rotation_status"] = "success"
        atomic_write_json(metadata_path, payload)
    except Exception as exc:  # The validated backup remains successful even when cleanup is temporarily blocked.
        payload["removed_backups"] = []
        payload["rotation_status"] = "warning"
        payload["rotation_warning"] = f"{type(exc).__name__}: {exc}"
        try:
            atomic_write_json(metadata_path, payload)
        except OSError:
            pass
    return payload


def main() -> None:
    args = parse_args()
    try:
        result = prune_backups(args) if args.prune_only else create_backup(args)
    except Exception as exc:  # noqa: BLE001 - command must emit a structured failure.
        result = {
            "status": "failed",
            "run_id": args.run_id,
            "error": f"{type(exc).__name__}: {exc}",
        }
        if args.result_path:
            atomic_write_json(args.result_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1) from exc
    if args.result_path:
        atomic_write_json(args.result_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
