from __future__ import annotations

import codecs
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


FRAMEWORK_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "bridge_node.py").is_file())
CODE_ROOT = FRAMEWORK_ROOT.parent.parent
sys.path.insert(0, str(FRAMEWORK_ROOT))

from bridge_node import (  # noqa: E402
    Bridge,
    assess_gf_supplemental_channel,
    assess_gffunds_core_coverage,
    assess_qieman_nav_freshness,
    assess_strategy_catalog_batch,
    cleanup_daily_logs,
    cleanup_minimal_report_sources,
    configured_gf_supplemental_channels,
    configured_report_scope,
    emit_console,
    previous_gffunds_collection_total,
    previous_gfsec_fima_product_total,
    previous_channel_strategy_total,
    promote_report_directory,
)


class BridgeSafetyTests(unittest.TestCase):
    def test_unique_entry_keeps_window_open_with_automation_opt_out(self) -> None:
        entry_path = CODE_ROOT.parent / "00_每日数据更新并发布_唯一入口.bat"
        entry = entry_path.read_text(encoding="utf-8-sig")
        self.assertIn('set "KEEP_WINDOW_OPEN=1"', entry)
        self.assertIn('if /I "%ADVISOR_KEEP_WINDOW_OPEN%"=="0"', entry)
        self.assertIn("pause >nul", entry)
        self.assertIn("The task has ended.", entry)

        raw = entry_path.read_bytes()
        self.assertNotIn(
            b"\n",
            raw.replace(b"\r\n", b""),
            "Windows batch entry must use CRLF consistently",
        )

    def test_console_relay_does_not_fail_a_node_when_stdout_is_detached(self) -> None:
        original_stdout = sys.stdout

        class DetachedStdout:
            def write(self, _text: str) -> None:
                raise OSError(22, "detached")

            def flush(self) -> None:
                raise OSError(22, "detached")

        try:
            sys.stdout = DetachedStdout()
            emit_console("progress")
            self.assertNotEqual(sys.stdout, original_stdout)
        finally:
            replacement = sys.stdout
            sys.stdout = original_stdout
            if replacement is not original_stdout and hasattr(replacement, "close"):
                replacement.close()

    def test_daily_pipeline_covers_enabled_channels_and_keeps_southern_manual_only(self) -> None:
        pipeline = json.loads((CODE_ROOT / "节点脚本" / "pipeline.json").read_text(encoding="utf-8"))
        pipeline_nodes = {row["id"]: row for row in pipeline["nodes"]}
        daily_nodes = {
            row["id"]: row
            for row in pipeline["nodes"]
            if (row.get("enabledWhen") or {}).get("daily") is True
        }
        required_nodes = {
            "ttfund_incremental",
            "gffunds_performance",
            "gffunds_collect",
            "gffunds_gate",
            "gfsec_fima_collect",
            "gfsec_fima_gate",
            "gfsec_fima_load",
            "gf_supplemental_collect",
            "gf_supplemental_gate",
            "gf_supplemental_load",
            "qieman_collect",
            "qieman_gate",
            "qieman_load",
            "public_fund_snapshot",
        }
        self.assertEqual(required_nodes - set(daily_nodes), set())
        self.assertEqual(
            daily_nodes["public_fund_snapshot"]["dependencies"],
            ["strategy_governance"],
        )
        self.assertEqual(
            daily_nodes["report_build"]["dependencies"],
            ["public_fund_snapshot"],
        )
        self.assertTrue(
            {"ttfund_incremental", "gffunds_gate", "gfsec_fima_load", "gf_supplemental_load", "qieman_load"}
            <= set(daily_nodes["process_load"]["dependencies"])
        )
        self.assertNotIn("southern_load", daily_nodes["process_load"]["dependencies"])
        for node_id in ("southern_collect", "southern_gate", "southern_load"):
            self.assertIs((pipeline_nodes[node_id].get("enabledWhen") or {}).get("daily"), False)
            manifest_path = (
                CODE_ROOT / "节点脚本" / pipeline_nodes[node_id]["directory"] / "node.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIs(manifest["daily"], False)
            self.assertEqual(manifest["criticality"], "optional")
            self.assertEqual(manifest["failureImpact"], "warning")
        process_manifest_path = (
            CODE_ROOT
            / "节点脚本"
            / daily_nodes["process_load"]["directory"]
            / "node.json"
        )
        process_manifest = json.loads(process_manifest_path.read_text(encoding="utf-8"))
        self.assertIs(process_manifest["allowFailedOptionalDependencies"], True)
        declared_order = {row["id"]: index for index, row in enumerate(pipeline["nodes"])}
        for row in pipeline["nodes"]:
            for dependency in row["dependencies"]:
                self.assertLess(
                    declared_order[dependency],
                    declared_order[row["id"]],
                    f"{row['id']} must be declared after dependency {dependency}",
                )

        policy = json.loads((CODE_ROOT / "config" / "daily_update_policy.json").read_text(encoding="utf-8"))
        for channel_id in ("gffunds", "gfsec_fima", "gfsec_robot"):
            self.assertIs(policy["channels"][channel_id]["requireCompleteCatalog"], True)
            self.assertIs(policy["channels"][channel_id]["requireCatalogBatchClosure"], True)
        self.assertIs(policy["channels"]["qieman"]["requireCatalogDiscoveryClosure"], True)
        self.assertIs(policy["channels"]["qieman"]["requireCatalogBatchClosure"], True)
        qieman_policy = policy["channels"]["qieman"]
        self.assertLessEqual(qieman_policy["historySignalPageSize"], 25)
        self.assertGreaterEqual(qieman_policy["historyRequestIdleTimeoutSeconds"], 120)
        self.assertGreaterEqual(
            qieman_policy["historyRequestTotalTimeoutSeconds"],
            qieman_policy["historyRequestIdleTimeoutSeconds"],
        )
        self.assertGreaterEqual(qieman_policy["historyRequestAttempts"], 4)
        self.assertEqual(qieman_policy["maximumNavDateLagBusinessDays"], 1)
        self.assertGreaterEqual(qieman_policy["minimumFreshNavDateRatio"], 0.98)
        southern_policy = policy["channels"]["southern"]
        self.assertIs(southern_policy["dailyUpdateEnabled"], False)
        self.assertEqual(southern_policy["updateMode"], "manual_only")
        self.assertIs(southern_policy["preserveExistingDatabaseRows"], True)
        self.assertEqual(southern_policy["minimumPerformanceStrategyRatio"], 1.0)
        self.assertEqual(southern_policy["minimumHistoricalHoldingRatio"], 1.0)
        self.assertGreaterEqual(southern_policy["minimumExactBenchmarkRatio"], 0.94)
        field_rules = json.loads(
            (CODE_ROOT / "config" / "系统字段检查规则.json").read_text(encoding="utf-8")
        )
        southern_freshness = next(
            row
            for row in field_rules["channelPerformanceFreshness"]
            if row.get("channelId") == "southern"
        )
        southern_coverage = next(
            row
            for row in field_rules["channelStrategyCoverage"]
            if row.get("channelId") == "southern"
        )
        self.assertEqual(southern_freshness["severity"], "warn")
        self.assertEqual(southern_freshness["dailyUpdateMode"], "manual_only")
        self.assertEqual(southern_coverage["severity"], "warn")
        self.assertEqual(southern_coverage["dailyUpdateMode"], "manual_only")

    def test_qieman_freshness_accepts_one_business_day_lag_at_ninety_eight_percent(self) -> None:
        result = assess_qieman_nav_freshness(
            {
                "strategy_total": 100,
                "source_latest_nav_date": "2026-08-12",
                "nav_latest_date_counts": {
                    "2026-08-12": 70,
                    "2026-08-11": 28,
                    "2026-08-10": 2,
                },
            },
            {
                "maximumNavDateLagBusinessDays": 1,
                "minimumFreshNavDateRatio": 0.98,
            },
        )

        self.assertIs(result["passed"], True)
        self.assertEqual(result["minimumFreshNavDate"], "2026-08-11")
        self.assertEqual(result["freshNavDateStrategyTotal"], 98)
        self.assertEqual(result["freshNavDateStrategyRatio"], 0.98)

    def test_qieman_freshness_rejects_older_or_missing_rows_beyond_two_percent(self) -> None:
        result = assess_qieman_nav_freshness(
            {
                "strategy_total": 100,
                "source_latest_nav_date": "2026-08-12",
                "nav_latest_date_counts": {
                    "2026-08-12": 70,
                    "2026-08-11": 27,
                    "2026-08-10": 2,
                },
            },
            {
                "maximumNavDateLagBusinessDays": 1,
                "minimumFreshNavDateRatio": 0.98,
            },
        )

        self.assertIs(result["passed"], False)
        self.assertEqual(result["freshNavDateStrategyTotal"], 97)

        contracts = {
            "ttfund": (
                CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "run_ttfund_incremental_update.ps1",
                ("discover_ttfund_strategy_catalog.py", "--catalog-manifest-path", "catalog_discovered_new_total"),
            ),
            "official_apps": (
                CODE_ROOT / "节点脚本" / "_共享组件" / "python_src" / "advisor_monitor" / "collectors" / "official_apps_public.py",
                ("catalog_new_strategy_total", "catalog_new_strategy_collected_total", "catalog_batch_closed"),
            ),
            "qieman": (
                CODE_ROOT / "节点脚本" / "03_且慢" / "01_目录增量与新策略采集" / "src" / "qieman_daily_update.py",
                ("catalog_new_strategy_total", "catalog_new_strategy_collected_total", "catalog_batch_missing_strategy_total"),
            ),
            "southern": (
                CODE_ROOT / "节点脚本" / "03_南方基金" / "01_目录与登录态采集" / "src" / "southern_daily_update.py",
                (
                    "IA049",
                    "IA050",
                    "IA028",
                    "catalog_new_strategy_total",
                    "catalog_batch_closed",
                    "run_southern_live_collect.py",
                    "southern_login.dpapi",
                ),
            ),
        }
        for label, (path, tokens) in contracts.items():
            text = path.read_text(encoding="utf-8-sig")
            for token in tokens:
                self.assertIn(token, text, f"{label} missing new-strategy contract token {token}")

    def test_channel_nodes_use_the_declared_bridge_path(self) -> None:
        roots = (
            CODE_ROOT / "节点脚本" / "03_广发证券",
            CODE_ROOT / "节点脚本" / "03_且慢",
            CODE_ROOT / "节点脚本" / "03_广发渠道补充",
            CODE_ROOT / "节点脚本" / "03_南方基金",
        )
        run_scripts = [path for root in roots for path in root.rglob("run.ps1")]
        self.assertTrue(run_scripts)
        for path in run_scripts:
            self.assertTrue(
                path.read_bytes().startswith(codecs.BOM_UTF8),
                f"Windows PowerShell 5.1 requires a UTF-8 BOM for non-ASCII paths: {path}",
            )
            text = path.read_text(encoding="utf-8-sig")
            self.assertNotIn("-Recurse -File -Filter 'bridge_node.py'", text, str(path))
            self.assertIn("节点脚本\\00_调度框架\\bridge_node.py", text, str(path))

    def test_catalog_gate_requires_complete_and_closed_new_strategy_batch(self) -> None:
        failures, metrics = assess_strategy_catalog_batch(
            {
                "catalog_complete": True,
                "catalog_batch_closed": False,
                "catalog_strategy_total": 3,
                "catalog_new_strategy_total": 2,
                "catalog_new_strategy_collected_total": 1,
                "catalog_batch_missing_strategy_ids": ["NEW_B"],
                "catalog_new_strategy_missing_ids": ["NEW_B"],
            },
            {"requireCompleteCatalog": True, "requireCatalogBatchClosure": True},
        )

        self.assertEqual(failures, ["strategy_catalog_batch_closure"])
        self.assertEqual(metrics["catalogNewStrategyCollectedTotal"], 1)
        self.assertEqual(metrics["catalogNewStrategyMissingIds"], ["NEW_B"])

    def test_gf_supplemental_channel_selection_excludes_bank(self) -> None:
        self.assertEqual(
            configured_gf_supplemental_channels("gfsec_robot"),
            ("gfsec_robot",),
        )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            configured_gf_supplemental_channels("gfbank_cgb,gfsec_robot")

    def test_gf_supplemental_gate_accepts_expected_public_and_cached_boundaries(self) -> None:
        robot_failures, robot_warnings, robot_metrics = assess_gf_supplemental_channel(
            "gfsec_robot",
            {
                "collection_status": "success_public_strategy_and_recommendation",
                "strategy_total": 49,
                "daily_performance_rows": 3496,
                "interval_performance_rows": 371,
                "recommendation_fund_rows": 90,
            },
            {"strategy_master_ok": True, "fund_level_position_ok": False},
            {"minimumStrategyTotal": 30, "minimumInventoryRetentionRatio": 0.9},
            49,
        )
        self.assertEqual(robot_failures, [])
        self.assertIn("public_recommendation_list_is_not_precise_holding", robot_warnings)
        self.assertEqual(robot_metrics["strategyTotal"], 49)

        bank_failures, bank_warnings, _ = assess_gf_supplemental_channel(
            "gfbank_cgb",
            {
                "collection_status": "success_public_entries_and_authenticated_cache",
                "strategy_total": 14,
                "daily_performance_rows": 1,
                "authenticated_cache_run_id": "cache-run",
                "authenticated_cache_captured_at": "2026-07-01T00:00:00+08:00",
            },
            {"strategy_master_ok": True, "fund_level_position_ok": False},
            {"minimumStrategyTotal": 10, "maximumAuthenticatedCacheAgeDays": 30},
            14,
            now=datetime.fromisoformat("2026-07-29T00:00:00+08:00"),
        )
        self.assertEqual(bank_failures, [])
        self.assertIn("authenticated_ui_has_no_fund_level_holding", bank_warnings)

    def test_gf_supplemental_gate_rejects_inventory_collapse(self) -> None:
        failures, _warnings, _metrics = assess_gf_supplemental_channel(
            "gfsec_robot",
            {
                "collection_status": "success_public_strategy_and_recommendation",
                "strategy_total": 20,
                "daily_performance_rows": 1,
                "interval_performance_rows": 1,
                "recommendation_fund_rows": 1,
            },
            {"strategy_master_ok": True, "fund_level_position_ok": False},
            {"minimumStrategyTotal": 10, "minimumInventoryRetentionRatio": 0.9},
            49,
        )
        self.assertIn("strategy_inventory_retention", failures)

    def test_previous_channel_total_skips_current_exact_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = root / "2026-07-28" / "previous.json"
            current = root / "2026-07-29" / "current.json"
            previous.parent.mkdir(parents=True, exist_ok=True)
            current.parent.mkdir(parents=True, exist_ok=True)
            previous.write_text(json.dumps({"channel_id": "gfbank_cgb", "collection_status": "success", "strategy_total": 14}), encoding="utf-8")
            current.write_text(json.dumps({"channel_id": "gfbank_cgb", "collection_status": "success", "strategy_total": 1}), encoding="utf-8")
            self.assertEqual(previous_channel_strategy_total(root, current, "gfbank_cgb"), 14)

    def test_previous_gfsec_fima_product_total_uses_latest_successful_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            older = root / "2026-07-26" / "older.json"
            newer = root / "2026-07-27" / "newer.json"
            current = root / "2026-07-28" / "current.json"
            for path, payload in (
                (older, {"channel_id": "gfsec_fima", "collection_status": "success", "strategy_total": 41}),
                (newer, {"channel_id": "gfsec_fima", "collection_status": "success", "strategy_total": 44}),
                (current, {"channel_id": "gfsec_fima", "collection_status": "success", "strategy_total": 44}),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
            now = time.time()
            os.utime(older, (now - 20, now - 20))
            os.utime(newer, (now - 10, now - 10))
            os.utime(current, (now, now))
            self.assertEqual(previous_gfsec_fima_product_total(root, current), 44)

    def test_strategy_governance_starts_with_lifecycle_and_rebalance_dedupe(self) -> None:
        bridge = Bridge.__new__(Bridge)
        calls: list[tuple[object, ...]] = []
        bridge.postprocess_range = lambda *args: calls.append(args) or 0

        self.assertEqual(bridge.strategy_governance(), 0)
        self.assertEqual(
            calls[0][0],
            "02d_govern_strategy_lifecycle_and_rebalance",
        )
        self.assertEqual(
            calls[0][1],
            "19f_audit_fund_lookthrough_coverage_after_gap_repair",
        )
        self.assertIn("--skip-performance-governance-backup", calls[0])

    def test_fund_nav_forwards_exact_trade_date_to_avoid_full_rescan_on_resume(self) -> None:
        bridge = Bridge.__new__(Bridge)
        calls: list[tuple[object, ...]] = []
        bridge.postprocess_range = lambda *args: calls.append(args) or 0

        with patch.dict(os.environ, {"TTFUND_TARGET_TRADE_DATE": "2026-08-11"}):
            self.assertEqual(bridge.fund_nav(), 0)

        self.assertEqual(calls[0][:2], ("04_refresh_fund_nav_public", "04_refresh_fund_nav_public"))
        self.assertIn("--target-trade-date", calls[0])
        self.assertIn("2026-08-11", calls[0])

    def test_report_build_daily_scope_only_builds_minimal_publish_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bridge = Bridge.__new__(Bridge)
            bridge.args = SimpleNamespace(dry_run=True)
            bridge.report_root = root / "formal_report"
            bridge.database_root = root / "database"
            bridge.temp_root = root / "temp"
            bridge.child_run_id = "run"
            bridge.node_run_dir = root / "node"
            bridge.policy = {"reports": {"dailyScope": "minimal_publish"}}
            bridge.context_updates = {}
            bridge.artifacts = []
            bridge.counters = {}
            commands: list[list[str]] = []
            bridge.python_command = lambda script, *args: ["python", "-u", "-X", "utf8", script, *args]
            bridge.run_command = lambda command: commands.append(command) or 0

            self.assertEqual(bridge.report_build(), 0)

            staging = root / "temp" / "minimal_report_source" / "run"
            self.assertEqual(
                bridge.context_updates["ADVISOR_REPORT_STAGING_ROOT"],
                str(staging),
            )
            self.assertEqual(
                bridge.context_updates["ADVISOR_REPORT_SCOPE"],
                "minimal_publish",
            )
            self.assertFalse(
                any("generate_full_data_statistics_report.py" in command for command in commands)
            )
            self.assertFalse(
                any("export_strategy_dashboard_data.py" in command for command in commands)
            )
            pack_command = next(command for command in commands if "build_basic_data_report_packs.py" in command)
            self.assertIn("--minimal-publish-only", pack_command)
            self.assertIn("--skip-data-audit", pack_command)
            self.assertIn("--skip-fund-enrichment", pack_command)
            self.assertEqual(
                pack_command[pack_command.index("--db-path") + 1],
                str(bridge.database_root / "analysis_zh_current.sqlite"),
            )
            manifest_command = next(
                command for command in commands if "write_analysis_platform_deploy_manifest.py" in command
            )
            self.assertEqual(manifest_command[-1], "minimal_publish")

    def test_full_report_scope_is_no_longer_supported(self) -> None:
        with self.assertRaisesRegex(ValueError, "only minimal_publish"):
            configured_report_scope({"reports": {"dailyScope": "full"}})

    def test_report_scope_rejects_unknown_policy_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported"):
            configured_report_scope({"reports": {"dailyScope": "everything"}})

    def test_minimal_data_audit_does_not_promote_full_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "temp" / "minimal_report_source" / "run"
            staging.mkdir(parents=True)
            (staging / "deployment_manifest.json").write_text(
                json.dumps({"pageSet": "basic_data"}),
                encoding="utf-8",
            )
            formal = root / "formal_report"
            formal.mkdir()
            (formal / "marker.txt").write_text("unchanged", encoding="utf-8")
            bridge = Bridge.__new__(Bridge)
            bridge.args = SimpleNamespace(dry_run=False)
            bridge.report_root = formal
            bridge.node_run_dir = root / "node"
            bridge.policy = {
                "reports": {"dailyScope": "minimal_publish"},
                "dailyAudit": {"runStaticChecks": False},
            }
            bridge.child_run_id = "run"
            bridge.artifacts = []
            bridge.counters = {}
            bridge.warnings = []
            bridge.context_updates = {}
            commands: list[list[str]] = []
            bridge.python_command = lambda script, *args: ["python", script, *args]

            def fake_run(command: list[str]) -> int:
                commands.append(command)
                summary = bridge.node_run_dir / "audit" / "2026-07-24" / "run" / "hook_summary.json"
                summary.parent.mkdir(parents=True)
                summary.write_text(
                    json.dumps(
                        {
                            "status": "warn",
                            "staticErrorCount": 0,
                            "issues": [{"severity": "warn"}],
                        }
                    ),
                    encoding="utf-8",
                )
                return 0

            bridge.run_command = fake_run
            with patch.dict(
                os.environ,
                {
                    "ADVISOR_REPORT_STAGING_ROOT": str(staging),
                    "ADVISOR_REPORT_SCOPE": "minimal_publish",
                },
            ):
                self.assertEqual(bridge.data_audit(), 0)

            self.assertEqual((formal / "marker.txt").read_text(encoding="utf-8"), "unchanged")
            self.assertTrue(staging.exists())
            self.assertEqual(
                bridge.context_updates["ADVISOR_MINIMAL_REPORT_SOURCE_ROOT"],
                str(staging),
            )
            self.assertEqual(bridge.context_updates["ADVISOR_REPORT_PROMOTED"], "0")
            self.assertIn("--skip-static", commands[0])

    def test_minimal_data_audit_dry_run_forwards_source_context(self) -> None:
        bridge = Bridge.__new__(Bridge)
        bridge.args = SimpleNamespace(dry_run=True)
        bridge.report_root = Path("formal")
        bridge.node_run_dir = Path("node")
        bridge.policy = {
            "reports": {"dailyScope": "minimal_publish"},
            "dailyAudit": {"runStaticChecks": False},
        }
        bridge.context_updates = {}
        bridge.python_command = lambda script, *args: ["python", script, *args]
        bridge.run_command = lambda _command: 0
        with patch.dict(
            os.environ,
            {
                "ADVISOR_REPORT_STAGING_ROOT": "temp/minimal/run",
                "ADVISOR_REPORT_SCOPE": "minimal_publish",
            },
        ):
            self.assertEqual(bridge.data_audit(), 0)

        self.assertEqual(
            bridge.context_updates["ADVISOR_MINIMAL_REPORT_SOURCE_ROOT"],
            "temp/minimal/run",
        )
        self.assertEqual(bridge.context_updates["ADVISOR_REPORT_PROMOTED"], "0")

    def test_publish_uses_audited_temp_source_and_cleans_it_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "temp" / "minimal_report_source" / "run"
            source.mkdir(parents=True)
            publish = root / "publish"
            publish.mkdir()
            (publish / "package_validation.json").write_text(
                json.dumps(
                    {
                        "status": "ready",
                        "checks": {
                            "activeCurrentHoldingRankMissingReferenceCount": 5,
                        },
                        "policy": {
                            "warningOnlyChecks": [
                                "activeCurrentHoldingRankMissingReferenceCount",
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            (publish / "deployment_manifest.json").write_text(
                json.dumps({"pageSet": ["策略列表"]}),
                encoding="utf-8",
            )
            bridge = Bridge.__new__(Bridge)
            bridge.args = SimpleNamespace(dry_run=False)
            bridge.workspace_root = root
            bridge.policy = {"reports": {"dailyScope": "minimal_publish"}}
            bridge.temp_root = root / "temp"
            bridge.report_root = root / "formal"
            bridge.publish_root = publish
            bridge.code_root = root / "code"
            bridge.node_run_dir = root / "node"
            bridge.artifacts = []
            bridge.counters = {}
            bridge.warnings = []
            bridge.context_updates = {}
            commands: list[list[str]] = []
            bridge.program = lambda _name: root / "publisher.ps1"
            bridge.run_command = lambda command: commands.append(command) or 0

            with patch.dict(
                os.environ,
                {
                    "ADVISOR_REPORT_SCOPE": "minimal_publish",
                    "ADVISOR_MINIMAL_REPORT_SOURCE_ROOT": str(source),
                },
            ):
                self.assertEqual(bridge.publish(), 0)

            self.assertFalse(source.exists())
            self.assertIn(str(source), commands[0])
            self.assertIn("-SkipPagesVerify", commands[0])
            self.assertEqual(
                bridge.counters["activeCurrentHoldingRankMissingReferenceCount"],
                5,
            )
            self.assertTrue(bridge.warnings)

    def test_minimal_report_source_cleanup_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "minimal_report_source"
            root.mkdir()
            current = root / "current"
            current.mkdir()
            now = time.time()
            old_paths = []
            for index in range(3):
                path = root / f"failed-{index}"
                path.mkdir()
                (path / "data.bin").write_bytes(b"x" * (index + 1))
                timestamp = now - (index + 1) * 86400
                os.utime(path, (timestamp, timestamp))
                old_paths.append(path)

            result = cleanup_minimal_report_sources(
                root,
                current,
                retention_days=30,
                retain_failed_runs=2,
            )

            self.assertEqual(len(result["removed"]), 1)
            self.assertFalse(old_paths[2].exists())
            self.assertTrue(old_paths[0].exists())
            self.assertTrue(old_paths[1].exists())
            self.assertTrue(current.exists())

    def test_ttfund_required_collection_without_exact_batch_is_channel_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = "parent__ttfund_incremental__attempt_01"
            summary_path = (
                root
                / "raw"
                / "ttfund"
                / "incremental_update_runs"
                / "2026-07-23"
                / run_id
                / "summary.json"
            )
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                json.dumps(
                    {
                        "state": "completed",
                        "should_collect": True,
                        "collect_run_id": "",
                    }
                ),
                encoding="utf-8",
            )
            bridge = Bridge.__new__(Bridge)
            bridge.args = SimpleNamespace(dry_run=False)
            bridge.raw_root = root / "raw"
            bridge.child_run_id = run_id
            bridge.program = lambda _name: root / "runner.ps1"
            bridge.run_command = lambda _command, env=None: 0
            bridge.artifacts = []
            bridge.context_updates = {}
            bridge.counters = {}
            bridge.warnings = []
            with patch.dict(os.environ, {"ADVISOR_DEVICE_ID": "test-device"}):
                self.assertEqual(bridge.ttfund_incremental(), 3)

    def test_previous_gffunds_inventory_ignores_metadata_and_performance_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "collection_summary" / "2026-07-23"
            root.mkdir(parents=True)
            current = root / "current.json"
            current.write_text("{}", encoding="utf-8")
            complete = root / "complete.json"
            complete.write_text(
                json.dumps(
                    {
                        "channel_id": "gffunds",
                        "collection_status": "success",
                        "strategy_total": 140,
                        "yield_ok": 140,
                        "rebalance_ok": 140,
                        "latest_snapshot_non_empty": 140,
                    }
                ),
                encoding="utf-8",
            )
            performance = root / "performance.json"
            performance.write_text(
                json.dumps(
                    {
                        "channel_id": "gffunds",
                        "collector": "update_gffunds_performance_curves",
                        "strategy_total": 66,
                    }
                ),
                encoding="utf-8",
            )
            metadata = root / "metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "channel_id": "gffunds",
                        "collector": "update_gffunds_strategy_metadata",
                        "strategy_total": 66,
                    }
                ),
                encoding="utf-8",
            )
            now = time.time()
            os.utime(complete, (now - 30, now - 30))
            os.utime(performance, (now - 20, now - 20))
            os.utime(metadata, (now - 10, now - 10))

            self.assertEqual(
                previous_gffunds_collection_total(root.parent, current),
                140,
            )

    def test_gffunds_core_coverage_requires_strategy_level_ratios(self) -> None:
        ratios, failures = assess_gffunds_core_coverage(
            {
                "strategy_total": 100,
                "yield_non_empty": 96,
                "rebalance_ok": 95,
                "latest_snapshot_non_empty": 94,
            },
            0.95,
        )
        self.assertEqual(failures, ["latestHoldingStrategyRatio"])
        self.assertEqual(ratios["performanceStrategyRatio"], 0.96)
        self.assertEqual(ratios["rebalanceResponseRatio"], 0.95)

    def test_gffunds_core_coverage_excludes_metadata_only_profit_issues(self) -> None:
        ratios, failures = assess_gffunds_core_coverage(
            {
                "strategy_total": 141,
                "core_strategy_total": 66,
                "profit_issue_strategy_total": 75,
                "yield_non_empty": 66,
                "rebalance_ok": 66,
                "latest_snapshot_non_empty": 66,
            },
            0.95,
        )
        self.assertEqual(failures, [])
        self.assertEqual(ratios["performanceStrategyRatio"], 1.0)
        self.assertEqual(ratios["latestHoldingStrategyRatio"], 1.0)

    def test_report_promotion_replaces_only_after_staging_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report"
            staging = root / ".report.staging.run"
            report.mkdir()
            staging.mkdir()
            (report / "version.txt").write_text("old", encoding="utf-8")
            (staging / "version.txt").write_text("new", encoding="utf-8")

            result = promote_report_directory(staging, report, "run")

            self.assertEqual(result["status"], "promoted")
            self.assertEqual((report / "version.txt").read_text(encoding="utf-8"), "new")
            self.assertFalse(staging.exists())
            self.assertFalse(root.joinpath(".report.previous.run").exists())

    def test_report_promotion_rolls_back_when_second_rename_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report"
            staging = root / ".report.staging.run"
            report.mkdir()
            staging.mkdir()
            (report / "version.txt").write_text("old", encoding="utf-8")
            (staging / "version.txt").write_text("new", encoding="utf-8")
            real_replace = os.replace

            def controlled_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                if Path(source).resolve() == staging.resolve() and Path(target).resolve() == report.resolve():
                    raise PermissionError("injected promotion failure")
                real_replace(source, target)

            with patch("bridge_node.os.replace", side_effect=controlled_replace):
                with self.assertRaisesRegex(PermissionError, "injected"):
                    promote_report_directory(staging, report, "run")

            self.assertEqual((report / "version.txt").read_text(encoding="utf-8"), "old")
            self.assertEqual((staging / "version.txt").read_text(encoding="utf-8"), "new")

    def test_log_cleanup_uses_longer_retention_for_failures_and_protects_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log_root = root / "logs"
            lock_root = root / "locks"
            day = log_root / "daily_update" / "2026-01-01"
            day.mkdir(parents=True)
            lock_root.mkdir()
            now = time.time()

            def create_run(run_id: str, status: str, age_days: int) -> Path:
                run = day / run_id
                run.mkdir()
                (run / "summary.json").write_text(
                    json.dumps({"status": status}),
                    encoding="utf-8",
                )
                (run / "console.log").write_text("x" * 10, encoding="utf-8")
                timestamp = now - age_days * 86400
                os.utime(run, (timestamp, timestamp))
                return run

            old_success = create_run("old-success", "success", 31)
            recent_failure = create_run("recent-failure", "failed_critical", 60)
            old_failure = create_run("old-failure", "failed_critical", 91)
            active = create_run("active-run", "success", 100)
            launcher_root = log_root / "launcher"
            launcher_root.mkdir()
            old_launcher = launcher_root / "old.log"
            old_launcher.write_text("launcher", encoding="utf-8")
            recent_launcher = launcher_root / "recent.log"
            recent_launcher.write_text("launcher", encoding="utf-8")
            os.utime(old_launcher, (now - 31 * 86400, now - 31 * 86400))
            (lock_root / "daily_update.lock").write_text(
                json.dumps({"runId": "active-run"}),
                encoding="utf-8",
            )
            policy = {
                "logs": {
                    "successfulRunRetentionDays": 30,
                    "failedOrUnfinishedRunRetentionDays": 90,
                    "launcherRetentionDays": 30,
                }
            }

            preview = cleanup_daily_logs(
                log_root,
                lock_root,
                "current-run",
                policy,
                dry_run=True,
            )
            self.assertEqual({Path(item["path"]).name for item in preview["candidates"]}, {"old-success", "old-failure"})
            self.assertEqual(
                {Path(item["path"]).name for item in preview["launcherCandidates"]},
                {"old.log"},
            )
            self.assertTrue(old_success.exists())

            cleanup_daily_logs(log_root, lock_root, "current-run", policy)
            self.assertFalse(old_success.exists())
            self.assertFalse(old_failure.exists())
            self.assertTrue(recent_failure.exists())
            self.assertTrue(active.exists())
            self.assertFalse(old_launcher.exists())
            self.assertTrue(recent_launcher.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
