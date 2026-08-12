from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

from benchmark_asset_classification import (  # noqa: E402
    compute_benchmark_asset_mix,
    effective_numbered_period_formula,
    load_benchmark_catalog,
)


class BenchmarkAssetClassificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_benchmark_catalog()

    def mix(self, formula: str) -> dict:
        return compute_benchmark_asset_mix(formula, self.catalog)

    def test_chinabond_is_bond_not_cash(self) -> None:
        result = self.mix("95%\u4e2d\u8bc1800\u6307\u6570+5%\u4e2d\u503a\u7efc\u5408\u5168\u4ef7(\u603b\u503c)\u6307\u6570")
        self.assertEqual(result["\u57fa\u51c6\u6743\u76ca\u5206\u7c7b\u6863"], "L10")
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u6743\u76ca"], 95.0)
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u503a\u5238"], 5.0)
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u73b0\u91d1"], 0.0)

    def test_weighted_equity_components_without_index_suffix_are_not_cash(self) -> None:
        cases = [
            ("95%\u4e2d\u8bc1\u79d1\u6280+5%\u4e2d\u503a\u7efc\u5408\u5168\u4ef7\uff08\u603b\u503c\uff09\u6307\u6570", "L10", 95.0, 5.0),
            ("5%\u4e2d\u503a\u7efc\u5408\u5168\u4ef7\uff08\u603b\u503c\uff09\u6307\u6570+95%\u4e2d\u8bc1800", "L10", 95.0, 5.0),
            ("59.00%\u6caa\u6df1300+41.00%\u4e2d\u503a-\u7efc\u5408\u5168\u4ef7(\u603b\u503c)\u6307\u6570", "L6", 59.0, 41.0),
            ("95%\u5185\u5730\u6d88\u8d39+5%\u4e2d\u503a\u7efc\u5408\u5168\u4ef7\uff08\u603b\u503c\uff09\u6307\u6570", "L10", 95.0, 5.0),
        ]
        for formula, bucket, equity, bond in cases:
            with self.subTest(formula=formula):
                result = self.mix(formula)
                self.assertEqual(result["\u57fa\u51c6\u6743\u76ca\u5206\u7c7b\u6863"], bucket)
                self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u6743\u76ca"], equity)
                self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u503a\u5238"], bond)
                self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u73b0\u91d1"], 0.0)

    def test_money_fund_index_is_cash(self) -> None:
        result = self.mix("95%\u4e2d\u8bc1500\u6307\u6570+5%\u4e2d\u8bc1\u8d27\u5e01\u578b\u57fa\u91d1\u6307\u6570")
        self.assertEqual(result["\u57fa\u51c6\u6743\u76ca\u5206\u7c7b\u6863"], "L10")
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u6743\u76ca"], 95.0)
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u73b0\u91d1"], 5.0)

    def test_dynamic_a_table_uses_current_disclosed_interval(self) -> None:
        formula = (
            "\u4e2d\u8bc1800\u6307\u6570\u6536\u76ca\u7387*A%+\u4e2d\u503a\u7efc\u5408\u5168\u4ef7(\u603b\u503c)\u6307\u6570\u6536\u76ca\u7387*(1-A)%_x000D_"
            "\u57fa\u91d1\u5408\u540c\u751f\u6548\u4e4b\u65e5\u81f32025\u5e7412\u670831\u65e5 50% 50%_x000D_"
            "2026\u5e741\u67081\u65e5\u81f32028\u5e7412\u670831\u65e5 48% 52%"
        )
        result = self.mix(formula)
        self.assertEqual(result["\u57fa\u51c6\u6743\u76ca\u5206\u7c7b\u6863"], "L5")
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u6743\u76ca"], 48.0)
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u503a\u5238"], 52.0)

    def test_dynamic_ab_table_uses_disclosed_pair(self) -> None:
        formula = (
            "\u6caa\u6df1300\u6307\u6570\u6536\u76ca\u7387*A%+\u4e2d\u8bc1\u7efc\u5408\u503a\u6307\u6570\u6536\u76ca\u7387*B%_x000D_"
            "2025/1/1 \u81f3 2025/12/31 12 88_x000D_"
            "\u76ee\u6807\u65e5\u671f(2025/12/31)\u4ee5\u540e 10 90"
        )
        result = self.mix(formula)
        self.assertEqual(result["\u57fa\u51c6\u6743\u76ca\u5206\u7c7b\u6863"], "L1")
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u6743\u76ca"], 10.0)
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u503a\u5238"], 90.0)

    def test_dynamic_x_with_fixed_cash_uses_residual_bond_weight(self) -> None:
        formula = (
            "X*\u6caa\u6df1300\u6307\u6570+(95%-X)*\u4e2d\u503a\u7efc\u5408\u5168\u4ef7\u6307\u6570+\u6d3b\u671f\u5b58\u6b3e\u5229\u7387*5%_x000D_"
            "2024-2025 45%_x000D_2026-2027 40%"
        )
        result = self.mix(formula)
        self.assertEqual(result["\u57fa\u51c6\u6743\u76ca\u5206\u7c7b\u6863"], "L4")
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u6743\u76ca"], 40.0)
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u503a\u5238"], 55.0)
        self.assertAlmostEqual(result["\u57fa\u51c6\u8d44\u4ea7\u5927\u7c7b-\u73b0\u91d1"], 5.0)

    def test_numbered_effective_period_selects_current_block(self) -> None:
        text = (
            "(1)\u57fa\u91d1\u5408\u540c\u751f\u6548\u65e5~2026.08.31_x000D_"
            "\u4e1a\u7ee9\u6bd4\u8f83\u57fa\u51c6=X*MSCI\u4e2d\u56fdA\u80a1\u5728\u5cb8\u6307\u6570+(1-X)*\u4e2d\u503a\u65b0\u7efc\u5408\u6307\u6570_x000D_"
            "2021/9/1-2026/8/31 10-40 20 80_x000D_"
            "(2)2026\u5e749\u67081\u65e5\u540e_x000D_\u4e1a\u7ee9\u6bd4\u8f83\u57fa\u51c6:\u4e2d\u503a\u65b0\u7efc\u5408\u6307\u6570"
        )
        selected = effective_numbered_period_formula(text, date(2026, 7, 15))
        self.assertIsNotNone(selected)
        self.assertIn("X*MSCI", selected[0])
        selected = effective_numbered_period_formula(text, date(2026, 9, 1))
        self.assertIsNotNone(selected)
        self.assertIn("\u4e2d\u503a\u65b0\u7efc\u5408", selected[0])


if __name__ == "__main__":
    unittest.main()
