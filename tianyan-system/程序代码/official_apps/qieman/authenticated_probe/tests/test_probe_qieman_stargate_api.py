from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULE_PATH = ROOT / "probe_qieman_stargate_api.py"
SPEC = importlib.util.spec_from_file_location("probe_qieman_stargate_api", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ProbeQiemanStargateApiTests(unittest.TestCase):
    def test_normalize_search_product_keeps_catalog_metrics_out_of_daily_series(self) -> None:
        row = MODULE.normalize_search_product(
            {
                "prodCode": "ZH000001",
                "prodName": "示例策略",
                "poManagerName": "示例机构",
                "risk5Level": 3,
                "productType": "固收+",
                "m4Type": "STEADY",
                "annualCompoundedReturn": 0.05,
                "maxDrawdown": 0.03,
                "establishedOn": "2020-01-01 00:00:00",
            },
            "2026-08-09T00:00:00+08:00",
            "run-1",
        )
        self.assertEqual(row["source_strategy_id"], "ZH000001")
        self.assertEqual(row["launch_date"], "2020-01-01 00:00:00")
        self.assertEqual(row["risk_level"], "R3")
        self.assertTrue(row["extra"]["catalog_metrics_are_not_daily_performance"])
        self.assertNotIn("strategy_performance_daily", row)

    def test_target_inventory_prefers_batch_adjustment_post(self) -> None:
        document = {
            "paths": {
                "/strategy/{code}/adjustments": {
                    "get": {"operationId": "GetStrategyAdjustments", "summary": "single"}
                },
                "/oap/api/v1/strategy/adjustments": {
                    "post": {"operationId": "GetStrategyAdjustments", "summary": "batch"}
                },
            }
        }
        rows = MODULE.target_operation_inventory(document)
        self.assertEqual(rows[0]["method"], "POST")
        self.assertEqual(rows[0]["path"], "/oap/api/v1/strategy/adjustments")


if __name__ == "__main__":
    unittest.main()
