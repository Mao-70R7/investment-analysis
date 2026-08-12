from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "生产程序" / "load_analysis_zh_current_sqlite.py"
SPEC = importlib.util.spec_from_file_location("load_analysis_zh_current_sqlite", SCRIPT)
assert SPEC and SPEC.loader
sys.path.insert(0, str(SCRIPT.parent))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NormalizedRunSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.entity_root = (
            self.root / "gfsec_fima" / "strategy_performance_daily" / "2026-07-31"
        )
        self.entity_root.mkdir(parents=True)
        for run_id in (
            "20260731T010042+0800__gfsec_fima_collect__attempt_01",
            "20260731T103654+0800__gfsec_fima_collect__attempt_01",
            "gfsec_fima_integration_20260728",
        ):
            (self.entity_root / f"{run_id}.jsonl").write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_timestamped_run_beats_legacy_lexicographic_name(self) -> None:
        with (
            patch.object(MODULE, "NORMALIZED_ROOT", self.root),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("GFSEC_FIMA_COLLECT_RUN_ID", None)
            files = MODULE.entity_files("gfsec_fima", "strategy_performance_daily")

        self.assertEqual(
            [path.stem for path in files],
            ["20260731T103654+0800__gfsec_fima_collect__attempt_01"],
        )

    def test_gate_run_id_selects_only_the_exact_batch(self) -> None:
        expected = "20260731T010042+0800__gfsec_fima_collect__attempt_01"
        with (
            patch.object(MODULE, "NORMALIZED_ROOT", self.root),
            patch.dict(os.environ, {"GFSEC_FIMA_COLLECT_RUN_ID": expected}),
        ):
            files = MODULE.entity_files("gfsec_fima", "strategy_performance_daily")

        self.assertEqual([path.stem for path in files], [expected])


class DailyPerformanceRepairTest(unittest.TestCase):
    def test_single_nav_point_does_not_invent_zero_drawdown(self) -> None:
        rows = [
            {
                "统一策略ID": "gfbank_cgb__demo",
                "交易日期": "2026-07-29",
                "单位净值": 1.0852,
                "累计收益率_百分比": 8.52,
                "日收益率_百分比": None,
                "最大回撤_百分比": None,
            }
        ]

        repaired = MODULE.repair_daily_performance_rows(rows, Counter())

        self.assertIsNone(repaired[0]["最大回撤_百分比"])

    def test_multiple_nav_points_still_compute_drawdown(self) -> None:
        rows = [
            {
                "统一策略ID": "demo",
                "交易日期": "2026-07-28",
                "单位净值": 1.1,
                "累计收益率_百分比": 10.0,
                "日收益率_百分比": None,
                "最大回撤_百分比": None,
            },
            {
                "统一策略ID": "demo",
                "交易日期": "2026-07-29",
                "单位净值": 1.0,
                "累计收益率_百分比": 0.0,
                "日收益率_百分比": None,
                "最大回撤_百分比": None,
            },
        ]

        repaired = MODULE.repair_daily_performance_rows(rows, Counter())

        self.assertEqual(repaired[-1]["最大回撤_百分比"], 9.09090909)


class StrategyCatalogFreshnessValidationTest(unittest.TestCase):
    def test_qieman_catalog_validation_closes_source_and_database_watermarks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary_path = Path(temporary) / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "channel_id": "qieman",
                        "catalog_strategy_ids": ["Q1", "Q2"],
                        "catalog_new_strategy_ids": ["Q2"],
                        "source_latest_nav_date": "2026-08-11",
                        "latest_nav_date_strategy_total": 2,
                    }
                ),
                encoding="utf-8",
            )
            connection = sqlite3.connect(":memory:")
            try:
                connection.executescript(
                    '''
                    CREATE TABLE "策略信息" ("渠道ID" TEXT, "渠道策略ID" TEXT);
                    CREATE TABLE "策略日度业绩" ("渠道ID" TEXT, "渠道策略ID" TEXT, "交易日期" TEXT);
                    INSERT INTO "策略信息" VALUES ('qieman', 'Q1'), ('qieman', 'Q2');
                    INSERT INTO "策略日度业绩" VALUES
                        ('qieman', 'Q1', '2026-08-11'),
                        ('qieman', 'Q2', '2026-08-11');
                    '''
                )
                validation = MODULE.validate_strategy_catalog_summaries(
                    connection, [summary_path], ["qieman"]
                )["qieman"]
                self.assertIs(validation["passed"], True)
                self.assertEqual(validation["loadedLatestNavDate"], "2026-08-11")

                connection.execute(
                    'UPDATE "策略日度业绩" SET "交易日期"="2026-08-07"'
                )
                with self.assertRaisesRegex(RuntimeError, "source_latest_nav_date"):
                    MODULE.validate_strategy_catalog_summaries(
                        connection, [summary_path], ["qieman"]
                    )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
