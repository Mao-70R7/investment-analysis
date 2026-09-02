from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "_共享组件"
    / "生产程序"
    / "build_minimal_publish_set.py"
)
SPEC = importlib.util.spec_from_file_location("build_minimal_publish_set", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MinimalPublishValidationPolicyTests(unittest.TestCase):
    def test_publisher_forbids_monthly_content_instead_of_requiring_it(self) -> None:
        publisher = (SCRIPT_PATH.parent / "update_and_publish_minimal_set.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertNotIn("MonthlyReportPageName", publisher)
        self.assertNotIn("Static monthly rebalance report is missing", publisher)
        self.assertIn("Monthly report content is forbidden", publisher)
        self.assertIn("Monthly report content is still referenced by the deployment manifest", publisher)
        self.assertIn('"$base/basic_data/institutions.html"', publisher)

    def test_edgeone_snapshot_branch_is_root_only_and_race_safe(self) -> None:
        publisher = (SCRIPT_PATH.parent / "update_and_publish_minimal_set.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn('[string]$EdgeOneRepositoryUrl = ""', publisher)
        self.assertIn('[string]$EdgeOneRepositoryBranch = "main"', publisher)
        self.assertIn('[string]$EdgeOneSnapshotBranch = ""', publisher)
        self.assertIn('"commit-tree", $sourceTree', publisher)
        self.assertIn('"rev-list", "--parents", "-n", "1", $snapshotCommit', publisher)
        self.assertIn('snapshot commit must be a root commit', publisher)
        self.assertIn('snapshot tree does not match the normal publish tree', publisher)
        self.assertIn("--force-with-lease=refs/heads/${branchValue}", publisher)
        self.assertIn("EdgeOne repository must be separate from the normal publish repository", publisher)
        self.assertIn('"http.version=HTTP/1.1"', publisher)
        self.assertIn('"http.postBuffer=524288000"', publisher)
        self.assertIn('"push", $remoteValue, $lease', publisher)
        self.assertIn("$maxSnapshotPushAttempts = 3", publisher)
        self.assertIn("refreshing the remote watermark before retry", publisher)
        self.assertIn("[switch]$UseIsolatedGitDirectory", publisher)
        self.assertIn('Join-Path $script:RunDir "edgeone_snapshot.git"', publisher)
        self.assertIn('"init", "--bare", $isolatedGitDirectory', publisher)
        self.assertIn('"--git-dir=$isolatedGitDirectory"', publisher)
        self.assertIn('Join-Path $alternateInfoDirectory "alternates"', publisher)
        self.assertIn('$sourceObjectDirectory.Replace(\'\\\', \'/\') + "`n"', publisher)
        self.assertIn("New-Object System.Text.UTF8Encoding($false)", publisher)
        self.assertIn("-UseIsolatedGitDirectory", publisher)
        self.assertIn("Publish-EdgeOneSnapshots", publisher)
        self.assertIn("$script:PublishedEdgeOneSnapshotBranch = $null", publisher)
        self.assertNotIn("$script:EdgeOneSnapshotBranch = $null", publisher)
        self.assertNotIn('push", "origin", "--force", "main', publisher)

    def test_existing_package_reuse_cannot_bypass_update_and_validation(self) -> None:
        publisher = (SCRIPT_PATH.parent / "update_and_publish_minimal_set.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn("[switch]$ReuseExistingValidatedPackage", publisher)
        self.assertIn("ReuseExistingValidatedPackage requires SkipDataUpdate", publisher)
        self.assertIn("package validation and smoke test remain mandatory", publisher)
        self.assertIn('Invoke-Step -Name "3. Validate Minimal Package"', publisher)
        self.assertIn('Invoke-Step -Name "4. Local HTTP Smoke Test"', publisher)

    def test_daily_dispatch_passes_dedicated_and_legacy_edgeone_targets(self) -> None:
        node_root = SCRIPT_PATH.parents[2]
        bridge = (node_root / "00_调度框架" / "bridge_node.py").read_text(encoding="utf-8-sig")
        compatibility = (SCRIPT_PATH.parent / "daily_update_orchestrator.py").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn('runtime_config.get("edgeOnePublishRemote")', bridge)
        self.assertIn('runtime_config.get("edgeOnePublishBranch")', bridge)
        self.assertIn('runtime_config.get("edgeOneLegacySnapshotBranch", "")', bridge)
        self.assertIn('"-EdgeOneRepositoryUrl"', bridge)
        self.assertIn('"-EdgeOneRepositoryBranch"', bridge)
        self.assertIn('"-EdgeOneSnapshotBranch"', bridge)
        self.assertIn('default=os.environ.get("ADVISOR_EDGEONE_LEGACY_SNAPSHOT_BRANCH", "")', compatibility)
        self.assertIn('"-EdgeOneRepositoryUrl"', compatibility)
        self.assertIn('"-EdgeOneSnapshotBranch"', compatibility)

    def test_strategy_filter_business_facts_require_explicit_yes_no_values(self) -> None:
        valid = {
            "strategies": [
                {"有基准": "是", "有业绩走势": "是", "有历史仓位": "是", "对客未终止": "是"},
                {"有基准": "否", "有业绩走势": "是", "有历史仓位": "否", "对客未终止": "是"},
            ]
        }
        self.assertEqual(MODULE.validate_strategy_filter_facts(valid), 1)

        invalid = {"strategies": [{"有基准": "是", "有业绩走势": "是"}]}
        with self.assertRaisesRegex(RuntimeError, "business completeness facts"):
            MODULE.validate_strategy_filter_facts(invalid)

    def test_strategy_filter_business_facts_reject_silent_empty_default_scope(self) -> None:
        rows = [
            {"有基准": "否", "有业绩走势": "是", "有历史仓位": "是", "对客未终止": "是"}
        ]
        with self.assertRaisesRegex(RuntimeError, "default scope would be empty"):
            MODULE.validate_strategy_filter_facts({"strategies": rows})

    def test_strategy_filter_default_scope_does_not_require_history_positions(self) -> None:
        rows = [
            {"有基准": "是", "有业绩走势": "是", "有历史仓位": "否", "对客未终止": "是"}
        ]
        self.assertEqual(MODULE.validate_strategy_filter_facts({"strategies": rows}), 1)

    def test_preview_runtime_is_kept_outside_generated_package(self) -> None:
        script_root = SCRIPT_PATH.parent
        start_script = (script_root / "start_minimal_publish.ps1").read_text(encoding="utf-8-sig")
        stop_script = (script_root / "stop_minimal_publish.ps1").read_text(encoding="utf-8-sig")

        self.assertIn('Join-Path $packageParent ".minimal_publish_runtime"', start_script)
        self.assertIn("-WorkingDirectory $runtimeDir", start_script)
        self.assertIn('Join-Path $packageParent ".minimal_publish_runtime\\processes.json"', stop_script)
        self.assertIn("$legacyRuntimeState", stop_script)

    def test_institution_overview_replaces_monthly_report_in_public_pages(self) -> None:
        self.assertIn("institutions.html", MODULE.PUBLIC_PAGES)
        self.assertEqual(MODULE.PUBLIC_PAGES[0], "institutions.html")
        self.assertEqual(MODULE.PAGE_RENDERERS["institutions.html"], "institutions.js")
        self.assertEqual(
            MODULE.PAGE_PACK_REPLACEMENTS["institutions.html"],
            {"basic_summary_core.js": "institution_overview_pack.js"},
        )
        self.assertEqual(MODULE.STATIC_REPORT_PAGES, ())
        self.assertEqual(MODULE.STATIC_REPORT_ASSET_DIRECTORIES, ())

    def test_compare_workflow_validation_blocks_missing_page_and_eager_full_packs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            basic_root = Path(temporary)
            assets = basic_root / "assets"
            assets.mkdir(parents=True)
            (assets / "ai-strategy.js").write_text(
                'return `./insights.html?tab=compare&compare=${ids}`;\n',
                encoding="utf-8",
            )
            (basic_root / "compare.html").write_text(
                '<script>startPage({"dataScripts":["./data/basic_summary_core.js",'
                '"./data/holding_snapshot_pack.js"]})</script>\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "published compare.html"):
                MODULE.validate_compare_workflow(basic_root)

    def test_compare_workflow_validation_accepts_published_route_and_lazy_packs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            basic_root = Path(temporary)
            assets = basic_root / "assets"
            assets.mkdir(parents=True)
            (assets / "ai-strategy.js").write_text(
                'return `./compare.html?compare=${ids}`;\n',
                encoding="utf-8",
            )
            (basic_root / "compare.html").write_text(
                '<script>startPage({"dataScripts":["./data/strategy_detail_index_pack.js"]})</script>\n',
                encoding="utf-8",
            )

            MODULE.validate_compare_workflow(basic_root)

    def test_strategy_compare_is_not_a_primary_menu_but_route_remains(self) -> None:
        nav = MODULE.minimal_nav("compare.html")
        self.assertNotIn('href="./compare.html"', nav)
        self.assertIn('href="./strategies.html"', nav)
        self.assertNotIn('class="nav-link is-active" href="./strategies.html"', nav)
        self.assertIn("compare.html", MODULE.PUBLIC_PAGES)
        self.assertNotIn("compare.html", MODULE.ACTIVE_PAGE)

    def test_compare_page_uses_dedicated_index_and_lazy_holding_pack(self) -> None:
        self.assertEqual(
            MODULE.PAGE_PACK_REPLACEMENTS["compare.html"],
            {
                "basic_summary_core.js": "strategy_detail_index_pack.js",
                "holding_snapshot_pack.js": None,
            },
        )

    def test_missing_peer_rank_is_warning_only(self) -> None:
        blocking, warnings = MODULE.classify_validation_checks(
            {"activeCurrentHoldingRankMissingReferenceCount": 5}
        )

        self.assertEqual(blocking, {})
        self.assertEqual(
            warnings,
            {"activeCurrentHoldingRankMissingReferenceCount": 5},
        )

    def test_broken_page_data_remains_blocking(self) -> None:
        blocking, warnings = MODULE.classify_validation_checks(
            {
                "strategyDetailMissingCount": 1,
                "currentHoldingScaleErrorReferenceCount": 2,
                "officialPerformanceImageMissingPublishedAssetCount": 3,
            }
        )

        self.assertEqual(
            blocking,
            {
                "strategyDetailMissingCount": 1,
                "currentHoldingScaleErrorReferenceCount": 2,
                "officialPerformanceImageMissingPublishedAssetCount": 3,
            },
        )
        self.assertEqual(warnings, {})

    def test_detail_path_uses_same_safe_stem_as_exported_detail_file(self) -> None:
        strategy_id = "gfsec_fima__regular:gffund:GFAXHB"
        payload = {"strategies": [{"统一策略ID": strategy_id}]}

        result = MODULE.rewrite_strategy_detail_paths(payload, "test-version")

        safe_stem = "gfsec_fima__regular_gffund_GFAXHB"
        self.assertEqual(
            result["strategies"][0]["detailFile"],
            f"./data/details/{MODULE.shard_key(safe_stem)}/{safe_stem}.js?v=test-version",
        )

    def test_official_performance_image_reference_requires_a_real_source_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            details = source / "data" / "details"
            details.mkdir(parents=True)
            payload = {
                "id": "gfbank_cgb__sample",
                "summary": {},
                "officialPerformanceImage": {
                    "url": "./assets/gfbank-performance/gfbank_cgb__sample.webp",
                },
            }
            (details / "gfbank_cgb__sample.js").write_text(
                "window.__BASIC_DATA__.details['gfbank_cgb__sample'] = "
                + json.dumps(payload, ensure_ascii=False)
                + ";\n",
                encoding="utf-8",
            )

            _, _, image_paths, stats, errors = MODULE.strategy_detail_inventory(source)

            self.assertEqual(errors, [])
            self.assertEqual(
                image_paths,
                {"assets/gfbank-performance/gfbank_cgb__sample.webp"},
            )
            self.assertEqual(stats["officialPerformanceImageReferenceCount"], 1)
            self.assertEqual(stats["officialPerformanceImageMissingSourceAssetCount"], 1)

            image = source / "assets" / "gfbank-performance" / "gfbank_cgb__sample.webp"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"verified-image")

            _, _, image_paths, stats, errors = MODULE.strategy_detail_inventory(source)

            self.assertEqual(errors, [])
            self.assertEqual(len(image_paths), 1)
            self.assertEqual(stats["officialPerformanceImageMissingSourceAssetCount"], 0)

    def test_registered_unmanaged_publish_app_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root / "existing"
            staging = root / "staging"
            app = existing / "basic_data" / "advisor_quota_workbench"
            app.mkdir(parents=True)
            (app / "index.html").write_text("verified prototype", encoding="utf-8")

            stats = MODULE.copy_preserved_publish_paths(existing, staging)

            self.assertEqual(stats["pathCount"], 1)
            self.assertEqual(stats["fileCount"], 1)
            self.assertEqual(
                (staging / "basic_data" / "advisor_quota_workbench" / "index.html").read_text(
                    encoding="utf-8"
                ),
                "verified prototype",
            )


if __name__ == "__main__":
    unittest.main()
