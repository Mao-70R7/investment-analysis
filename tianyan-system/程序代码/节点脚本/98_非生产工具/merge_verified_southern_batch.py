from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return payload


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def quick_check(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=120)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        return str(row[0] if row else "missing")
    finally:
        connection.close()


def southern_counts(path: Path) -> dict[str, Any]:
    channel = "southern"
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=120)
    try:
        return {
            "strategy": int(
                connection.execute(
                    'SELECT COUNT(*) FROM "策略信息" WHERE "渠道ID"=?', (channel,)
                ).fetchone()[0]
            ),
            "performance": int(
                connection.execute(
                    'SELECT COUNT(*) FROM "策略日度业绩" WHERE "渠道ID"=?', (channel,)
                ).fetchone()[0]
            ),
            "currentHolding": int(
                connection.execute(
                    'SELECT COUNT(*) FROM "策略当前持仓" WHERE "渠道ID"=?', (channel,)
                ).fetchone()[0]
            ),
            "historicalHolding": int(
                connection.execute(
                    'SELECT COUNT(*) FROM "策略历史持仓" WHERE "渠道ID"=?', (channel,)
                ).fetchone()[0]
            ),
            "rebalanceEvent": int(
                connection.execute(
                    'SELECT COUNT(*) FROM "策略调仓事件" WHERE "渠道ID"=?', (channel,)
                ).fetchone()[0]
            ),
            "rebalanceDetail": int(
                connection.execute(
                    'SELECT COUNT(*) FROM "策略调仓明细" WHERE "渠道ID"=?', (channel,)
                ).fetchone()[0]
            ),
            "benchmarkComponent": int(
                connection.execute(
                    'SELECT COUNT(*) FROM "策略业绩基准成分" WHERE "渠道ID"=?', (channel,)
                ).fetchone()[0]
            ),
            "latestPerformanceDate": connection.execute(
                'SELECT MAX("交易日期") FROM "策略日度业绩" WHERE "渠道ID"=?', (channel,)
            ).fetchone()[0],
            "latestPerformanceStrategy": int(
                connection.execute(
                    '''SELECT COUNT(DISTINCT "渠道策略ID") FROM "策略日度业绩"
                       WHERE "渠道ID"=? AND "交易日期"=(
                           SELECT MAX("交易日期") FROM "策略日度业绩" WHERE "渠道ID"=?
                       )''',
                    (channel, channel),
                ).fetchone()[0]
            ),
        }
    finally:
        connection.close()


def expected_counts(summary: dict[str, Any]) -> dict[str, Any]:
    counts = summary.get("counts") or {}
    coverage = summary.get("coverage") or {}
    return {
        "strategy": int(summary.get("strategy_total") or 0),
        "performance": int(counts.get("strategy_performance_daily") or 0),
        "currentHolding": int(counts.get("strategy_fund_snapshot") or 0),
        "historicalHolding": int(counts.get("strategy_fund_snapshot_history") or 0),
        "rebalanceEvent": int(counts.get("strategy_rebalance_event") or 0),
        "rebalanceDetail": int(counts.get("strategy_rebalance_fund_delta") or 0),
        "latestPerformanceDate": summary.get("source_latest_nav_date"),
        "latestPerformanceStrategy": int(coverage.get("performance_with_rows") or 0),
    }


