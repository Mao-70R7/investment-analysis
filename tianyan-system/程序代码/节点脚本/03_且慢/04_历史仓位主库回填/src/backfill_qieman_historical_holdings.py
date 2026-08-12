from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transactionally backfill accepted Qieman historical holdings.")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--code-root", type=Path, required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--schema-path", type=Path, required=True)
    parser.add_argument("--normalized-root", type=Path, required=True)
    parser.add_argument("--accepted-state", type=Path, required=True)
    parser.add_argument("--result-path", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_loader(code_root: Path):
    path = code_root / "节点脚本" / "_共享组件" / "生产程序" / "load_analysis_zh_current_sqlite.py"
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("qieman_history_loader", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"unable to import loader: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot_coverage(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        '''
        WITH snapshot AS (
            SELECT "统一策略ID", "历史快照ID", COUNT(*) AS row_count,
                   SUM(CASE WHEN "是否精确权重"=1 AND "基金权重_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS exact_count,
                   SUM(COALESCE("基金权重_百分比", 0)) AS weight_sum
            FROM "策略历史持仓"
            WHERE "渠道ID"='qieman'
            GROUP BY "统一策略ID", "历史快照ID"
        )
        SELECT COUNT(*) AS snapshot_count,
               COUNT(DISTINCT "统一策略ID") AS strategy_count,
               SUM(CASE WHEN exact_count=row_count AND weight_sum BETWEEN 99 AND 101 THEN 1 ELSE 0 END) AS complete_snapshot_count,
               COUNT(DISTINCT CASE WHEN exact_count=row_count AND weight_sum BETWEEN 99 AND 101 THEN "统一策略ID" END) AS complete_strategy_count
        FROM snapshot
        '''
    ).fetchone()
    return {
        "snapshotCount": int(row[0] or 0),
        "strategyCount": int(row[1] or 0),
        "completeSnapshotCount": int(row[2] or 0),
        "completeStrategyCount": int(row[3] or 0),
    }


def main() -> None:
    args = parse_args()
    code_root = args.code_root.resolve()
    normalized_root = args.normalized_root.resolve()
    accepted_state = read_json(args.accepted_state.resolve())
    run_id = str(accepted_state.get("run_id") or "").strip()
    summary_path = Path(str(accepted_state.get("summary_path") or "")).resolve()
    if not run_id or not summary_path.is_file():
        raise SystemExit("accepted Qieman run or summary is missing")
    summary = read_json(summary_path)
    if str(summary.get("run_id") or "").strip() != run_id:
        raise SystemExit("accepted Qieman state and summary run IDs do not match")
    expected_rows = int((summary.get("counts") or {}).get("strategy_fund_snapshot_history") or 0)
    source_path = normalized_root / "qieman" / "strategy_fund_snapshot_history" / run_id / f"{run_id}.jsonl"
    if expected_rows <= 0 or not source_path.is_file():
        raise SystemExit("accepted Qieman historical holding snapshot is missing or empty")
    result: dict[str, Any] = {
        "state": "qieman_historical_holdings_backfill_checked" if args.dry_run else "qieman_historical_holdings_backfilled",
        "run_id": run_id,
        "summary_path": str(summary_path),
        "source_path": str(source_path),
        "expected_rows": expected_rows,
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        atomic_json(args.result_path.resolve(), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    loader = load_loader(code_root)
    previous_root = loader.NORMALIZED_ROOT
    previous_run = os.environ.get("QIEMAN_COLLECT_RUN_ID")
    loader.NORMALIZED_ROOT = normalized_root
    os.environ["QIEMAN_COLLECT_RUN_ID"] = run_id
    connection = loader.init_db(args.db_path.resolve(), args.schema_path.resolve(), keep_existing_db=True)
    try:
        if connection.in_transaction:
            connection.commit()
        connection.execute("PRAGMA busy_timeout=120000")
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute('DELETE FROM "策略历史持仓" WHERE "渠道ID"=?', ("qieman",))
            counters: dict[str, int] = defaultdict(int)
            loader.import_channel_historical_holdings(
                connection,
                "qieman",
                {run_id: {"captured_at": summary.get("captured_at")}},
                counters,
            )
            actual_rows = int(
                connection.execute(
                    'SELECT COUNT(*) FROM "策略历史持仓" WHERE "渠道ID"=?',
                    ("qieman",),
                ).fetchone()[0]
            )
            skipped_strategy = int(counters.get("策略历史持仓_策略缺失跳过") or 0)
            skipped_key = int(counters.get("策略历史持仓_业务键缺失跳过") or 0)
            coverage = snapshot_coverage(connection)
            if (
                actual_rows != expected_rows
                or int(counters.get("策略历史持仓") or 0) != expected_rows
                or skipped_strategy
                or skipped_key
                or coverage["completeSnapshotCount"] <= 0
            ):
                raise RuntimeError(
                    "Qieman historical holding backfill did not close: "
                    f"expected={expected_rows}, actual={actual_rows}, "
                    f"skipped_strategy={skipped_strategy}, skipped_key={skipped_key}, coverage={coverage}"
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
        result.update({"actual_rows": actual_rows, "coverage": coverage, "counters": dict(counters)})
        atomic_json(args.result_path.resolve(), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        connection.close()
        loader.NORMALIZED_ROOT = previous_root
        if previous_run is None:
            os.environ.pop("QIEMAN_COLLECT_RUN_ID", None)
        else:
            os.environ["QIEMAN_COLLECT_RUN_ID"] = previous_run


if __name__ == "__main__":
    main()
