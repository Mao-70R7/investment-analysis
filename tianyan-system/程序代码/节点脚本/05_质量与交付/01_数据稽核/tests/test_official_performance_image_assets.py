from __future__ import annotations

import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


AUDIT_PATH = next(
    parent / "_共享组件" / "生产程序" / "标准化数据稽核.py"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "生产程序" / "标准化数据稽核.py").is_file()
)
SPEC = importlib.util.spec_from_file_location("standard_data_audit", AUDIT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OfficialPerformanceImageAssetTests(unittest.TestCase):
    def write_detail(self, site: Path) -> None:
        detail = site / "data" / "details" / "aa" / "gfbank_cgb__sample.js.gz"
        detail.parent.mkdir(parents=True)
        payload = {
            "id": "gfbank_cgb__sample",
            "officialPerformanceImage": {
                "url": "./assets/gfbank-performance/gfbank_cgb__sample.webp",
            },
        }
        with gzip.open(detail, "wt", encoding="utf-8") as handle:
            handle.write(
                "window.__BASIC_DATA__.details['gfbank_cgb__sample'] = "
                + json.dumps(payload, ensure_ascii=False)
                + ";\n"
            )

    def test_screenshot_reference_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            self.write_detail(site)
            issues: list[dict[str, object]] = []

            MODULE.audit_official_performance_image_assets(issues, site)

            self.assertEqual(len(issues), 1)
            self.assertEqual(
                issues[0]["ruleId"],
                "PAGE_PERFORMANCE_SCREENSHOT_AS_CURVE",
            )
            self.assertEqual(issues[0]["severity"], "error")

    def test_existing_referenced_image_is_still_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            self.write_detail(site)
            image = site / "assets" / "gfbank-performance" / "gfbank_cgb__sample.webp"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"verified-image")
            issues: list[dict[str, object]] = []

            MODULE.audit_official_performance_image_assets(issues, site)

            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["ruleId"], "PAGE_PERFORMANCE_SCREENSHOT_AS_CURVE")

    def test_page_without_screenshot_reference_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            detail = site / "data" / "details" / "aa" / "gfbank_cgb__sample.js.gz"
            detail.parent.mkdir(parents=True)
            with gzip.open(detail, "wt", encoding="utf-8") as handle:
                handle.write(
                    "window.__BASIC_DATA__.details['gfbank_cgb__sample'] = "
                    + json.dumps({"id": "gfbank_cgb__sample"}, ensure_ascii=False)
                    + ";\n"
                )
            issues: list[dict[str, object]] = []

            MODULE.audit_official_performance_image_assets(issues, site)

            self.assertEqual(issues, [])

    def test_single_point_derived_zero_interval_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            detail = site / "data" / "details" / "gfbank_cgb__sample.js.gz"
            detail.parent.mkdir(parents=True)
            payload = {
                "id": "gfbank_cgb__sample",
                "curves": {
                    "披露业绩": {"points": [{"日期": "2026-07-29", "数值": 1.085}]},
                    "基准业绩": {"points": []},
                    "沪深300业绩": {"points": [{"日期": "2026-07-29", "数值": 4600.26}]},
                },
                "intervalMatrix": [
                    {"口径": "披露业绩", "今年以来": 0},
                    {"口径": "基准业绩", "今年以来": None},
                    {"口径": "沪深300业绩", "今年以来": 0},
                ],
            }
            with gzip.open(detail, "wt", encoding="utf-8") as handle:
                handle.write(
                    "window.__BASIC_DATA__.details['gfbank_cgb__sample'] = "
                    + json.dumps(payload, ensure_ascii=False)
                    + ";\n"
                )
            issues: list[dict[str, object]] = []

            MODULE.audit_single_point_interval_returns(issues, site)

            self.assertEqual(len(issues), 1)
            self.assertEqual(
                issues[0]["ruleId"],
                "PAGE_SINGLE_POINT_DERIVED_INTERVAL_RETURN",
            )

    def test_single_point_with_only_official_intervals_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            detail = site / "data" / "details" / "gfbank_cgb__sample.js.gz"
            detail.parent.mkdir(parents=True)
            payload = {
                "id": "gfbank_cgb__sample",
                "curves": {
                    "披露业绩": {"points": [{"日期": "2026-07-29", "数值": 1.085}]},
                    "基准业绩": {"points": []},
                    "沪深300业绩": {"points": [{"日期": "2026-07-29", "数值": 4600.26}]},
                },
                "intervalMatrix": [
                    {"口径": "披露业绩", "近一月": 0.35, "近6月": 0.6, "近1年": 2.11, "成立以来": 8.5, "今年以来": None},
                    {"口径": "基准业绩", "近一月": 0.08, "近6月": 0.99, "近1年": 0.42, "成立以来": 6.49, "今年以来": None},
                    {"口径": "沪深300业绩", "今年以来": None, "成立以来": None},
                ],
            }
            with gzip.open(detail, "wt", encoding="utf-8") as handle:
                handle.write(
                    "window.__BASIC_DATA__.details['gfbank_cgb__sample'] = "
                    + json.dumps(payload, ensure_ascii=False)
                    + ";\n"
                )
            issues: list[dict[str, object]] = []

            MODULE.audit_single_point_interval_returns(issues, site)

            self.assertEqual(issues, [])

    def test_minimal_runtime_without_cache_guard_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            assets = site / "assets"
            assets.mkdir(parents=True)
            (assets / "minimal-publish-runtime.js").write_text("window.MinimalPublish = {};\n", encoding="utf-8")
            (site / "strategy.html").write_text(
                '<script src="./assets/minimal-publish-runtime.js?v=test-build"></script>\n',
                encoding="utf-8",
            )
            issues: list[dict[str, object]] = []

            MODULE.audit_minimal_publish_cache_guard(issues, site)

            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["ruleId"], "PAGE_STALE_BUILD_CACHE_GUARD_MISSING")

    def test_project_minimal_runtime_has_cache_guard(self) -> None:
        source_site = AUDIT_PATH.parents[3] / "basic_data"
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            assets = site / "assets"
            assets.mkdir(parents=True)
            (assets / "minimal-publish-runtime.js").write_text(
                (source_site / "assets" / "minimal-publish-runtime.js").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (site / "strategy.html").write_text(
                '<script src="./assets/minimal-publish-runtime.js?v=test-build"></script>\n',
                encoding="utf-8",
            )
            issues: list[dict[str, object]] = []

            MODULE.audit_minimal_publish_cache_guard(issues, site)

            self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