def validate_source(summary: dict[str, Any], audit: dict[str, Any], run_id: str) -> None:
    if summary.get("run_id") != run_id:
        raise RuntimeError(f"summary run mismatch: {summary.get('run_id')} != {run_id}")
    if summary.get("audit_status") != "passed":
        raise RuntimeError(f"summary audit did not pass: {summary.get('audit_status')}")
    if not summary.get("catalog_discovery_complete") or not summary.get("catalog_batch_closed"):
        raise RuntimeError("southern catalog batch is not complete and closed")
    if audit.get("status") != "passed" or int(audit.get("error_count") or 0) != 0:
        raise RuntimeError("specialized Southern audit did not pass")
    expected = expected_counts(summary)
    if expected["strategy"] <= 0 or expected["performance"] <= 0 or expected["historicalHolding"] <= 0:
        raise RuntimeError(f"required source counts are empty: {expected}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up production and transactionally load one verified Southern normalized batch."
    )
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--audit-path", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace_root = args.workspace_root.resolve()
    framework_root = workspace_root / "程序代码" / "节点脚本" / "00_调度框架"
    production_root = workspace_root / "程序代码" / "节点脚本" / "_共享组件" / "生产程序"
    sys.path.insert(0, str(framework_root))
    sys.path.insert(0, str(production_root))

    from node_runner import acquire_resource_lock, release_resource_lock
    from runtime_workspace import sqlite_backup
    from workspace import load_workspace

    workspace = load_workspace(workspace_root)
    summary_path = args.summary_path.resolve()
    audit_path = args.audit_path.resolve()
    summary = read_json(summary_path)
    audit = read_json(audit_path)
    validate_source(summary, audit, args.run_id)

    run_id = f"manual-southern-merge-{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')}"
    daily_lock: tuple[Path, str] | None = None
    write_lock: tuple[Path, str] | None = None
    metadata_path = workspace.log_root / "southern_merge" / run_id / "merge_result.json"
    result: dict[str, Any] = {
        "runId": run_id,
        "sourceRunId": args.run_id,
        "status": "running",
        "startedAt": now_text(),
        "summaryPath": str(summary_path),
        "auditPath": str(audit_path),
        "databasePath": str(workspace.main_db),
    }
    try:
        daily_lock = acquire_resource_lock(workspace, "daily_update", run_id, "manual_southern_merge")
        write_lock = acquire_resource_lock(workspace, "main_db_write", run_id, "manual_southern_merge")

        source_check = quick_check(workspace.main_db)
        if source_check != "ok":
            raise RuntimeError(f"production quick_check failed before merge: {source_check}")
        before = southern_counts(workspace.main_db)

        backup_path = workspace.backup_root / f"analysis_zh_current_pre_southern_{run_id}.sqlite"
        sqlite_backup(workspace.main_db, backup_path)
        backup_check = quick_check(backup_path)
        if backup_check != "ok":
            raise RuntimeError(f"backup quick_check failed: {backup_check}")

        env = os.environ.copy()
        env["SOUTHERN_COLLECT_RUN_ID"] = args.run_id
        command = [
            sys.executable,
            "-X",
            "utf8",
            str(production_root / "load_analysis_zh_current_sqlite.py"),
            "--db-path",
            str(workspace.main_db),
            "--schema-path",
            str(workspace.code_root / "schemas" / "analysis_zh_current.sql"),
            "--normalized-root",
            str(workspace.normalized_root),
            "--channels",
            "southern",
            "--keep-existing-db",
            "--strategy-catalog-summary",
            str(summary_path),
        ]
        completed = subprocess.run(command, cwd=workspace_root, env=env, text=True, encoding="utf-8")
        if completed.returncode != 0:
            raise RuntimeError(f"Southern loader failed with exit code {completed.returncode}")

        after = southern_counts(workspace.main_db)
        expected = expected_counts(summary)
        mismatches = {
            key: {"expected": expected[key], "actual": after.get(key)}
            for key in expected
            if after.get(key) != expected[key]
        }
        if mismatches:
            raise RuntimeError(f"post-load Southern count mismatch: {mismatches}")
        final_check = quick_check(workspace.main_db)
        if final_check != "ok":
            raise RuntimeError(f"production quick_check failed after merge: {final_check}")

        result.update(
            {
                "status": "success",
                "finishedAt": now_text(),
                "backupPath": str(backup_path),
                "backupBytes": backup_path.stat().st_size,
                "backupQuickCheck": backup_check,
                "databaseQuickCheck": final_check,
                "before": before,
                "after": after,
                "expected": expected,
            }
        )
        write_json_atomic(metadata_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except BaseException as exc:
        result.update({"status": "failed", "finishedAt": now_text(), "error": str(exc)})
        write_json_atomic(metadata_path, result)
        raise
    finally:
        if write_lock is not None:
            release_resource_lock(*write_lock)
        if daily_lock is not None:
            release_resource_lock(*daily_lock)


if __name__ == "__main__":
    main()
