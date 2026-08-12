from __future__ import annotations

import importlib.util
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
SPEC = importlib.util.spec_from_file_location("standard_data_audit", SCRIPT_PATH)
assert SPEC and SPEC.loader
sys.path.insert(0, str(SCRIPT_PATH.parent))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GFFundsChannelCoverageAuditTests(unittest.TestCase):
    def test_strategy_level_coverage_detects_sparse_non_empty_fact_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "analysis.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    '''
                    CREATE TABLE "策略信息" (
                        "统一策略ID" TEXT PRIMARY KEY,
                        "渠道ID" TEXT NOT NULL
                    );
                    CREATE TABLE "策略日度业绩" (
                        "统一策略ID" TEXT NOT NULL
                    );
                    CREATE TABLE "策略当前持仓" (
                        "统一策略ID" TEXT NOT NULL
                    );
                    '''
                )
                conn.executemany(
                    'INSERT INTO "策略信息" VALUES (?, "gffunds")',
                    [(f"gffunds:S{index:03d}",) for index in range(100)],
                )
                conn.executemany(
                    'INSERT INTO "策略日度业绩" VALUES (?)',
                    [(f"gffunds:S{index:03d}",) for index in range(96)],
                )
                conn.executemany(
                    'INSERT INTO "策略当前持仓" VALUES (?)',
                    [(f"gffunds:S{index:03d}",) for index in range(94)],
                )
                issues: list[dict] = []
                MODULE.RULE_CATALOG = {
                    "GFFUNDS_CORE_STRATEGY_COVERAGE_BELOW_THRESHOLD": {
                        "severity": "error",
                        "原因说明": "fixture",
                        "优化建议": "fixture",
                        "修复责任脚本": "fixture",
                        "修复责任节点": "gffunds_gate",
                    }
                }
                MODULE.audit_channel_strategy_coverage_rules(
                    issues,
                    conn,
                    {"策略信息", "策略日度业绩", "策略当前持仓"},
                    {
                        "channelStrategyCoverage": [
                            {
                                "channelId": "gffunds",
                                "metrics": [
                                    {
                                        "name": "日度业绩策略覆盖",
                                        "table": "策略日度业绩",
                                        "minimumRate": 0.95,
                                    },
                                    {
                                        "name": "当前基金持仓策略覆盖",
                                        "table": "策略当前持仓",
                                        "minimumRate": 0.95,
                                    },
                                ],
                            }
                        ]
                    },
                )

            self.assertEqual(len(issues), 1)
            self.assertEqual(
                issues[0]["ruleId"],
                "GFFUNDS_CORE_STRATEGY_COVERAGE_BELOW_THRESHOLD",
            )
            self.assertEqual(issues[0]["sample"][0]["覆盖率"], 0.94)

    def test_performance_coverage_excludes_strategies_without_official_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "analysis.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    '''
                    CREATE TABLE "策略信息" (
                        "统一策略ID" TEXT PRIMARY KEY,
                        "渠道ID" TEXT NOT NULL
                    );
                    CREATE TABLE "策略治理标签" (
                        "统一策略ID" TEXT PRIMARY KEY,
                        "是否缺官方业绩" INTEGER NOT NULL
                    );
                    CREATE TABLE "策略日度业绩" (
                        "统一策略ID" TEXT NOT NULL
                    );
                    CREATE TABLE "策略当前持仓" (
                        "统一策略ID" TEXT NOT NULL
                    );
                    '''
                )
                strategy_ids = [f"gffunds:S{index:03d}" for index in range(100)]
                conn.executemany(
                    'INSERT INTO "策略信息" VALUES (?, "gffunds")',
                    [(strategy_id,) for strategy_id in strategy_ids],
                )
                conn.executemany(
                    'INSERT INTO "策略治理标签" VALUES (?, ?)',
                    [
                        (strategy_id, 0 if index < 60 else 1)
                        for index, strategy_id in enumerate(strategy_ids)
                    ],
                )
                conn.executemany(
                    'INSERT INTO "策略日度业绩" VALUES (?)',
                    [(strategy_id,) for strategy_id in strategy_ids[:57]],
                )
                conn.executemany(
                    'INSERT INTO "策略当前持仓" VALUES (?)',
                    [(strategy_id,) for strategy_id in strategy_ids],
                )
                issues: list[dict] = []
                MODULE.audit_channel_strategy_coverage_rules(
                    issues,
                    conn,
                    {"策略信息", "策略治理标签", "策略日度业绩", "策略当前持仓"},
                    {
                        "channelStrategyCoverage": [
                            {
                                "channelId": "gffunds",
                                "metrics": [
                                    {
                                        "name": "日度业绩策略覆盖",
                                        "table": "策略日度业绩",
                                        "minimumRate": 0.95,
                                        "denominatorEligibility": {
                                            "table": "策略治理标签",
                                            "conditions": {"是否缺官方业绩": 0},
                                            "说明": "仅官方已披露业绩策略",
                                        },
                                    },
                                    {
                                        "name": "当前基金持仓策略覆盖",
                                        "table": "策略当前持仓",
                                        "minimumRate": 0.95,
                                    },
                                ],
                            }
                        ]
                    },
                )

            self.assertEqual(issues, [])

    def test_holding_coverage_excludes_login_only_profit_issues(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "analysis.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    '''
                    CREATE TABLE "策略信息" (
                        "统一策略ID" TEXT PRIMARY KEY,
                        "渠道ID" TEXT NOT NULL
                    );
                    CREATE TABLE "策略治理标签" (
                        "统一策略ID" TEXT PRIMARY KEY,
                        "是否目标盈期次" INTEGER NOT NULL
                    );
                    CREATE TABLE "策略当前持仓" (
                        "统一策略ID" TEXT NOT NULL
                    );
                    '''
                )
                core_ids = [f"gffunds:GFJJ{index:06d}" for index in range(66)]
                profit_ids = [f"gffunds:ZY{index:08d}" for index in range(75)]
                conn.executemany(
                    'INSERT INTO "策略信息" VALUES (?, "gffunds")',
                    [(strategy_id,) for strategy_id in [*core_ids, *profit_ids]],
                )
                conn.executemany(
                    'INSERT INTO "策略治理标签" VALUES (?, ?)',
                    [(strategy_id, 0) for strategy_id in core_ids]
                    + [(strategy_id, 1) for strategy_id in profit_ids],
                )
                conn.executemany(
                    'INSERT INTO "策略当前持仓" VALUES (?)',
                    [(strategy_id,) for strategy_id in core_ids],
                )
                issues: list[dict] = []
                MODULE.audit_channel_strategy_coverage_rules(
                    issues,
                    conn,
                    {"策略信息", "策略治理标签", "策略当前持仓"},
                    {
                        "channelStrategyCoverage": [
                            {
                                "channelId": "gffunds",
                                "metrics": [
                                    {
                                        "name": "当前基金持仓策略覆盖",
                                        "table": "策略当前持仓",
                                        "minimumRate": 0.95,
                                        "denominatorEligibility": {
                                            "table": "策略治理标签",
                                            "conditions": {"是否目标盈期次": 0},
                                            "说明": "仅普通 GFJJ 核心策略",
                                        },
                                    }
                                ],
                            }
                        ]
                    },
                )

            self.assertEqual(issues, [])


class ChannelPerformanceFreshnessAuditTests(unittest.TestCase):
    @staticmethod
    def run_audit(qieman_rows: list[tuple[str, str]]) -> list[dict]:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "analysis.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    '''
                    CREATE TABLE "策略日度业绩" (
                        "渠道ID" TEXT NOT NULL,
                        "渠道策略ID" TEXT NOT NULL,
                        "交易日期" TEXT NOT NULL
                    );
                    '''
                )
                conn.executemany(
                    'INSERT INTO "策略日度业绩" VALUES ("qieman", ?, ?)',
                    qieman_rows,
                )
                conn.execute(
                    'INSERT INTO "策略日度业绩" VALUES ("ttfund", "T001", "2026-08-11")'
                )
                issues: list[dict] = []
                MODULE.RULE_CATALOG = {
                    "QIEMAN_LATEST_NAV_DATE_COVERAGE_LOW": {
                        "severity": "error",
                        "原因说明": "fixture",
                        "优化建议": "fixture",
                        "修复责任脚本": "fixture",
                        "修复责任节点": "data_audit",
                    }
                }
                MODULE.audit_channel_performance_freshness_rules(
                    issues,
                    conn,
                    {"策略日度业绩"},
                    {
                        "pageStrategyScope": {
                            "activeChannelIds": ["ttfund", "qieman"]
                        },
                        "channelPerformanceFreshness": [
                            {
                                "channelId": "qieman",
                                "ruleId": "QIEMAN_LATEST_NAV_DATE_COVERAGE_LOW",
                                "table": "策略日度业绩",
                                "minimumLatestDateRate": 0.99,
                                "maximumBusinessDayLagFromSystemLatest": 1,
                            }
                        ],
                    },
                )
                return issues

    def test_stale_channel_watermark_is_an_error(self) -> None:
        issues = self.run_audit(
            [(f"Q{index:03d}", "2026-08-07") for index in range(100)]
        )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["ruleId"], "QIEMAN_LATEST_NAV_DATE_COVERAGE_LOW")
        self.assertEqual(issues[0]["sample"][0]["相差工作日"], 2)

    def test_latest_date_strategy_coverage_requires_ninety_nine_percent(self) -> None:
        failed = self.run_audit(
            [(f"Q{index:03d}", "2026-08-11" if index < 98 else "2026-08-07") for index in range(100)]
        )
        passed = self.run_audit(
            [(f"Q{index:03d}", "2026-08-11" if index < 99 else "2026-08-07") for index in range(100)]
        )
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["sample"][0]["最新日覆盖率"], 0.98)
        self.assertEqual(passed, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
