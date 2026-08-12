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
            {"有基准": "是", "有业绩走势": "是", "有历史仓位": "否", "对客未终止": "是"}
        ]
        with self.assertRaisesRegex(RuntimeError, "default scope would be empty"):
            MODULE.validate_strategy_filter_facts({"strategies": rows})

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

    def test_strategy_compare_is_a_strategy_list_subflow_not_a_primary_menu(self) -> None:
        nav = MODULE.minimal_nav("strategies.html")
        self.assertNotIn('href="./compare.html"', nav)
        self.assertIn('class="nav-link is-active" href="./strategies.html"', nav)
        self.assertEqual(MODULE.ACTIVE_PAGE["compare.html"], "strategies.html")

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
