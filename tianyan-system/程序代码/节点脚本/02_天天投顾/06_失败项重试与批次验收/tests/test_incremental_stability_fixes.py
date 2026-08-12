from __future__ import annotations

import sys
import json
import os
import sqlite3
import subprocess
import unittest
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

import build_ttfund_incremental_plan as plan
import audit_ttfund_incremental_smoke as incremental_smoke
import discover_ttfund_strategy_catalog as catalog_discovery
import load_ttfund_incremental_performance as incremental_performance
import load_analysis_zh_current_sqlite as analysis_loader
from advisor_monitor.collectors.gffunds_public import GFFundsPublicCollector, RawResponse
from advisor_monitor.collectors.official_apps_public import catalog_page_closed
from advisor_monitor.strategy_catalog import (
    catalog_diff,
    load_catalog_manifest,
    reconcile_catalog_batch,
)
from summarize_incremental_collection_gaps import csv_first_available_column, parse_dt


class TTFundLifecycleTests(unittest.TestCase):
    def test_only_expired_observation_stage_is_definitively_stopped(self) -> None:
        as_of = date(2026, 7, 19)
        lifecycle = {
            "is_stop": True,
            "current_stage": "observeStage",
            "operate_end_time": "26-07-18",
        }

        self.assertEqual(
            plan.classify_current_holding_lifecycle("stopped", lifecycle, as_of=as_of),
            "definitively_stopped",
        )

    def test_future_operation_date_stays_in_refresh_scope(self) -> None:
        lifecycle = {
            "is_stop": True,
            "current_stage": "observeStage",
            "operate_end_time": "26-07-31",
        }

        self.assertEqual(
            plan.classify_current_holding_lifecycle("stopped", lifecycle, as_of=date(2026, 7, 19)),
            "stopped_operation_active",
        )

    def test_subscription_stage_and_missing_lifecycle_are_not_skipped(self) -> None:
        subscription = {
            "is_stop": True,
            "current_stage": "subscribeStage",
            "operate_end_time": "--",
        }

        self.assertEqual(
            plan.classify_current_holding_lifecycle("stopped", subscription, as_of=date(2026, 7, 19)),
            "stopped_active_stage",
        )
        self.assertEqual(
            plan.classify_current_holding_lifecycle("stopped", None, as_of=date(2026, 7, 19)),
            "stopped_lifecycle_missing",
        )

    def test_benchmark_retry_default_is_seven_days(self) -> None:
        with patch.object(sys, "argv", ["build_ttfund_incremental_plan.py"]):
            args = plan.parse_args()

        self.assertEqual(args.benchmark_detail_cooldown_days, 7)


