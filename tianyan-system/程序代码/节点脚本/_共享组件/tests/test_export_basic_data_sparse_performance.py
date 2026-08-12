from __future__ import annotations

import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "生产程序" / "export_basic_data_pages.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("export_basic_data_pages", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SparsePerformanceDisplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            '''
            CREATE TABLE "策略产品披露净值" (
                "统一策略ID" TEXT, "交易日期" TEXT, "披露单位净值" REAL,
                "披露累计收益率_百分比" REAL, "是否可画曲线" INTEGER
            );
            CREATE TABLE "策略日度业绩" (
                "统一策略ID" TEXT, "交易日期" TEXT, "单位净值" REAL,
                "累计收益率_百分比" REAL
            );
            CREATE TABLE "策略区间业绩" (
                "统一策略ID" TEXT, "统计日期" TEXT, "区间代码" TEXT,
                "策略收益率_百分比" REAL, "基准收益率_百分比" REAL
            );
            CREATE TABLE "策略披露风险指标" (
                "统一策略ID" TEXT, "统计日期" TEXT, "区间代码" TEXT,
                "官方最大回撤_百分比" REAL, "官方波动率_百分比" REAL,
                "官方夏普" REAL, "数据来源字段" TEXT
            );
            INSERT INTO "策略日度业绩" VALUES ('gfbank_cgb__demo', '2026-07-27', 1.2896, 28.96);
            '''
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_single_point_keeps_cumulative_but_not_ytd_or_drawdown(self) -> None:
        returns = MODULE.build_disclosed_return_map(self.conn)["gfbank_cgb__demo"]
        self.assertEqual(returns["累计收益率"], 28.96)
        self.assertIsNone(returns["今年以来"])
        self.assertEqual(returns["业绩点数"], 1)
        self.assertNotIn("gfbank_cgb__demo", MODULE.build_disclosed_risk_map(self.conn))

    def test_gfsec_public_delete_status_is_displayed_as_stopped(self) -> None:
        self.assertEqual(MODULE.operation_status("delete"), "已下架")
        self.assertEqual(MODULE.operation_status("normal"), "正常运作")

    def test_gfsec_robot_is_archived_instead_of_current_client_shelf(self) -> None:
        fields = MODULE.display_status_fields(
            "gfsec_robot",
            "广发证券易淘金/贝塔牛理财",
            "正常运作",
        )

        self.assertEqual(fields["天天当前对客展示"], "否")
        self.assertIn("历史接口留档", fields["天天展示状态"])
        self.assertIn("gfsec_fima", fields["天天展示判定依据"])

    def test_official_annualized_return_is_available_to_strategy_summary(self) -> None:
        self.conn.execute(
            '''
            INSERT INTO "策略区间业绩"
            VALUES ('gfbank_cgb__demo', '2026-07-27', 'annualized', 6.25, NULL)
            '''
        )

        returns = MODULE.build_disclosed_return_map(self.conn)["gfbank_cgb__demo"]

        self.assertEqual(returns["年化收益"], 6.25)

    def test_interval_only_strategy_keeps_official_returns_without_faking_nav(self) -> None:
        self.conn.executemany(
            '''
            INSERT INTO "策略区间业绩"
            VALUES ('gfsec_robot__biotech.theme', '2023-08-01', ?, ?, NULL)
            ''',
            [
                ("1m", -3.8581),
                ("1y", -18.9694),
                ("since_inception", 103.5976),
            ],
        )

        returns = MODULE.build_disclosed_return_map(self.conn)["gfsec_robot__biotech.theme"]
        latest = MODULE.build_disclosed_latest_value_map(self.conn)["gfsec_robot__biotech.theme"]

        self.assertEqual(returns["近一月"], -3.8581)
        self.assertEqual(returns["近1年"], -18.9694)
        self.assertEqual(returns["累计收益率"], 103.5976)
        self.assertEqual(returns["收益数据截至"], "2023-08-01")
        self.assertEqual(returns["业绩点数"], 0)
        self.assertIsNone(latest["官方单位净值"])
        self.assertEqual(latest["官方累计收益"], 103.5976)

    def test_interval_only_strategy_accepts_gfsec_std_as_official_cumulative(self) -> None:
        self.conn.executemany(
            '''
            INSERT INTO "策略区间业绩"
            VALUES ('gfsec_robot__biotech.theme', '2023-08-01', ?, ?, NULL)
            ''',
            [
                ("1m", -3.8581),
                ("std", 103.5976),
            ],
        )

        returns = MODULE.build_disclosed_return_map(self.conn)["gfsec_robot__biotech.theme"]
        latest = MODULE.build_disclosed_latest_value_map(self.conn)["gfsec_robot__biotech.theme"]

        self.assertEqual(returns["近一月"], -3.8581)
        self.assertEqual(returns["累计收益率"], 103.5976)
        self.assertEqual(latest["官方累计收益"], 103.5976)

    def test_official_risk_is_fallback_when_curve_is_unavailable(self) -> None:
        self.conn.execute(
            '''
            INSERT INTO "策略披露风险指标"
            VALUES (
                'gfsec_robot__allocation.risk3p12', '2023-08-01', 'std',
                26.8259, 7.6619, 0.32879, 'strategy_master.extra.performance.yield'
            )
            '''
        )

        risk = MODULE.build_disclosed_risk_map(self.conn)["gfsec_robot__allocation.risk3p12"]

        self.assertEqual(risk["最大回撤"], 26.8259)
        self.assertEqual(risk["波动率"], 7.6619)
        self.assertEqual(risk["夏普比率"], 0.3288)
        self.assertEqual(risk["风险数据截至"], "2023-08-01")
        self.assertEqual(risk["风险来源"], "渠道官方披露风险指标")

    def test_interval_matrix_keeps_official_six_month_and_benchmark_returns(self) -> None:
        self.conn.executemany(
            '''
            INSERT INTO "策略区间业绩"
            VALUES ('gfbank_cgb__demo', '2026-07-29', ?, ?, ?)
            ''',
            [
                ("1m", 0.15, 0.22),
                ("6m", 0.33, 1.41),
                ("1y", 1.58, 3.57),
                ("since_inception", 16.93, 20.25),
            ],
        )
        official = MODULE.build_official_interval_return_map(self.conn)["gfbank_cgb__demo"]
        disclosed = MODULE.official_interval_fields(
            official,
            MODULE.OFFICIAL_INTERVAL_MATRIX_FIELD_BY_CODE,
        )
        benchmark = MODULE.official_interval_fields(
            official,
            MODULE.OFFICIAL_INTERVAL_MATRIX_FIELD_BY_CODE,
            value_field="基准收益率_百分比",
        )

        matrix = MODULE.build_interval_matrix(
            {
                "披露业绩": [{"日期": "2026-07-29", "数值": 1.085, "模式": "nav"}],
                "沪深300业绩": [{"日期": "2026-07-29", "数值": 4600.26, "模式": "nav"}],
            },
            disclosed,
            benchmark,
        )
        by_name = {row["口径"]: row for row in matrix}

        self.assertEqual(by_name["披露业绩"]["近6月"], 0.33)
        self.assertIsNone(by_name["披露业绩"]["今年以来"])
        self.assertEqual(by_name["基准业绩"]["近一月"], 0.22)
        self.assertEqual(by_name["基准业绩"]["近6月"], 1.41)
        self.assertEqual(by_name["基准业绩"]["近1年"], 3.57)
        self.assertEqual(by_name["基准业绩"]["成立以来"], 20.25)
        self.assertIsNone(by_name["沪深300业绩"]["今年以来"])

    def test_strategy_detail_template_never_uses_app_screenshot_as_curve(self) -> None:
        template = (
            Path(__file__).resolve().parents[3]
            / "basic_data"
            / "assets"
            / "strategy-detail.js"
        ).read_text(encoding="utf-8")

        self.assertNotIn("officialPerformanceImage", template)
        self.assertNotIn("official-performance-image", template)
        self.assertIn("暂无真实业绩走势图", template)
        self.assertIn("不使用截图或图像反推点替代", template)
        self.assertIn("isLegacyArchive", template)
        self.assertIn("历史接口留档", template)
        self.assertIn('benchmarkPoints.length >= 2 ? ["基准业绩"] : []', template)


if __name__ == "__main__":
    unittest.main()
