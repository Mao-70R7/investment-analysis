from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULE_PATH = ROOT / "probe_qieman_public_api.py"
SPEC = importlib.util.spec_from_file_location("probe_qieman_public_api", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ProbeQiemanPublicApiTests(unittest.TestCase):
    def test_extract_portfolio_candidates_filters_non_portfolios(self) -> None:
        catalog = {
            "m4Items": [
                {
                    "group": "M4_STEADY",
                    "name": "稳钱",
                    "desc": "三年内",
                    "recommends": [
                        {"recCode": "ZH123456", "recName": "组合一"},
                        {"recCode": "J7", "recName": "简慢投资组合"},
                        {"recCode": "WALLET", "recName": "钱包"},
                    ],
                }
            ]
        }
        rows = MODULE.extract_portfolio_candidates(catalog)
        self.assertEqual([row["recCode"] for row in rows], ["J7", "ZH123456"])
        self.assertEqual(rows[0]["_group"]["group"], "M4_STEADY")

    def test_normalized_master_keeps_missing_benchmark_null(self) -> None:
        row = MODULE.normalized_master_candidate(
            {
                "recCode": "SI000001",
                "recName": "组合二",
                "author": "盈米基金",
                "tips": "测试",
                "url": "https://qieman.com/portfolios/SI000001",
                "data": [{"key": "中风险｜随时可买", "text": "股票基金"}],
                "_group": {"group": "M4_LONGTERM", "group_name": "长期投资"},
            },
            "2026-08-07T00:00:00+08:00",
            "run-1",
        )
        self.assertEqual(row["risk_level"], "中风险")
        self.assertEqual(row["strategy_type"], "股票基金")
        self.assertEqual(row["extra"]["availability_text"], "随时可买")
        self.assertIsNone(row["benchmark"])
        self.assertEqual(row["confidence_level"], "public_curated_entry_not_complete_catalog")

    def test_merge_candidates_deduplicates_codes_and_keeps_sources(self) -> None:
        catalogs = [
            (
                "/pmdj/v2/m4",
                {
                    "m4Items": [
                        {
                            "group": "M4_STEADY",
                            "name": "短期稳健",
                            "recommends": [{"recCode": "ZH123456", "recName": "组合一"}],
                        }
                    ]
                },
            ),
            (
                "/pmdj/v2/m4/hand-picked",
                {
                    "handPickedItems": [
                        {
                            "group": "M4_STEADY",
                            "name": "短期稳健",
                            "recommends": [
                                {"recCode": "ZH123456", "recName": "组合一"},
                                {"recCode": "SI000002", "recName": "组合二"},
                            ],
                        }
                    ]
                },
            ),
        ]
        rows = MODULE.merge_portfolio_candidates(catalogs)
        self.assertEqual([row["recCode"] for row in rows], ["SI000002", "ZH123456"])
        merged = next(row for row in rows if row["recCode"] == "ZH123456")
        self.assertEqual(
            merged["_source_catalog_paths"],
            ["/pmdj/v2/m4", "/pmdj/v2/m4/hand-picked"],
        )


if __name__ == "__main__":
    unittest.main()