class GapSummaryTests(unittest.TestCase):
    def test_run_timestamp_is_normalized_for_file_mtime_comparison(self) -> None:
        parsed = parse_dt("2026-07-19T12:12:07+08:00")

        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed.tzinfo)

    def test_official_curve_csv_accepts_chinese_channel_strategy_id(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "official_curve_missing.csv"
            path.write_text(
                "统一策略ID,渠道策略ID,策略名称\n"
                "ttfund__ABC123,ABC123,策略A\n"
                "ttfund__DEF456,DEF456,策略B\n",
                encoding="utf-8-sig",
            )

            values = csv_first_available_column(
                path,
                ("渠道策略ID", "source_strategy_id", "strategy_id"),
            )

        self.assertEqual(values, ["ABC123", "DEF456"])


class IncrementalSmokeAuditTests(unittest.TestCase):
    def test_newer_fund_nav_is_not_reported_as_stale(self) -> None:
        lag = incremental_smoke.date_lag_days("2026-08-08", "2026-08-07")

        self.assertEqual(lag, -1)
        self.assertFalse(incremental_smoke.fund_nav_lag_is_unacceptable(lag, 1))

    def test_fund_nav_older_than_allowed_lag_is_stale(self) -> None:
        lag = incremental_smoke.date_lag_days("2026-08-05", "2026-08-07")

        self.assertEqual(lag, 2)
        self.assertTrue(incremental_smoke.fund_nav_lag_is_unacceptable(lag, 1))


class IncrementalPerformanceConnectionTests(unittest.TestCase):
    def test_schema_transaction_is_committed_before_synchronous_pragma(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE setup_marker (id INTEGER)")
            conn.execute("INSERT INTO setup_marker VALUES (1)")
            self.assertTrue(conn.in_transaction)

            incremental_performance.configure_incremental_connection(conn)

            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM setup_marker").fetchone()[0], 1)
        finally:
            conn.close()


class IncrementalBaselineSelectionTests(unittest.TestCase):
    def test_failed_collection_batch_is_not_promoted_as_next_run_baseline(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            successful = root / "20260720.json"
            failed = root / "20260721.json"
            successful.write_text(
                json.dumps(
                    {
                        "run_id": "20260720",
                        "batch_state": "ready",
                        "strategy_total": 100,
                        "detail_cache_strategy_total": 95,
                        "captured_at": "2026-07-20T12:00:00",
                    }
                ),
                encoding="utf-8",
            )
            failed.write_text(
                json.dumps(
                    {
                        "run_id": "20260721",
                        "batch_state": "failed_quality_gate",
                        "strategy_total": 120,
                        "detail_cache_strategy_total": 120,
                        "captured_at": "2026-07-21T12:00:00",
                    }
                ),
                encoding="utf-8",
            )

            path, payload = plan.best_collection_summary(root)

            self.assertEqual(path, successful)
        self.assertEqual(payload["run_id"], "20260720")


class StrategyCatalogDiscoveryTests(unittest.TestCase):
    def test_catalog_extracts_strategy_id_from_detail_skip_url(self) -> None:
        node = {
            "skipUrl": (
                "fund://mp.1234567.com.cn/weex/app/pages/strategyDetail/index"
                "?id=JNLCAYT&fromOutStrategy=toGuoduChoose"
            )
        }

        self.assertEqual(catalog_discovery.strategy_ids_from_node(node), ["JNLCAYT"])

    def test_catalog_does_not_treat_unrelated_article_id_as_strategy(self) -> None:
        node = {"skipUrl": "fund://page/newsdetail?id=20260808001"}

        self.assertEqual(catalog_discovery.strategy_ids_from_node(node), [])

    def test_advisor_cache_uses_code_as_partner_id(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "saveAllAdvisersInfokey_demo"
            path.write_text(
                json.dumps(
                    [
                        {"id": 6, "code": "644", "name": "国联民生证券投顾"},
                        {"id": 4, "code": "469", "name": "中欧财富投顾"},
                    ]
                ),
                encoding="utf-8",
            )

            rows = catalog_discovery.extract_advisor_rows(Path(directory))

        self.assertEqual([row["partner_id"] for row in rows], ["469", "644"])
        self.assertEqual(rows[0]["advisor_name"], "中欧财富投顾")

    def test_advisor_log_extracts_and_validates_grouped_strategy_ids(self) -> None:
        log_text = "\n".join(
            [
                "I ReactNativeJS: strategyId: 'ABC1234&0&DEF5678&ABC1234'",
                "I ReactNativeJS: strategyId: 'SHORT&TOO_LONG_1'",
            ]
        )

        self.assertEqual(
            catalog_discovery.strategy_ids_from_advisor_log(log_text),
            ["ABC1234", "DEF5678"],
        )

    def test_advisor_page_uses_supported_type8_link_wrapper(self) -> None:
        deep_link = catalog_discovery.advisor_page_deep_link("469")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(deep_link).query)
        wrapper = json.loads(query["linkto"][0])

        self.assertEqual(query["type"], ["8"])
        self.assertEqual(wrapper["LinkType"], 2)
        self.assertEqual(
            wrapper["LinkTo"],
            "fund://mp.1234567.com.cn/weex/"
            "fund034076731f1e4b/pages/question/index?partnerId=469",
        )

    def test_advisor_catalog_rows_merge_with_home_metadata(self) -> None:
        home_rows = [
            {
                "source_strategy_id": "ABC1234",
                "strategy_name": "首页策略",
                "partner_id": None,
                "strategy_type": "稳健",
                "launch_date": "2026-08-01",
                "skip_url": "fund://detail?id=ABC1234",
                "source_files": ["home.json"],
            }
        ]
        advisor_result = {
            "results": [
                {
                    "partner_id": "469",
                    "advisor_name": "中欧财富投顾",
                    "strategy_ids": ["ABC1234", "DEF5678"],
                    "evidence_path": "partner_469_evidence.log",
                }
            ]
        }

        rows, ids = catalog_discovery.merge_catalog_rows(home_rows, advisor_result)

        self.assertEqual(ids, ["ABC1234", "DEF5678"])
        by_id = {row["source_strategy_id"]: row for row in rows}
        self.assertEqual(by_id["ABC1234"]["strategy_name"], "首页策略")
        self.assertEqual(by_id["ABC1234"]["partner_id"], "469")
        self.assertEqual(by_id["DEF5678"]["advisor_name"], "中欧财富投顾")

    def test_catalog_diff_reports_ids_not_in_local_baseline(self) -> None:
        diff = catalog_diff(["A", "B", "B", ""], ["B", "C"])

        self.assertEqual(diff["catalog_strategy_ids"], ["A", "B"])
        self.assertEqual(diff["new_strategy_ids"], ["A"])
        self.assertEqual(diff["new_strategy_total"], 1)

    def test_catalog_batch_reconciliation_requires_every_new_id_in_master(self) -> None:
        reconciliation = reconcile_catalog_batch(
            ["OLD", "NEW_A", "NEW_B"],
            ["OLD", "NEW_A", "EXTRA"],
            ["OLD"],
        )

        self.assertFalse(reconciliation["catalog_batch_closed"])
        self.assertEqual(reconciliation["catalog_batch_missing_strategy_ids"], ["NEW_B"])
        self.assertEqual(reconciliation["catalog_new_strategy_collected_ids"], ["NEW_A"])
        self.assertEqual(reconciliation["catalog_new_strategy_missing_ids"], ["NEW_B"])

    def test_single_page_catalog_uses_reported_total_to_prove_closure(self) -> None:
        rows = [{"id": "A"}, {"id": "B"}]

        self.assertTrue(catalog_page_closed({"total": 2}, rows, 999))
        self.assertFalse(catalog_page_closed({"total": 3}, rows, 999))

    def test_transactional_catalog_validation_rejects_missing_new_strategy(self) -> None:
        with TemporaryDirectory() as directory:
            summary_path = Path(directory) / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "channel_id": "gffunds",
                        "catalog_strategy_ids": ["OLD", "NEW"],
                        "catalog_new_strategy_ids": ["NEW"],
                    }
                ),
                encoding="utf-8",
            )
            connection = sqlite3.connect(":memory:")
            try:
                connection.execute('CREATE TABLE "策略信息" ("渠道ID" TEXT, "渠道策略ID" TEXT)')
                connection.execute('INSERT INTO "策略信息" VALUES (?, ?)', ("gffunds", "OLD"))
                with self.assertRaisesRegex(RuntimeError, "missing_catalog"):
                    analysis_loader.validate_strategy_catalog_summaries(
                        connection,
                        [summary_path],
                        ["gffunds"],
                    )
            finally:
                connection.close()

    def test_catalog_manifest_ids_are_merged_and_incomplete_is_preserved(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(
                json.dumps(
                    {
                        "state": "ready",
                        "catalog_strategy_ids": ["NEW", "NEW", "OLD"],
                        "catalog_complete": False,
                        "catalog_completeness": "unverified_app_cache_without_server_total",
                    }
                ),
                encoding="utf-8",
            )
            manifest = load_catalog_manifest(path)

        self.assertEqual(manifest["catalog_strategy_ids"], ["NEW", "OLD"])
        self.assertEqual(manifest["catalog_strategy_total"], 2)
        self.assertFalse(manifest["catalog_complete"])


class GFFundsCatalogPaginationTests(unittest.TestCase):
    def test_catalog_paginates_until_short_page(self) -> None:
        with TemporaryDirectory() as directory:
            collector = GFFundsPublicCollector(Path(directory), run_id="catalog_test")
            calls: list[int] = []

            def fake_post_form(endpoint, body, *, collector_name, raw_relative_path):
                page = int(body["page_no"])
                calls.append(page)
                rows = (
                    [{"adv_id": f"GFJJ{index:06d}"} for index in range(200)]
                    if page == 1
                    else [{"adv_id": "GFJJ000200"}]
                )
                return RawResponse(
                    json_data={"RETCODE": "0000", "config_list": rows},
                    text="",
                    snapshot={"parse_status": "success"},
                    raw_path=Path(directory) / str(raw_relative_path),
                )

            collector.post_form = fake_post_form  # type: ignore[method-assign]
            rows, info = collector.collect_strategy_catalog()

        self.assertEqual(calls, [1, 2])
        self.assertEqual(len(rows), 201)
        self.assertTrue(info["catalog_complete"])
        self.assertEqual(info["catalog_stop_reason"], "short_page")


class DetailCacheFreshnessTests(unittest.TestCase):
    @staticmethod
    def write_cache(path: Path, *, valid: bool, mtime: datetime) -> None:
        payload = {"tgExtendInfo": {"currentStage": "observeStage"}} if valid else {}
        path.write_text(json.dumps(payload), encoding="utf-8")
        timestamp = mtime.timestamp()
        os.utime(path, (timestamp, timestamp))

    def test_layout_cache_does_not_refresh_authoritative_detail_mtime(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old_detail_time = datetime(2026, 7, 17, 12, 0, 0)
            failed_attempt_time = datetime(2026, 7, 25, 9, 0, 0)
            self.write_cache(
                root / "strategyDetailPageDataABC123_app.0",
                valid=True,
                mtime=old_detail_time,
            )
            self.write_cache(
                root / "ttfund-layout-cache-advicer-strategy-detail-matter-ABC123-datas_app.0",
                valid=True,
                mtime=failed_attempt_time,
            )

            inventory = plan.load_cache_inventory(root)

        self.assertEqual(inventory["detail_strategy_ids"], ["ABC123"])
        self.assertEqual(inventory["detail_file_strategy_ids"], ["ABC123"])
        self.assertEqual(inventory["detail_layout_file_strategy_ids"], ["ABC123"])
        self.assertEqual(inventory["detail_freshness_source"], "strategyDetailPageData")
        self.assertIs(inventory["layout_cache_counts_as_detail"], False)
        detail_mtime = datetime.fromisoformat(inventory["detail_mtime_by_strategy"]["ABC123"])
        self.assertEqual(detail_mtime.replace(tzinfo=None), old_detail_time)

    def test_layout_only_cache_is_not_counted_as_complete_detail(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_cache(
                root / "ttfund-layout-cache-advicer-strategy-detail-matter-ABC123-datas_app.0",
                valid=True,
                mtime=datetime(2026, 7, 25, 9, 0, 0),
            )

            inventory = plan.load_cache_inventory(root)

        self.assertEqual(inventory["detail_strategy_ids"], [])
        self.assertEqual(inventory["detail_file_strategy_ids"], [])
        self.assertEqual(inventory["detail_layout_file_strategy_ids"], ["ABC123"])
        self.assertNotIn("ABC123", inventory["detail_mtime_by_strategy"])

    def test_invalid_detail_response_never_gets_freshness_mtime(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_cache(
                root / "strategyDetailPageDataABC123_app.0",
                valid=False,
                mtime=datetime(2026, 7, 25, 9, 0, 0),
            )

            inventory = plan.load_cache_inventory(root)

        self.assertEqual(inventory["detail_strategy_ids"], [])
        self.assertEqual(inventory["invalid_detail_strategy_ids"], ["ABC123"])
        self.assertNotIn("ABC123", inventory["detail_mtime_by_strategy"])


class PowerShellResultContractTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell contract")
    def test_json_array_helper_expands_every_strategy_row(self) -> None:
        helper = ROOT / "节点脚本" / "_共享组件" / "生产程序" / "json_array_helpers.ps1"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result_path = root / "results.json"
            script_path = root / "verify.ps1"
            result_path.write_text(
                json.dumps([{"strategy_id": "A"}, {"strategy_id": "B"}]),
                encoding="utf-8",
            )
            helper_literal = str(helper).replace("'", "''")
            result_literal = str(result_path).replace("'", "''")
            script_path.write_text(
                f"""
$ErrorActionPreference = "Stop"
. '{helper_literal}'
$rows = @(Read-JsonArrayStrict -Path '{result_literal}')
[ordered]@{{
    count = $rows.Count
    ids = @($rows | ForEach-Object {{ [string]$_.strategy_id }})
}} | ConvertTo-Json -Compress
""".strip(),
                encoding="utf-8-sig",
            )

            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["ids"], ["A", "B"])

    def test_incremental_script_uses_strict_array_reader(self) -> None:
        script = (
            ROOT
            / "节点脚本"
            / "_共享组件"
            / "生产程序"
            / "run_ttfund_incremental_update.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("Read-JsonArrayStrict -Path $ResultsPath", script)
        self.assertNotIn("$rows = @(Get-Content -LiteralPath $ResultsPath", script)

    def test_incremental_script_retries_failed_ids_on_the_same_physical_phone(self) -> None:
        script = (
            ROOT
            / "节点脚本"
            / "_共享组件"
            / "生产程序"
            / "run_ttfund_incremental_update.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("[int]$DeviceFailureCircuitBreakThreshold = 5", script)
        self.assertIn(
            '"--soft-circuit-break-consecutive-incomplete-detail", "$DeviceFailureCircuitBreakThreshold"',
            script,
        )
        self.assertIn("Get-DriveFailureIds", script)
        self.assertIn("01_detail_drive_physical_retry", script)
        self.assertIn('"--device-id", $DeviceId', script)
        self.assertIn('device_mode = "physical_only"', script)
        self.assertIn("same_phone_failed_ids_second_pass", script)
        self.assertNotIn("ADVISOR_FALLBACK_DEVICE_ID", script)
        self.assertNotIn("MuMu", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
