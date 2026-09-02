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
SPEC = importlib.util.spec_from_file_location("standard_data_audit_rebalance_quality", SCRIPT_PATH)
assert SPEC and SPEC.loader
sys.path.insert(0, str(SCRIPT_PATH.parent))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


ALGORITHM = "standard_rebalance_asset_dual_nav_v10_all_channels_20260528"
RULES = {
    "rebalanceQualityFreshnessCheck": {
        "enabled": True,
        "ruleId": "REBALANCE_QUALITY_FACTS_STALE",
        "algorithmVersion": ALGORITHM,
        "excludedChannelIds": ["qieman"],
    },
    "rebalanceContributionCurveCoverageCheck": {
        "enabled": True,
        "ruleId": "REBALANCE_CONTRIBUTION_CURVE_MISSING",
        "excludedChannelIds": ["qieman"],
        "requiredEvaluationStatus": "可评估",
        "minPointsPerSide": 2,
    },
}


def create_fixture(path: Path, quality_event_id: str = "event-new", with_status: bool = True) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            '''
            CREATE TABLE "策略调仓事件" ("调仓事件ID" TEXT PRIMARY KEY);
            CREATE TABLE "策略模拟净值区间" (
                "调仓事件ID" TEXT, "统一策略ID" TEXT, "渠道ID" TEXT,
                "调仓日期" TEXT, "算法版本" TEXT
            );
            CREATE TABLE "调仓质量事件分析" (
                "调仓事件ID" TEXT PRIMARY KEY, "统一策略ID" TEXT,
                "策略名称" TEXT, "渠道ID" TEXT, "调仓日期" TEXT, "评估状态" TEXT
            );
            CREATE TABLE "调仓质量基金明细" ("调仓事件ID" TEXT);
            CREATE TABLE "调仓质量构建状态" (
                "构建ID" TEXT PRIMARY KEY, "算法版本" TEXT, "生成时间" TEXT,
                "排除渠道JSON" TEXT, "源事件数" INTEGER, "质量事件数" INTEGER,
                "源最新调仓日期" TEXT, "质量最新调仓日期" TEXT,
                "基金净值最新日期" TEXT, "缺失事件数" INTEGER, "孤立事件数" INTEGER
            );
            '''
        )
        conn.execute('INSERT INTO "策略调仓事件" VALUES (?)', ("event-new",))
        conn.execute(
            'INSERT INTO "策略模拟净值区间" VALUES (?,?,?,?,?)',
            ("event-new", "ttfund__S1", "ttfund", "2026-08-28", ALGORITHM),
        )
        conn.execute(
            'INSERT INTO "调仓质量事件分析" VALUES (?,?,?,?,?,?)',
            (quality_event_id, "ttfund__S1", "示例策略", "ttfund", "2026-08-28", "可评估"),
        )
        conn.execute('INSERT INTO "调仓质量基金明细" VALUES (?)', (quality_event_id,))
        if with_status:
            conn.execute(
                'INSERT INTO "调仓质量构建状态" VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (
                    "latest",
                    ALGORITHM,
                    "2026-09-01T12:00:00+08:00",
                    json.dumps(["qieman"]),
                    1,
                    1,
                    "2026-08-28",
                    "2026-08-28",
                    "2026-08-29",
                    0,
                    0,
                ),
            )
        conn.commit()


def write_detail(site_dir: Path, event_id: str | None, strategy_id: str = "ttfund__S1") -> None:
    detail_dir = site_dir / "data" / "details"
    detail_dir.mkdir(parents=True)
    curves = {}
    if event_id:
        points = [
            {"日期": "2026-08-28", "数值": 0.0},
            {"日期": "2026-08-29", "数值": 0.1},
        ]
        curves[event_id] = {
            "series": {
                "调仓前仓位模拟": {"points": points},
                "调仓后仓位实际": {"points": points},
            }
        }
    payload = {"id": strategy_id, "contributionCurves": curves}
    (detail_dir / f"{strategy_id}.js").write_text(
        f"window.__BASIC_DATA__.details[{json.dumps(strategy_id)}] = "
        + json.dumps(payload, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )


def write_summary(site_dir: Path, strategy_ids: list[str]) -> None:
    data_dir = site_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {"strategies": [{"统一策略ID": strategy_id} for strategy_id in strategy_ids]}
    (data_dir / "basic_summary_core.js").write_text(
        "window.__BASIC_DATA__.summaryCore = "
        + json.dumps(payload, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )


class RebalanceQualityFreshnessAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_catalog = MODULE.RULE_CATALOG
        MODULE.RULE_CATALOG = {
            "REBALANCE_QUALITY_FACTS_STALE": {
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
                "修复责任节点": "strategy_governance",
            },
            "REBALANCE_CONTRIBUTION_CURVE_MISSING": {
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
                "修复责任节点": "report_build",
            },
        }

    def tearDown(self) -> None:
        MODULE.RULE_CATALOG = self.original_catalog

    def test_matching_event_set_and_build_status_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "fixture.sqlite"
            create_fixture(db_path)
            issues: list[dict] = []
            MODULE.audit_rebalance_quality_freshness(issues, db_path, RULES)
        self.assertEqual(issues, [])

    def test_stale_event_id_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "fixture.sqlite"
            create_fixture(db_path, quality_event_id="event-old", with_status=False)
            issues: list[dict] = []
            MODULE.audit_rebalance_quality_freshness(issues, db_path, RULES)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "error")
        self.assertEqual(issues[0]["ruleId"], "REBALANCE_QUALITY_FACTS_STALE")
        self.assertEqual(issues[0]["sample"]["missingEvents"][0]["调仓事件ID"], "event-new")

    def test_latest_evaluable_event_with_exact_two_sided_curve_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "fixture.sqlite"
            site_dir = root / "site"
            create_fixture(db_path)
            write_detail(site_dir, "event-new")
            issues: list[dict] = []
            MODULE.audit_rebalance_contribution_curve_coverage(issues, db_path, site_dir, RULES)
        self.assertEqual(issues, [])

    def test_newer_unavailable_event_falls_back_to_latest_evaluable_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "fixture.sqlite"
            site_dir = root / "site"
            create_fixture(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    'INSERT INTO "调仓质量事件分析" VALUES (?,?,?,?,?,?)',
                    ("event-newer", "ttfund__S1", "示例策略", "ttfund", "2026-08-31", "暂不可评估"),
                )
                conn.commit()
            write_detail(site_dir, "event-new")
            issues: list[dict] = []
            MODULE.audit_rebalance_contribution_curve_coverage(issues, db_path, site_dir, RULES)
        self.assertEqual(issues, [])

    def test_latest_evaluable_event_without_exact_curve_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "fixture.sqlite"
            site_dir = root / "site"
            create_fixture(db_path)
            write_detail(site_dir, "event-old")
            issues: list[dict] = []
            MODULE.audit_rebalance_contribution_curve_coverage(issues, db_path, site_dir, RULES)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "error")
        self.assertEqual(issues[0]["ruleId"], "REBALANCE_CONTRIBUTION_CURVE_MISSING")
        self.assertEqual(issues[0]["sample"]["failures"][0]["调仓事件ID"], "event-new")

    def test_unpublished_historical_strategy_is_outside_page_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "fixture.sqlite"
            site_dir = root / "site"
            create_fixture(db_path)
            write_summary(site_dir, ["ttfund__OTHER"])
            write_detail(site_dir, None, strategy_id="ttfund__OTHER")
            issues: list[dict] = []
            MODULE.audit_rebalance_contribution_curve_coverage(issues, db_path, site_dir, RULES)
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
