from __future__ import annotations

import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

import retry_ttfund_official_curve_gaps as curve_retry  # noqa: E402
import run_resilient_fund_nav_refresh as nav_retry  # noqa: E402


class FundNavRetryTests(unittest.TestCase):
    def test_completed_summary_is_preferred_over_newer_progress_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            completed = root / "20260805T160620+0800.json"
            progress = root / "20260805T160620+0800.progress.json"
            completed.write_text('{"state":"completed"}\n', encoding="utf-8")
            progress.write_text('{"state":"running"}\n', encoding="utf-8")

            selected = nav_retry.select_completed_summary_path({completed, progress})

        self.assertEqual(selected, completed)

    def test_status_sets_treat_missing_result_as_failure(self) -> None:
        summary = {
            "latest_dates_after": {"000001": "2026-07-21"},
            "empty_fund_codes": ["000002"],
            "failures": [{"fund_code": "000003", "error": "timeout"}],
        }

        success, empty, failed = nav_retry.status_sets(
            summary,
            {"000001", "000002", "000003", "000004"},
        )

        self.assertEqual(success, {"000001"})
        self.assertEqual(empty, {"000002"})
        self.assertEqual(set(failed), {"000003", "000004"})
        self.assertEqual(failed["000004"]["error"], "collector_no_result")

    def test_status_sets_reads_success_codes_from_normalized_meta(self) -> None:
        with TemporaryDirectory() as directory:
            meta_path = Path(directory) / "fund_nav_meta.jsonl"
            meta_path.write_text(
                '{"\u57fa\u91d1\u4ee3\u7801":"000001","\u5386\u53f2\u7ed3\u675f\u65e5\u671f":"2026-07-21"}\n',
                encoding="utf-8",
            )
            summary = {
                "latest_dates_after": {"2026-07-21": 1},
                "normalized_meta_path": str(meta_path),
                "empty_fund_codes": ["000002"],
            }

            success, empty, failed = nav_retry.status_sets(
                summary,
                {"000001", "000002"},
            )

        self.assertEqual(success, {"000001"})
        self.assertEqual(empty, {"000002"})
        self.assertEqual(failed, {})

    def test_current_holding_scope_uses_each_strategys_latest_date(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    '''
                    CREATE TABLE "策略当前持仓" (
                        "统一策略ID" TEXT,
                        "渠道ID" TEXT,
                        "持仓日期" TEXT,
                        "基金代码" TEXT,
                        "基金权重_百分比" REAL
                    )
                    '''
                )
                conn.executemany(
                    'INSERT INTO "策略当前持仓" VALUES (?, ?, ?, ?, ?)',
                    [
                        ("ttfund__A", "ttfund", "2026-07-20", "000001", 50),
                        ("ttfund__A", "ttfund", "2026-07-21", "000002", 50),
                        ("gffunds__B", "gffunds", "2026-07-19", "000003", 30),
                        ("other__C", "other", "2026-07-21", "000004", 40),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            codes, error = nav_retry.load_current_holding_codes(db_path)

        self.assertIsNone(error)
        self.assertEqual(codes, {"000002", "000003"})


class OfficialCurveRetryTests(unittest.TestCase):
    def test_only_active_missing_strategies_enter_device_retry(self) -> None:
        rows = [
            {"渠道策略ID": "A", "quality_scope": "active"},
            {"渠道策略ID": "B", "quality_scope": "stopped"},
            {"渠道策略ID": "C", "quality_scope": "test"},
        ]

        self.assertEqual(curve_retry.active_missing_ids(rows), ["A"])

    def test_recovered_curve_rows_replace_same_strategy_date(self) -> None:
        existing = [
            {"source_strategy_id": "A", "trade_date": "2026-07-21", "nav": 1.01},
            {"source_strategy_id": "B", "trade_date": "2026-07-21", "nav": 1.02},
        ]
        recovered = [
            {"source_strategy_id": "A", "trade_date": "2026-07-21", "nav": 1.03},
            {"source_strategy_id": "A", "trade_date": "2026-07-22", "nav": 1.04},
        ]

        rows = curve_retry.merge_rows(existing, recovered)
        by_key = {(row["source_strategy_id"], row["trade_date"]): row for row in rows}

        self.assertEqual(len(rows), 3)
        self.assertEqual(by_key[("A", "2026-07-21")]["nav"], 1.03)
        self.assertEqual(by_key[("B", "2026-07-21")]["nav"], 1.02)

    def test_benchmark_gap_is_only_recovered_when_benchmark_reaches_latest_strategy_date(self) -> None:
        gap_types = {"A": "基准曲线缺失", "B": "策略曲线缺失", "C": "基准曲线滞后"}
        rows = [
            {"source_strategy_id": "A", "trade_date": "2026-07-21", "benchmark_return": None},
            {"source_strategy_id": "B", "trade_date": "2026-07-21", "benchmark_return": None},
            {"source_strategy_id": "C", "trade_date": "2026-07-20", "benchmark_return": 0.1},
            {"source_strategy_id": "C", "trade_date": "2026-07-21", "benchmark_return": None},
        ]

        recovered = curve_retry.recovered_ids_for_gaps(rows, gap_types)

        self.assertEqual(recovered, {"B"})

        rows.append({"source_strategy_id": "A", "trade_date": "2026-07-21", "benchmark_return": 0.2})
        rows.append({"source_strategy_id": "C", "trade_date": "2026-07-21", "benchmark_return": 0.3})
        self.assertEqual(curve_retry.recovered_ids_for_gaps(rows, gap_types), {"A", "B", "C"})

    def test_offline_fallback_device_is_detected_before_batch_visit(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["adb", "-s", "physical-1", "get-state"],
            returncode=1,
            stdout="",
            stderr="error: device 'physical-1' not found",
        )
        with patch.object(curve_retry.subprocess, "run", return_value=completed) as run:
            result = curve_retry.probe_adb_device("adb", "physical-1")

        self.assertEqual(result["state"], "unavailable")
        self.assertFalse(result["ready"])
        self.assertIn("not found", result["detail"])
        run.assert_called_once()

    def test_ready_fallback_device_passes_probe(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["adb", "-s", "physical-1", "get-state"],
            returncode=0,
            stdout="device\n",
            stderr="",
        )
        with patch.object(curve_retry.subprocess, "run", return_value=completed):
            result = curve_retry.probe_adb_device("adb", "physical-1")

        self.assertEqual(result["state"], "ready")
        self.assertTrue(result["ready"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
