from __future__ import annotations

import sys
import json
import unittest
from pathlib import Path


PROGRAM_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "生产程序").is_dir())
CODE_ROOT = PROGRAM_ROOT.parents[1]
sys.path.insert(0, str(PROGRAM_ROOT / "生产程序"))

from business_naming import (  # noqa: E402
    canonical_advisor_institution,
    canonical_business_channel,
    is_undisclosed_institution,
)


class BusinessNamingTests(unittest.TestCase):
    def test_gfsec_sources_share_one_business_channel(self) -> None:
        self.assertEqual(canonical_business_channel("gfsec_fima", "广发证券易淘金/财富管家"), "广发证券")
        self.assertEqual(canonical_business_channel("gfsec_robot", "广发证券易淘金/贝塔牛理财"), "广发证券")

    def test_gffunds_aliases_share_one_name(self) -> None:
        for value in (
            "广发基金-广发投顾",
            "广发基金投顾",
            "广发基金有限公司",
            "广发基金管理有限公司",
        ):
            with self.subTest(value=value):
                self.assertEqual(canonical_advisor_institution(value), "广发基金")
                self.assertEqual(canonical_business_channel("", value), "广发基金")

    def test_unrelated_names_are_unchanged(self) -> None:
        self.assertEqual(canonical_business_channel("ttfund", "天天基金/投顾"), "天天基金/投顾")
        self.assertEqual(canonical_advisor_institution("易方达基金"), "易方达基金")

    def test_qieman_business_channel_is_yingmi_fund(self) -> None:
        self.assertEqual(canonical_business_channel("qieman", "且慢/盈米基金"), "盈米基金")
        self.assertEqual(canonical_business_channel("", "且慢"), "盈米基金")
        self.assertEqual(canonical_advisor_institution("且慢"), "盈米基金")

    def test_undisclosed_manager_falls_back_to_own_channel(self) -> None:
        for value in (None, "", "未披露", "未知机构", "未识别机构", "--"):
            with self.subTest(value=value):
                self.assertTrue(is_undisclosed_institution(value))
                self.assertEqual(
                    canonical_advisor_institution(value, "qieman", "且慢/盈米基金"),
                    "盈米基金",
                )
        self.assertEqual(
            canonical_advisor_institution("未披露", "gfsec_fima", "广发证券易淘金/财富管家"),
            "广发证券",
        )
        self.assertEqual(
            canonical_advisor_institution("未披露", "ttfund", "天天基金/投顾"),
            "天天基金/投顾",
        )
        self.assertEqual(canonical_advisor_institution("未披露", "ttfund"), "天天基金/投顾")
        self.assertEqual(canonical_advisor_institution("未知机构", "southern"), "南方基金")

    def test_disclosed_third_party_manager_is_preserved(self) -> None:
        self.assertEqual(
            canonical_advisor_institution("银华基金", "qieman", "且慢/盈米基金"),
            "银华基金",
        )

    def test_active_channel_fallback_config_matches_canonical_naming(self) -> None:
        rules = json.loads((CODE_ROOT / "config" / "系统字段检查规则.json").read_text(encoding="utf-8"))
        scope = rules["pageStrategyScope"]
        fallback = scope["managerFallbackBySourceId"]
        for source_id in scope["activeChannelIds"]:
            with self.subTest(source_id=source_id):
                self.assertIn(source_id, fallback)
                self.assertEqual(canonical_advisor_institution("未披露", source_id), fallback[source_id])


if __name__ == "__main__":
    unittest.main()
