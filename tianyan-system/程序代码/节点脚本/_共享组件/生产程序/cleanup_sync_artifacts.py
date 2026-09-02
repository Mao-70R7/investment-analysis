from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
ANALYSIS_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
RAW_INDEX_DB = PROJECT_ROOT / "data" / "advisor_monitor.sqlite"
RUN_LOCK = PROJECT_ROOT / "data" / "run_daily_incremental.lock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Safely clean Syncthing conflict/temp files, Python caches, and old DB backups. "
            "Dry-run is the default."
        )
    )
    parser.add_argument("--execute", action="store_true", help="Actually delete files. Default only writes a report.")
    parser.add_argument("--report-root", type=Path, help="Optional external report directory for root conflict cleanup.")
    parser.add_argument("--output-dir", type=Path, help="Where to write cleanup_summary.json and cleanup_actions.jsonl.")
    return parser.parse_args()


def safe_path(path: Path, root: Path = PROJECT_ROOT) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"path escapes root: {path}")
    return resolved


def rel(path: Path, root: Path = PROJECT_ROOT) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def quick_check(db_path: Path) -> str:
    if not db_path.exists():
        raise FileNotFoundError(f"missing database: {db_path}")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("PRAGMA quick_check").fetchone()
    return str(row[0]) if row else "no_result"


def preflight(execute: bool) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "run_lock_exists": RUN_LOCK.exists(),
        "analysis_db_exists": ANALYSIS_DB.exists(),
        "raw_index_db_exists": RAW_INDEX_DB.exists(),
    }
    if RUN_LOCK.exists():
        checks["run_lock"] = str(RUN_LOCK)
        if execute:
            raise RuntimeError(f"incremental lock exists: {RUN_LOCK}")
    checks["analysis_quick_check"] = quick_check(ANALYSIS_DB)
    checks["raw_index_quick_check"] = quick_check(RAW_INDEX_DB)
    if execute:
        failed = [
            key
            for key in ("analysis_quick_check", "raw_index_quick_check")
            if checks.get(key) != "ok"
        ]
        if failed:
            raise RuntimeError(f"SQLite quick_check failed: {failed}")
    return checks


def is_syncthing_conflict_or_temp(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("~syncthing~")
        or ".sync-conflict-" in name
        or name.startswith(".sync-conflict-")
    )


def is_source_like(path: Path) -> bool:
    return path.suffix.lower() in {".py", ".ps1", ".bat", ".cmd", ".js", ".ts", ".sh"}


def add_file(actions: list[dict[str, Any]], path: Path, reason: str, root: Path = PROJECT_ROOT) -> None:
    if not path.exists() or not path.is_file():
        return
    safe_path(path, root)
    actions.append(
        {
            "action": "delete_file",
            "path": rel(path, root),
            "absolute_path": str(path.resolve()),
            "bytes": path.stat().st_size,
            "reason": reason,
        }
    )


def add_dir(actions: list[dict[str, Any]], path: Path, reason: str, root: Path = PROJECT_ROOT) -> None:
    if not path.exists() or not path.is_dir():
        return
    safe_path(path, root)
    size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    actions.append(
        {
            "action": "delete_dir",
            "path": rel(path, root),
            "absolute_path": str(path.resolve()),
            "bytes": size,
            "reason": reason,
        }
    )


def latest_valid_backup(backups_dir: Path) -> Path | None:
    if not backups_dir.exists():
        return None
    candidates = [
        item
        for item in backups_dir.iterdir()
        if item.is_file()
        and item.suffix.lower() == ".sqlite"
        and not item.name.startswith("~syncthing~")
        and ".sync-conflict-" not in item.name
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def collect_project_actions() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}

    keep_backup = latest_valid_backup(PROJECT_ROOT / "data" / "backups")
    metadata["kept_latest_valid_backup"] = rel(keep_backup) if keep_backup else None

    for pycache in PROJECT_ROOT.rglob("__pycache__"):
        add_dir(actions, pycache, "Python cache directory")
    for pyc in PROJECT_ROOT.rglob("*.pyc"):
        add_file(actions, pyc, "Python compiled cache")

    for item in PROJECT_ROOT.rglob("*"):
        if not item.is_file():
            continue
        if ".git" in item.parts:
            continue
        if not is_syncthing_conflict_or_temp(item):
            continue
        reason = "Syncthing conflict/temp artifact"
        if item.parent == PROJECT_ROOT / "data" and item.name.startswith("~syncthing~") and ".sqlite" in item.name:
            reason = "Syncthing interrupted SQLite temp file in data root"
        add_file(actions, item, reason)

    details_dir = PROJECT_ROOT / "site" / "basic_data" / "data" / "details"
    if details_dir.exists():
        for item in details_dir.iterdir():
            if item.is_file() and is_syncthing_conflict_or_temp(item):
                add_file(actions, item, "Basic-data detail conflict/temp artifact")

    backups_dir = PROJECT_ROOT / "data" / "backups"
    if backups_dir.exists():
        for item in backups_dir.iterdir():
            if not item.is_file():
                continue
            if keep_backup and item.resolve() == keep_backup.resolve():
                continue
            if item.suffix.lower() == ".sqlite" or is_syncthing_conflict_or_temp(item):
                add_file(actions, item, "Old or temporary database backup")

    return dedupe_actions(actions), metadata


