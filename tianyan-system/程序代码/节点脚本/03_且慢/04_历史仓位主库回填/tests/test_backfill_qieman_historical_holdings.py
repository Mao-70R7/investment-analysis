from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "backfill_qieman_historical_holdings.py"
SPEC = importlib.util.spec_from_file_location("qieman_history_backfill", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_snapshot_coverage_only_counts_closed_exact_snapshots() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        '''CREATE TABLE "策略历史持仓" (
               "统一策略ID" TEXT, "渠道ID" TEXT, "历史快照ID" TEXT,
               "基金权重_百分比" REAL, "是否精确权重" INTEGER
           )'''
    )
    connection.executemany(
        'INSERT INTO "策略历史持仓" VALUES (?, ?, ?, ?, ?)',
        [
            ("qieman__A", "qieman", "A-1", 50.0, 1),
            ("qieman__A", "qieman", "A-1", 50.0, 1),
            ("qieman__B", "qieman", "B-1", 60.0, 1),
            ("qieman__B", "qieman", "B-1", None, 0),
        ],
    )
    assert MODULE.snapshot_coverage(connection) == {
        "snapshotCount": 2,
        "strategyCount": 2,
        "completeSnapshotCount": 1,
        "completeStrategyCount": 1,
    }
    connection.close()
