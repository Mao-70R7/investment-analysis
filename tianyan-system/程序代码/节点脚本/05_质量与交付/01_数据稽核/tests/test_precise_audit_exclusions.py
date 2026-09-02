from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
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
sys.path.insert(0, str(SCRIPT_PATH.parent))
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
            "TTFUND_PUBLIC_QUOTE_ADVANCES_OFFICIAL_DATE": {
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
                "修复责任节点": "strategy_governance",
            },
            "QIEMAN_EXACT_BENCHMARK_COMPONENT_INVALID": {
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
                "修复责任节点": "qieman_load",
            },
            "QIEMAN_EXACT_BENCHMARK_CURVE_CHECKPOINT_MISMATCH": {
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
                "修复责任节点": "report_build",
            },
            "QIEMAN_OFFICIAL_REBALANCE_CONTRIBUTION_CURVE_MISSING": {
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
                "修复责任节点": "report_build",
            },
        }

    def tearDown(self) -> None:
        MODULE.RULE_CATALOG = self.original_catalog

    def test_fund_detail_coverage_applies_only_to_page_sets_that_publish_fund_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_root = Path(temporary)
            site_dir = report_root / "basic_data"
            site_dir.mkdir()

            self.assertTrue(MODULE.fund_detail_pages_required(site_dir))

            (report_root / "deployment_manifest.json").write_text(
                json.dumps({"pageSet": "minimal_publish"}),
                encoding="utf-8",
            )
            self.assertFalse(MODULE.fund_detail_pages_required(site_dir))

            (report_root / "deployment_manifest.json").write_text(
                json.dumps({"pageSet": "all"}),
                encoding="utf-8",
            )
            self.assertTrue(MODULE.fund_detail_pages_required(site_dir))

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

    def test_public_quote_cannot_advance_ttfund_official_performance_date(self) -> None:
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
                    "渠道ID" TEXT,
                    "交易日期" TEXT,
                    "业绩区段类型" TEXT
                );
                CREATE TABLE "策略产品披露净值" (
                    "统一策略ID" TEXT,
                    "渠道ID" TEXT,
                    "交易日期" TEXT,
                    "业绩区段类型" TEXT
                );
                CREATE TABLE "策略治理标签" (
                    "统一策略ID" TEXT,
                    "官方最新业绩日期" TEXT
                );
                INSERT INTO "策略信息" VALUES ('ttfund__S1', 'S1', '示例策略', 'ttfund');
                INSERT INTO "策略日度业绩" VALUES ('ttfund__S1', 'ttfund', '2026-08-12', 'public_quote');
                INSERT INTO "策略产品披露净值" VALUES ('ttfund__S1', 'ttfund', '2026-08-11', 'official_app_curve');
                INSERT INTO "策略治理标签" VALUES ('ttfund__S1', '2026-08-12');
                '''
            )
            issues: list[dict] = []
            MODULE.audit_ttfund_official_performance_date_lineage(issues, conn)
            self.assertEqual([issue["ruleId"] for issue in issues], ["TTFUND_PUBLIC_QUOTE_ADVANCES_OFFICIAL_DATE"])

            conn.execute(
                '''UPDATE "策略治理标签" SET "官方最新业绩日期"='2026-08-11' WHERE "统一策略ID"='ttfund__S1' '''
            )
            issues = []
            MODULE.audit_ttfund_official_performance_date_lineage(issues, conn)
            self.assertEqual(issues, [])

    def test_qieman_exact_benchmark_components_must_close_to_one_hundred_percent(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                '''
                CREATE TABLE "策略业绩基准成分" (
                    "统一策略ID" TEXT,
                    "渠道ID" TEXT,
                    "渠道策略ID" TEXT,
                    "指数代码" TEXT,
                    "权重_百分比" REAL,
                    "是否精确拆分" INTEGER
                );
                INSERT INTO "策略业绩基准成分" VALUES
                    ('qieman__ZH044931', 'qieman', 'ZH044931', '000300.SH', 10.0, 1),
                    ('qieman__ZH044931', 'qieman', 'ZH044931', 'CBA00103.CS', 89.0, 1);
                '''
            )
            issues: list[dict] = []
            MODULE.audit_qieman_structured_benchmark_components(issues, conn)
        self.assertEqual([issue["ruleId"] for issue in issues], ["QIEMAN_EXACT_BENCHMARK_COMPONENT_INVALID"])

    def test_qieman_benchmark_checkpoint_and_contribution_curve_regressions(self) -> None:
        rules = {
            "strategyBenchmarkCurveCheckpoints": [
                {
                    "strategyId": "qieman__ZH044931",
                    "curveName": "基准业绩",
                    "date": "2025-11-27",
                    "expectedCumulativeReturnPct": 8.65,
                    "tolerancePctPoints": 0.015,
                    "requiredComponentCodes": ["000300.SH", "CBA00103.CS"],
                    "requiredMethodContains": "逐日再平衡",
                }
            ],
            "strategyContributionCurveRequirements": [
                {
                    "strategyId": "qieman__ZH044931",
                    "minEventCount": 1,
                    "minPointsPerSide": 2,
                    "requiredEvaluationStatus": "官方权重净值回放",
                }
            ],
        }
        valid_detail = {
            "benchmarkMeta": {
                "可计算组件": [
                    {"指数代码": "000300.SH", "权重": 10.0},
                    {"指数代码": "CBA00103.CS", "权重": 90.0},
                ],
                "组合计算方法": "指数日收益按披露权重逐日再平衡复合",
            },
            "curves": {
                "基准业绩": {
                    "模式": "nav",
                    "points": [
                        {"日期": "2020-06-12", "数值": 1.0},
                        {"日期": "2025-11-27", "数值": 1.0865},
                    ],
                }
            },
            "contributionCurves": {
                "event-1": {
                    "评估状态": "官方权重净值回放",
                    "series": {
                        "调仓前仓位模拟": {"points": [{"日期": "2026-01-01", "数值": 0}, {"日期": "2026-01-02", "数值": 0.1}]},
                        "调仓后仓位实际": {"points": [{"日期": "2026-01-01", "数值": 0}, {"日期": "2026-01-02", "数值": 0.2}]},
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            site_dir = Path(temporary) / "basic_data"
            detail_dir = site_dir / "data" / "details"
            detail_dir.mkdir(parents=True)
            detail_path = detail_dir / "qieman__ZH044931.js"

            detail_path.write_text(
                "window.STRATEGY_DETAIL = " + json.dumps(valid_detail, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )
            issues: list[dict] = []
            MODULE.audit_strategy_detail_regression_rules(issues, site_dir, rules)
            self.assertEqual(issues, [])

            invalid_detail = json.loads(json.dumps(valid_detail, ensure_ascii=False))
            invalid_detail["benchmarkMeta"]["可计算组件"][1]["指数代码"] = "CBA00603.CS"
            invalid_detail["curves"]["基准业绩"]["points"][1]["数值"] = 1.0857
            invalid_detail["contributionCurves"] = {}
            detail_path.write_text(
                "window.STRATEGY_DETAIL = " + json.dumps(invalid_detail, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )
            issues = []
            MODULE.audit_strategy_detail_regression_rules(issues, site_dir, rules)

        self.assertEqual(
            {issue["ruleId"] for issue in issues},
            {
                "QIEMAN_EXACT_BENCHMARK_CURVE_CHECKPOINT_MISMATCH",
                "QIEMAN_OFFICIAL_REBALANCE_CONTRIBUTION_CURVE_MISSING",
            },
        )

    def test_declared_nonrankable_strategy_is_not_treated_as_ranking_omission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_root = Path(temporary)
            site_dir = report_root / "basic_data"
            data_dir = site_dir / "data"
            data_dir.mkdir(parents=True)
            asset_dir = site_dir / "assets"
            asset_dir.mkdir()
            (asset_dir / "institutions.js").write_text(
                '\n'.join(
                    [
                        'withGlobalStrategyFilters("./mixed-performance-scatter.html"',
                        'productType: "投顾策略"',
                        'riskWeight: bucket',
                        'channel: scope.channel',
                        'institution: scope.institution',
                    ]
                ),
                encoding="utf-8",
            )
            (asset_dir / "mixed-performance-scatter.js").write_text(
                '\n'.join(
                    [
                        'initialParams.get("channel")',
                        'initialParams.get("institution")',
                        'initialParams.get("riskWeight")',
                        'id="mixedChannel"',
                        'id="mixedInstitution"',
                    ]
                ),
                encoding="utf-8",
            )
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
