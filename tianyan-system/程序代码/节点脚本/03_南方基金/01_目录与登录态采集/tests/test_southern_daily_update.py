from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "southern_daily_update.py"
SPEC = importlib.util.spec_from_file_location("southern_daily_update_test", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SouthernDailyUpdateTest(unittest.TestCase):
    def test_benchmark_formula_removes_disclaimer(self) -> None:
        raw = "1、上证国债*20%+沪深300*80%；<br/>2、组合业绩未包括投顾服务费。"
        self.assertEqual("上证国债*20%+沪深300*80%", MODULE.benchmark_formula(raw))

    def test_non_formula_disclaimer_is_not_a_benchmark(self) -> None:
        self.assertIsNone(MODULE.benchmark_formula("组合业绩未包括投顾服务费。<br/>"))

    def test_known_index_mapping_covers_risk_assets(self) -> None:
        self.assertEqual(("沪深300", "权益"), MODULE.INDEX_META["000300"])
        self.assertEqual(("大宗商品全收益", "商品"), MODULE.INDEX_META["H00979"])


if __name__ == "__main__":
    unittest.main()
