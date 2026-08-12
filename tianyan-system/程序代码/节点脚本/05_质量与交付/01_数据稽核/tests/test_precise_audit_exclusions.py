from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


SCRIPT_PATH = next(
    parent / "_共享组件" / "生产程序" / "标准化数据稽核.py"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "生产程序" / "标准化数据稽核.py").is_file()
)
SPEC = importlib.util.spec_from_file_location("standard_data_audit_precise", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PreciseAuditExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_catalog = MODULE.RULE_CATALOG
        MODULE.RULE_CATALOG = {
            "TTFUND_STRATEGY_BENCHMARK_CURVE_STALE": {
                "maxSourceLagBusinessDays": 1,
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
                "修复责任节点": "index_benchmark",
            },
            "TTFUND_STRATEGY_BENCHMARK_CURVE_SOURCE_LAG": {
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
                "修复责任节点": "index_benchmark",
            },
            "TTFUND_INCOMPLETE_STRATEGY_BENCHMARK_MISSING": {
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
                "修复责任节点": "index_benchmark",
            },
        }

    def tearDown(self) -> None:
        MODULE.RULE_CATALOG = self.original_catalog

    def test_one_business_day_benchmark_source_lag_warns_but_long_lag_blocks(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                '''
                CREATE TABLE "策略信息" (
                    "统一策略ID" TEXT PRIMARY KEY,
                    "渠道策略ID" TEXT,
                    "策略名称" TEXT,
                    "渠道ID" TEXT
                );
                CREATE TABLE "策略日度业绩" (
                    "统一策略ID" TEXT,
                    "交易日期" TEXT,
                    "基准收益率_百分比" REAL
                );
                INSERT INTO "策略信息" VALUES
                    ('ttfund__S1', 'S1', '允许源延迟', 'ttfund'),
                    ('ttfund__S2', 'S2', '超期源延迟', 'ttfund');
                INSERT INTO "策略日度业绩" VALUES
                    ('ttfund__S1', '2026-07-22', 0.1),
                    ('ttfund__S1', '2026-07-23', NULL),
                    ('ttfund__S2', '2026-07-17', 0.2),
                    ('ttfund__S2', '2026-07-23', NULL);
                '''
            )
            issues: list[dict] = []
            MODULE.audit_ttfund_strategy_benchmark_curve_freshness(issues, conn)

        self.assertEqual(MODULE.business_day_lag("2026-07-17", "2026-07-20"), 1)
        self.assertEqual({issue["severity"] for issue in issues}, {"error", "warn"})
        error = next(issue for issue in issues if issue["severity"] == "error")
        warning = next(issue for issue in issues if issue["severity"] == "warn")
        self.assertEqual(error["sample"][0]["统一策略ID"], "ttfund__S2")
        self.assertEqual(warning["sample"][0]["统一策略ID"], "ttfund__S1")

    def test_single_point_new_strategy_without_benchmark_warns_but_does_not_block(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                '''
                CREATE TABLE "策略信息" (
                    "统一策略ID" TEXT PRIMARY KEY,
                    "渠道策略ID" TEXT,
                    "策略名称" TEXT,
                    "渠道ID" TEXT
                );
                CREATE TABLE "策略日度业绩" (
                    "统一策略ID" TEXT,
                    "交易日期" TEXT,
                    "基准收益率_百分比" REAL
                );
                INSERT INTO "策略信息" VALUES ('ttfund__NEW', 'NEW', '新策略', 'ttfund');
                INSERT INTO "策略日度业绩" VALUES ('ttfund__NEW', '2026-08-07', NULL);
                '''
            )
            issues: list[dict] = []
            MODULE.audit_ttfund_strategy_benchmark_curve_freshness(issues, conn)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "warn")
        self.assertEqual(issues[0]["ruleId"], "TTFUND_INCOMPLETE_STRATEGY_BENCHMARK_MISSING")
        self.assertEqual(issues[0]["sample"][0]["策略曲线点数"], 1)

    def test_declared_nonrankable_strategy_is_not_treated_as_ranking_omission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_root = Path(temporary)
            site_dir = report_root / "basic_data"
            data_dir = site_dir / "data"
            data_dir.mkdir(parents=True)
            summary = {
                "strategies": [
                    {
                        "统一策略ID": "strategy_A",
                        "数据完整性": "完整",
                        "风险等级": "R2",
                        "研报产品类型": "普通策略",
                        "是否纳入常规排名": 1,
                    },
                    {
                        "统一策略ID": "strategy_B",
                        "数据完整性": "完整",
                        "风险等级": "R3",
                        "研报产品类型": "普通策略",
                        "是否广发策略": "是",
                        "是否纳入常规排名": 1,
                    },
                    {
                        "统一策略ID": "strategy_C",
                        "数据完整性": "完整",
                        "风险等级": "R3",
                        "研报产品类型": "普通策略",
                        "是否广发策略": "是",
                        "是否纳入常规排名": 0,
                    },
                ]
            }
            (data_dir / "basic_summary_core.js").write_text(
                "window.BASIC_SUMMARY_CORE = "
                + json.dumps(summary, ensure_ascii=False)
                + ";\n",
                encoding="utf-8",
            )
            source_path = report_root / "reports" / "mixed" / "workbook_source.json"
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                json.dumps(
                    {
                        "meta": {
                            "strategyListVisibleRowCount": 3,
                            "strategyListRankingSourceCoveredCount": 2,
                            "strategyListNonrankableRowCount": 1,
                            "strategyListMissingEligibleRowCount": 0,
                        },
                        "excludedStrategyRows": [
                            {
                                "产品代码": "strategy_C",
                                "剔除原因": "策略列表可见但不具备混排资格；无官方披露业绩",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (data_dir / "mixed_performance_scatter_pack.json").write_text(
                json.dumps(
                    {
                        "meta": {
                            "sourceWorkbookPack": str(source_path),
                            "intervals": [],
                            "intervalAsOfDates": {},
                        },
                        "rows": [
                            {
                                "id": "strategy_A",
                                "name": "A",
                                "productType": "投顾策略",
                                "detailUrl": "strategy.html?id=strategy_A",
                                "isGuangfa": False,
                            },
                            {
                                "id": "strategy_B",
                                "name": "B",
                                "productType": "投顾策略",
                                "detailUrl": "strategy.html?id=strategy_B",
                                "isGuangfa": True,
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            database = report_root / "analysis.sqlite"
            sqlite3.connect(database).close()
            issues: list[dict] = []
            MODULE.audit_mixed_performance_pack(issues, site_dir, database)

        self.assertEqual(issues, [])

    def test_nonrankable_strategy_present_in_mixed_pack_is_blocking_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_root = Path(temporary)
            site_dir = report_root / "basic_data"
            data_dir = site_dir / "data"
            data_dir.mkdir(parents=True)
            summary = {
                "strategies": [
                    {
                        "统一策略ID": "strategy_ACTIVE",
                        "策略名称": "正常策略",
                        "数据完整性": "完整",
                        "风险等级": "R2",
                        "研报产品类型": "普通策略",
                        "是否纳入常规排名": 1,
                    },
                    {
                        "统一策略ID": "strategy_STOPPED",
                        "策略名称": "已下架策略",
                        "数据完整性": "完整",
                        "风险等级": "R2",
                        "研报产品类型": "普通策略",
                        "策略治理状态": "已停止策略",
                        "是否已停止": 1,
                        "是否纳入常规排名": 0,
                    },
                ]
            }
            (data_dir / "basic_summary_core.js").write_text(
                "window.BASIC_SUMMARY_CORE = " + json.dumps(summary, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )
            source_path = report_root / "reports" / "mixed" / "workbook_source.json"
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                json.dumps(
                    {
                        "meta": {
                            "strategyListVisibleRowCount": 2,
                            "strategyListRankingSourceCoveredCount": 1,
                            "strategyListNonrankableRowCount": 1,
                            "strategyListMissingEligibleRowCount": 0,
                        },
                        "excludedStrategyRows": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (data_dir / "mixed_performance_scatter_pack.json").write_text(
                json.dumps(
                    {
                        "meta": {
                            "sourceWorkbookPack": str(source_path),
                            "intervals": [],
                            "intervalAsOfDates": {},
                        },
                        "rows": [
                            {
                                "id": "strategy_ACTIVE",
                                "name": "正常策略",
                                "productType": "投顾策略",
                                "detailUrl": "strategy.html?id=strategy_ACTIVE",
                                "isGuangfa": False,
                            },
                            {
                                "id": "strategy_STOPPED",
                                "name": "已下架策略",
                                "productType": "投顾策略",
                                "detailUrl": "strategy.html?id=strategy_STOPPED",
                                "isGuangfa": False,
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            database = report_root / "analysis.sqlite"
            sqlite3.connect(database).close()
            issues: list[dict] = []
            MODULE.audit_mixed_performance_pack(issues, site_dir, database)

        inactive_issue = next(issue for issue in issues if issue["ruleId"] == "RANKING_INCLUDES_INACTIVE_STRATEGY")
        self.assertEqual(inactive_issue["severity"], "error")
        self.assertEqual(inactive_issue["sample"][0]["统一策略ID"], "strategy_STOPPED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
