from __future__ import annotations

import gzip
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = next(
    parent / "_共享组件" / "生产程序" / "标准化数据稽核.py"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "生产程序" / "标准化数据稽核.py").is_file()
)
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("standard_data_audit_relationship_test", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StrategyRelationshipAuditTests(unittest.TestCase):
    def test_active_alias_requires_page_relation_curve_and_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "analysis.sqlite"
            site = root / "basic_data"
            details = site / "data" / "details"
            details.mkdir(parents=True)
            conn = sqlite3.connect(database)
            try:
                conn.executescript(
                    '''
                    CREATE TABLE "策略信息" ("统一策略ID" TEXT PRIMARY KEY);
                    CREATE TABLE "策略日度业绩" ("统一策略ID" TEXT, "交易日期" TEXT);
                    CREATE TABLE "策略关系" (
                        "子策略ID" TEXT PRIMARY KEY, "母策略ID" TEXT, "官方业绩策略ID" TEXT,
                        "关系状态" TEXT, "置信分" REAL
                    );
                    '''
                )
                conn.executemany('INSERT INTO "策略信息" VALUES (?)', [("child",), ("parent",)])
                conn.executemany('INSERT INTO "策略日度业绩" VALUES (?, ?)', [("parent", "2026-07-28"), ("parent", "2026-07-29")])
                conn.execute('INSERT INTO "策略关系" VALUES (?, ?, ?, ?, ?)', ("child", "parent", "parent", "active", 100.0))
                conn.commit()
            finally:
                conn.close()
            payload = {
                "strategyRelation": {"官方业绩策略ID": "parent"},
                "curves": {"披露业绩": {"points": [{"日期": "2026-07-28", "数值": 1.0}, {"日期": "2026-07-29", "数值": 1.01}]}},
                "curveWarnings": ["本期暂无独立披露净值，披露业绩共享母策略，不代表本期独立成立以来收益。"],
            }
            with gzip.open(details / "child.js.gz", "wt", encoding="utf-8") as handle:
                handle.write("window.__TEST__ = " + json.dumps(payload, ensure_ascii=False) + ";\n")
            issues: list[dict] = []
            MODULE.audit_strategy_parent_child_relationships(issues, database, site)
            self.assertEqual(issues, [])

    def test_high_confidence_review_with_positive_evidence_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "analysis.sqlite"
            site = root / "basic_data"
            (site / "data" / "details").mkdir(parents=True)
            evidence = {
                "curve": {"isAlias": True, "matchRatio": 1.0},
                "holdingMatched": True,
                "rebalanceMatched": True,
            }
            conn = sqlite3.connect(database)
            try:
                conn.executescript(
                    '''
                    CREATE TABLE "策略信息" ("统一策略ID" TEXT PRIMARY KEY);
                    CREATE TABLE "策略日度业绩" ("统一策略ID" TEXT, "交易日期" TEXT);
                    CREATE TABLE "策略关系" (
                        "子策略ID" TEXT PRIMARY KEY, "母策略ID" TEXT, "官方业绩策略ID" TEXT,
                        "关系状态" TEXT, "置信分" REAL, "证据JSON" TEXT, "连续不一致次数" INTEGER
                    );
                    '''
                )
                conn.executemany('INSERT INTO "策略信息" VALUES (?)', [("child",), ("parent",)])
                conn.executemany(
                    'INSERT INTO "策略日度业绩" VALUES (?, ?)',
                    [("parent", "2026-07-28"), ("parent", "2026-07-29")],
                )
                conn.execute(
                    'INSERT INTO "策略关系" VALUES (?, ?, ?, ?, ?, ?, ?)',
                    ("child", "parent", "parent", "review", 100.0, json.dumps(evidence), 6),
                )
                conn.commit()
            finally:
                conn.close()
            issues: list[dict] = []
            MODULE.audit_strategy_parent_child_relationships(issues, database, site)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].get("ruleId"), "STRATEGY_PARENT_CHILD_HIGH_CONFIDENCE_REVIEW_STALE")

    def test_shared_performance_child_cannot_lose_available_parent_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "analysis.sqlite"
            site = root / "basic_data"
            details = site / "data" / "details"
            details.mkdir(parents=True)
            conn = sqlite3.connect(database)
            try:
                conn.executescript(
                    '''
                    CREATE TABLE "策略信息" ("统一策略ID" TEXT PRIMARY KEY);
                    CREATE TABLE "策略日度业绩" ("统一策略ID" TEXT, "交易日期" TEXT);
                    CREATE TABLE "策略关系" (
                        "子策略ID" TEXT PRIMARY KEY, "母策略ID" TEXT, "官方业绩策略ID" TEXT,
                        "关系状态" TEXT, "置信分" REAL
                    );
                    '''
                )
                conn.executemany('INSERT INTO "策略信息" VALUES (?)', [("child",), ("parent",)])
                conn.executemany('INSERT INTO "策略日度业绩" VALUES (?, ?)', [("parent", "2026-07-28"), ("parent", "2026-07-29")])
                conn.execute('INSERT INTO "策略关系" VALUES (?, ?, ?, ?, ?)', ("child", "parent", "parent", "active", 100.0))
                conn.commit()
            finally:
                conn.close()
            detail_payload = {
                "strategyRelation": {"官方业绩策略ID": "parent"},
                "curves": {"披露业绩": {"points": [{"日期": "2026-07-28", "数值": 1.0}, {"日期": "2026-07-29", "数值": 1.01}]}},
                "curveWarnings": ["披露业绩共享母策略，不代表本期独立成立以来收益。"],
            }
            (details / "child.js").write_text(
                "window.__TEST__ = " + json.dumps(detail_payload, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )
            profile = {
                "基准风险资产权重": "",
                "基准风险资产权重说明": "",
                "基准风险资产权重_百分比": None,
                "权益中枢": None,
                "固收中枢": None,
                "基准风险资产中枢": None,
                "海外配置中枢": None,
                "指数化程度": None,
                "主动管理程度": None,
                "风险资产偏离": None,
                "配置风格标签": "",
            }
            core = {
                "strategies": [
                    {
                        **profile,
                        "统一策略ID": "parent",
                        "基准可用状态": "文本+曲线",
                        "业绩基准说明": "母策略官方基准",
                    },
                    {
                        **profile,
                        "统一策略ID": "child",
                        "基准可用状态": "缺失",
                        "业绩基准说明": "",
                        "业绩基准来源策略ID": "child",
                        "业绩基准继承口径": "策略自身披露",
                    },
                ]
            }
            (site / "data" / "basic_summary_core.js").write_text(
                "window.__BASIC_DATA__.summary = " + json.dumps(core, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )

            issues: list[dict] = []
            MODULE.audit_strategy_parent_child_relationships(issues, database, site)
            self.assertEqual([issue.get("ruleId") for issue in issues], ["PAGE_STRATEGY_SHARED_BENCHMARK_MISSING"])

    def test_target_profit_institution_alias_split_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "analysis.sqlite"
            site = root / "basic_data"
            (site / "data").mkdir(parents=True)
            sqlite3.connect(database).close()
            pack = {
                "periods": [
                    {
                        "统一策略ID": "gffunds__1",
                        "策略名称": "幸福小心愿目标盈第1期",
                        "投顾机构": "广发基金投顾",
                        "系列名称": "幸福小心愿目标盈",
                        "系列ID": "target_raw_alias_1",
                    },
                    {
                        "统一策略ID": "gffunds__2",
                        "策略名称": "幸福小心愿目标盈第2期",
                        "投顾机构": "广发基金有限公司",
                        "系列名称": "幸福小心愿目标盈",
                        "系列ID": "target_raw_alias_2",
                    },
                ]
            }
            (site / "data" / "target_profit_analysis_pack.js").write_text(
                "window.__BASIC_TARGET_PROFIT_ANALYSIS_PACK__ = " + json.dumps(pack, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )

            issues: list[dict] = []
            MODULE.audit_target_profit_page_consistency(issues, database, site)

            self.assertEqual(
                {issue.get("ruleId") for issue in issues},
                {"PAGE_BUSINESS_NAMING_NOT_CANONICAL", "TARGET_PROFIT_SERIES_SPLIT_BY_INSTITUTION_ALIAS"},
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
