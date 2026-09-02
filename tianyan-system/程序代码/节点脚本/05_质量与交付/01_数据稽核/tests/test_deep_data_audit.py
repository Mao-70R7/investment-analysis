from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "_共享组件"
    / "生产程序"
    / "run_deep_data_audit.py"
)
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("run_deep_data_audit", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DeepDataAuditTest(unittest.TestCase):
    def test_rollup_status_prefers_error_then_warn(self) -> None:
        status, summary = MODULE.rollup_status(
            [{"status": "ok"}, {"status": "warn"}, {"status": "error"}]
        )
        self.assertEqual(status, "error")
        self.assertEqual(summary, {"error": 1, "warn": 1, "ok": 1, "total": 3})

    def test_parse_number_map_rejects_non_object(self) -> None:
        payload, error = MODULE.parse_number_map("[1,2]")
        self.assertEqual(payload, {})
        self.assertEqual(error, "not_object")

    def test_exact_holding_snapshot_outside_tolerance_is_error(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE "策略当前持仓"(
              "统一策略ID" TEXT,
              "渠道ID" TEXT,
              "持仓日期" TEXT,
              "基金代码" TEXT,
              "基金名称" TEXT,
              "基金权重_百分比" REAL,
              "是否精确权重" INTEGER
            )
            """
        )
        conn.executemany(
            'INSERT INTO "策略当前持仓" VALUES(?,?,?,?,?,?,?)',
            [
                ("s1", "c1", "2026-07-30", "000001", "A", 60.0, 1),
                ("s1", "c1", "2026-07-30", "000002", "B", 30.0, 1),
            ],
        )
        checks: list[dict[str, object]] = []
        metrics = MODULE.holding_checks(conn, {"策略当前持仓"}, checks, 10)
        self.assertEqual(metrics["badExactSnapshotCount"], 1)
        self.assertEqual(checks[0]["status"], "error")
        conn.close()

    def test_markdown_report_contains_findings_and_channels(self) -> None:
        report = {
            "generatedAt": "2026-07-30T00:00:00+08:00",
            "status": "warn",
            "summary": {"error": 0, "warn": 1, "ok": 1, "total": 2},
            "database": {
                "path": "db.sqlite",
                "quickCheck": "ok",
                "quickCheckSeconds": 1,
                "keyTableRows": {"策略信息": 1},
            },
            "pages": {"reportRoot": "site", "manifestPath": "site/deployment_manifest.json"},
            "checks": [
                {
                    "status": "warn",
                    "domain": "pages",
                    "name": "示例",
                    "current": 1,
                    "threshold": "0",
                    "detail": "说明",
                    "impact": "影响",
                    "recommendation": "建议",
                }
            ],
            "channels": [
                {
                    "channelId": "c",
                    "strategies": 1,
                    "currentHoldingStrategies": 1,
                    "currentHoldingRows": 2,
                    "dailyStrategies": 1,
                    "dailyRows": 3,
                    "dailyLatestDate": "2026-07-30",
                    "intervalStrategies": 1,
                    "intervalRows": 4,
                    "rebalanceStrategies": 1,
                    "rebalanceEvents": 1,
                    "rebalanceDetails": 2,
                }
            ],
            "components": {},
            "artifacts": {"json": "report.json", "markdown": "report.md"},
            "production": {"latestDailySummaryPath": "summary.json"},
        }
        text = MODULE.render_report(report)
        self.assertIn("主要问题", text)
        self.assertIn("渠道覆盖总览", text)
        self.assertIn("示例", text)


if __name__ == "__main__":
    unittest.main()
