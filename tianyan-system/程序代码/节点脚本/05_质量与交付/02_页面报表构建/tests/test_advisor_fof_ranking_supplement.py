from __future__ import annotations

import importlib.util
import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[3] / "_共享组件" / "生产程序"
SCRIPT_PATH = SCRIPT_DIR / "build_advisor_fof_ranking_pack.py"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("advisor_fof_ranking_pack", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AdvisorFofRankingSupplementTests(unittest.TestCase):
    def test_end_date_prefers_refreshed_fund_nav_over_stale_snapshot(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE "策略标准业绩净值" ("交易日期" TEXT);
            CREATE TABLE "基金日度净值" ("交易日期" TEXT);
            CREATE TABLE "公募基金产品绩效快照" ("绩效截止日期" TEXT);
            INSERT INTO "策略标准业绩净值" VALUES ('2026-07-30');
            INSERT INTO "基金日度净值" VALUES ('2026-07-30');
            INSERT INTO "公募基金产品绩效快照" VALUES ('2026-07-16');
            """
        )

        self.assertEqual(MODULE.resolve_end_date(conn), date(2026, 7, 30))

    def test_only_rankable_supplemental_strategies_enter_common_ranking(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE "策略信息" (
                "统一策略ID" TEXT,
                "渠道ID" TEXT,
                "渠道策略ID" TEXT,
                "策略名称" TEXT,
                "投顾机构" TEXT,
                "风险等级" TEXT,
                "策略类型" TEXT,
                "业绩基准" TEXT,
                "策略状态" TEXT
            );
            CREATE TABLE "渠道信息" ("渠道ID" TEXT, "渠道名称" TEXT);
            CREATE TABLE "策略治理标签" (
                "统一策略ID" TEXT,
                "是否纳入常规排名" INTEGER,
                "治理状态" TEXT,
                "分析分组" TEXT,
                "是否已停止" INTEGER,
                "是否业绩异常" INTEGER
            );
            INSERT INTO "渠道信息" VALUES ('gfsec_fima', '广发证券易淘金/财富管家');
            """
        )
        strategies = [
            ("gfsec_fima__regular", 1, "正常运行"),
            ("gfsec_fima__target", 0, "目标盈期次"),
            ("gfsec_fima__signal", 0, "信号类策略"),
        ]
        for strategy_id, rankable, governance_status in strategies:
            conn.execute(
                'INSERT INTO "策略信息" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    strategy_id,
                    "gfsec_fima",
                    strategy_id,
                    strategy_id,
                    "示例机构",
                    "R3",
                    "示例类型",
                    "示例基准",
                    "public",
                ),
            )
            conn.execute(
                'INSERT INTO "策略治理标签" VALUES (?, ?, ?, ?, 0, 0)',
                (strategy_id, rankable, governance_status, governance_status),
            )

        rows = MODULE.fetch_supplemental_strategy_rows(conn, set())

        self.assertEqual([row["统一策略ID"] for row in rows], ["gfsec_fima__regular"])


if __name__ == "__main__":
    unittest.main()
