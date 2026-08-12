from __future__ import annotations

import sys
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from build_qieman_catalog_coverage import build_rows, exact_benchmark, summarize  # noqa: E402


class QiemanCatalogCoverageTest(unittest.TestCase):
    def test_blank_benchmark_label_is_not_exact(self) -> None:
        self.assertIsNone(exact_benchmark(["业绩基准："]))
        self.assertEqual("沪深300指数", exact_benchmark(["业绩基准：沪深300指数"]))

    def test_summary_is_not_upgraded_to_daily_series_or_weight(self) -> None:
        catalog = {
            "mappings": [
                {
                    "strategy_name": "示例策略",
                    "source_strategy_id": "ZH000001",
                    "status": "mapped",
                    "detail_text_sample": ["累计收益", "年化收益", "最大回撤", "业绩基准：沪深300指数"],
                }
            ]
        }
        rows = build_rows(catalog, {"rows": []}, [], {"unique_fund_code_count": 0})
        self.assertTrue(rows[0]["performance_summary_available"])
        self.assertFalse(rows[0]["performance_daily_series_complete"])
        self.assertTrue(rows[0]["exact_benchmark_available"])
        self.assertFalse(rows[0]["holding_fund_weight_complete"])
        self.assertFalse(rows[0]["all_three_strictly_complete"])

    def test_strict_counts_keep_weights_at_zero(self) -> None:
        rows = [
            {
                "route_mapped": True,
                "performance_summary_available": True,
                "performance_daily_series_complete": False,
                "exact_benchmark_available": True,
                "holding_constituent_list_confirmed": True,
                "holding_fund_weight_complete": False,
                "all_three_strictly_complete": False,
            }
        ]
        result = summarize(rows, {"protected_endpoint_probe_count": 24, "protected_endpoint_nonempty_count": 0})
        self.assertEqual(0, result["performance_daily_series_complete_count"])
        self.assertEqual(0, result["holding_fund_weight_complete_count"])
        self.assertEqual(0, result["all_three_strictly_complete_count"])


if __name__ == "__main__":
    unittest.main()