def collect_report_actions(report_root: Path | None) -> list[dict[str, Any]]:
    if report_root is None:
        return []
    root = report_root.resolve()
    if not root.exists():
        raise FileNotFoundError(f"report root does not exist: {root}")
    actions: list[dict[str, Any]] = []
    for item in root.iterdir():
        if item.is_file() and is_syncthing_conflict_or_temp(item):
            add_file(actions, item, "Report-root Syncthing conflict/temp artifact", root)
    return dedupe_actions(actions)


def dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for action in actions:
        key = str(action["absolute_path"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(action)
    return unique


def validate_actions(actions: list[dict[str, Any]]) -> None:
    unsafe_source_actions = [
        action
        for action in actions
        if action["action"] == "delete_file"
        and is_source_like(Path(str(action["absolute_path"])))
        and "sync-conflict" not in Path(str(action["absolute_path"])).name
        and not Path(str(action["absolute_path"])).name.startswith("~syncthing~")
    ]
    if unsafe_source_actions:
        raise RuntimeError(f"refusing to delete source-like files: {unsafe_source_actions[:5]}")


def execute_actions(actions: list[dict[str, Any]]) -> None:
    for action in actions:
        path = Path(str(action["absolute_path"]))
        if action["action"] == "delete_file":
            path.unlink(missing_ok=True)
        elif action["action"] == "delete_dir":
            shutil.rmtree(path, ignore_errors=True)


def summarize(actions: list[dict[str, Any]]) -> dict[str, Any]:
    count_by_reason: Counter[str] = Counter()
    bytes_by_reason: Counter[str] = Counter()
    for action in actions:
        reason = str(action["reason"])
        count_by_reason[reason] += 1
        bytes_by_reason[reason] += int(action.get("bytes") or 0)
    total_bytes = sum(int(action.get("bytes") or 0) for action in actions)
    return {
        "action_count": len(actions),
        "bytes_total": total_bytes,
        "mb_total": round(total_bytes / 1024 / 1024, 3),
        "count_by_reason": dict(count_by_reason),
        "mb_by_reason": {key: round(value / 1024 / 1024, 3) for key, value in bytes_by_reason.items()},
    }


def write_reports(output_dir: Path, summary: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    safe_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cleanup_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "cleanup_actions.jsonl").write_text(
        "\n".join(json.dumps(action, ensure_ascii=False) for action in actions),
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    now = datetime.now().astimezone()
    output_dir = args.output_dir or PROJECT_ROOT / "outputs" / "current_cleanup" / now.strftime("%Y-%m-%d") / now.strftime(
        "%Y%m%dT%H%M%S%z"
    )
    output_dir = safe_path(output_dir)

    checks = preflight(args.execute)
    project_actions, metadata = collect_project_actions()
    report_actions = collect_report_actions(args.report_root)
    all_actions = dedupe_actions(project_actions + report_actions)
    validate_actions(all_actions)

    summary = {
        "generated_at": now.isoformat(timespec="seconds"),
        "mode": "execute" if args.execute else "dry_run",
        "project_root": str(PROJECT_ROOT),
        "report_root": str(args.report_root.resolve()) if args.report_root else None,
        "preflight": checks,
        "metadata": metadata,
        "cleanup": summarize(all_actions),
        "output_dir": str(output_dir),
    }
    write_reports(output_dir, summary, all_actions)
    if args.execute:
        execute_actions(all_actions)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
