from __future__ import annotations

import importlib.util
import sqlite3
import unittest
from pathlib import Path


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
SCRIPT_PATH = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_rebalance_quality_analysis.py"
POST_UPDATE_SCRIPT_PATH = (
    CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "run_ttfund_post_update_quality.py"
)
SPEC = importlib.util.spec_from_file_location("build_rebalance_quality_analysis", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RebalanceQualityAnalysisTests(unittest.TestCase):
    def test_post_update_chain_rebuilds_quality_after_nav_and_before_deviation(self) -> None:
        source = POST_UPDATE_SCRIPT_PATH.read_text(encoding="utf-8")
        heavy_steps_pos = source.index("heavy_steps: list[tuple[str, list[str]]] = [")
        reconstruct_pos = source.index('"10_reconstruct_strategy_nav"', heavy_steps_pos)
        quality_pos = source.index('"10b_build_rebalance_quality_analysis"', reconstruct_pos)
        deviation_pos = source.index('"11_analyze_official_deviation"', quality_pos)

        self.assertLess(reconstruct_pos, quality_pos)
        self.assertLess(quality_pos, deviation_pos)
        quality_step = source[quality_pos:deviation_pos]
        self.assertIn('str(SCRIPT_DIR / "build_rebalance_quality_analysis.py")', quality_step)
        self.assertIn('"--algorithm-version",', quality_step)
        self.assertIn('args.algorithm_version', quality_step)

    def test_money_market_daily_return_continues_adjusted_nav(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            '''
            CREATE TABLE "基金日度净值" (
                "基金代码" TEXT,
                "交易日期" TEXT,
                "复权净值" REAL,
                "累计净值" REAL,
                "单位净值" REAL,
                "日收益率_百分比" REAL
            )
            '''
        )
        conn.executemany(
            'INSERT INTO "基金日度净值" VALUES (?,?,?,?,?,?)',
            [
                ("000891", "2026-07-08", 100.0, None, None, 0.1),
                ("000891", "2026-07-09", None, None, None, 1.0),
                ("000891", "2026-07-10", None, None, None, 1.0),
                ("000891", "2026-07-11", None, None, None, 1.0),
            ],
        )

        start_date, end_date, interval_return = MODULE.FundNavLookup(conn).interval_return(
            "000891", "2026-07-08", "2026-07-11", end_closed=True
        )

        self.assertEqual(start_date, "2026-07-08")
        self.assertEqual(end_date, "2026-07-10")
        self.assertAlmostEqual(interval_return, 2.01, places=8)

    def test_event_result_label_keeps_neutral_threshold(self) -> None:
        self.assertEqual(
            MODULE.event_result_label("全组合可评估", 0.04),
            ("平", "全组合中性"),
        )
        self.assertEqual(
            MODULE.event_result_label("全组合可评估", 0.25),
            ("胜", "全组合正贡献"),
        )

    def test_atomic_replacement_writes_current_event_id_and_build_status(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            '''
            CREATE TABLE "策略信息" ("统一策略ID" TEXT PRIMARY KEY, "投顾机构" TEXT);
            CREATE TABLE "策略调仓事件" (
                "调仓事件ID" TEXT PRIMARY KEY, "调仓标题" TEXT, "调仓原因" TEXT
            );
            CREATE TABLE "策略调仓明细" (
                "调仓明细ID" TEXT, "调仓事件ID" TEXT, "基金代码" TEXT, "基金名称" TEXT,
                "调前权重_百分比" REAL, "调后权重_百分比" REAL,
                "调仓动作" TEXT, "基金代码匹配状态" TEXT
            );
            CREATE TABLE "策略模拟净值区间" (
                "统一策略ID" TEXT, "区间序号" INTEGER, "算法版本" TEXT, "渠道ID" TEXT,
                "渠道策略ID" TEXT, "策略名称" TEXT, "调仓事件ID" TEXT, "调仓日期" TEXT,
                "下一调仓日期" TEXT, "区间结束日期" TEXT, "区间结束类型" TEXT,
                "区间是否有效" INTEGER, "是否纳入模拟" INTEGER, "质量等级" TEXT,
                "问题说明" TEXT, "修复说明" TEXT, "明细行数" INTEGER, "区间收益率_百分比" REAL
            );
            CREATE TABLE "基金日度净值" (
                "基金代码" TEXT, "交易日期" TEXT, "复权净值" REAL,
                "累计净值" REAL, "单位净值" REAL, "日收益率_百分比" REAL
            );
            INSERT INTO "策略信息" VALUES ('ttfund__S1', '示例机构');
            INSERT INTO "策略调仓事件" VALUES ('event-current', '示例调仓', '示例原因');
            INSERT INTO "策略调仓明细" VALUES
                ('detail-1', 'event-current', '000001', '示例基金', 100, 100, '持平', '原始基金代码');
            INSERT INTO "策略模拟净值区间" VALUES (
                'ttfund__S1', 1, 'standard_rebalance_asset_dual_nav_v10_all_channels_20260528',
                'ttfund', 'S1', '示例策略', 'event-current', '2026-01-01', '2026-01-10',
                '2026-01-09', '下一调仓日前', 1, 1, 'A', '', '', 1, 2.0
            );
            INSERT INTO "基金日度净值" VALUES
                ('000001', '2026-01-01', 100, NULL, NULL, 0),
                ('000001', '2026-01-09', 102, NULL, NULL, 2);
            '''
        )

        payload = MODULE.build_quality_payload(
            conn,
            MODULE.DEFAULT_ALGORITHM_VERSION,
            {"qieman"},
        )
        conn.execute("BEGIN IMMEDIATE")
        MODULE.replace_quality_tables(conn, payload)
        conn.commit()

        self.assertEqual(
            conn.execute('SELECT "调仓事件ID" FROM "调仓质量事件分析"').fetchone()[0],
            "event-current",
        )
        status = conn.execute(
            'SELECT "源事件数", "质量事件数", "缺失事件数", "孤立事件数" '
            'FROM "调仓质量构建状态" WHERE "构建ID"="latest"'
        ).fetchone()
        self.assertEqual(tuple(status), (1, 1, 0, 0))
        conn.close()


if __name__ == "__main__":
    unittest.main()
