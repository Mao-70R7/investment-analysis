from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROGRAM_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "生产程序").is_dir())
sys.path.insert(0, str(PROGRAM_ROOT / "生产程序"))

from business_naming import canonical_advisor_institution, canonical_business_channel  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
