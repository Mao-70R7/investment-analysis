from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROBE_ROOT = Path(__file__).resolve().parents[1]
if str(PROBE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROBE_ROOT))

from normalize_qieman_signed_history_catalog import (
    normalise_signal_history,
    position_map,
)


class NormalizeQiemanSignedHistoryCatalogTests(unittest.TestCase):
    def test_post_position_uses_full_capital_weight_when_legacy_target_percent_is_zero(self) -> None:
        rows = [
            {
                "fundCode": "000001",
                "targetPercent": 0,
                "targetCapitalPercent": 0.4,
                "capitalPercent": 0.38,
            },
            {
                "fundCode": "000002",
                "targetPercent": 0,
                "targetCapitalPercent": 0.6,
                "capitalPercent": 0.62,
            },
        ]
        result = position_map(rows, "post_target")
        self.assertEqual(result["000001"]["weight_field"], "targetCapitalPercent")
        self.assertAlmostEqual(sum(item["weight"] for item in result.values()), 1.0)

    def test_signal_instruction_ratio_is_not_used_as_portfolio_weight(self) -> None:
        payload = {
            "complete": True,
            "content": [
                {
                    "id": 1,
                    "prodCode": "SI_TEST",
                    "prodName": "测试发车",
                    "adjustedDate": "2026-08-05",
                    "createdTime": "2026-08-05 10:00:00",
                    "buyOrders": [
                        {"fundCode": "000002", "fundName": "基金二", "percent": 0.5, "amount": 500}
                    ],
                    "redeemOrders": [],
                    "convertOrders": [],
                    "extra": {
                        "signalPoSimulateAsset": {
                            "updatedDate": "2026-08-04",
                            "compositionAssetList": [
                                {"prodCode": "000001", "percent": 1.0}
                            ],
                        },
                        "modelTargetComposition": [
                            {"fundCode": "000001", "fundName": "基金一", "targetCapitalPercent": 0.8},
                            {"fundCode": "000002", "fundName": "基金二", "targetCapitalPercent": 0.2},
                        ],
                    },
                }
            ],
        }
        events, instructions, snapshots, projected_events, projected_deltas, quality = normalise_signal_history(
            "SI_TEST", "测试发车", payload, "run-1", "2026-08-05", {}, {}
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(len(instructions), 1)
        self.assertEqual(instructions[0]["instruction_ratio"], 0.5)
        self.assertEqual(instructions[0]["after_portfolio_weight"], 0.2)
        self.assertIn("not_portfolio_weight", instructions[0]["instruction_ratio_semantics"])
        self.assertEqual(len(snapshots), 3)
        self.assertEqual(len(projected_events), 1)
        self.assertEqual(len(projected_deltas), 2)
        self.assertTrue(quality["position_history_complete"])

    def test_signal_history_does_not_synthesize_missing_old_positions(self) -> None:
        payload = {
            "complete": True,
            "content": [
                {
                    "id": 1,
                    "adjustedDate": "2020-01-01",
                    "buyOrders": [{"fundCode": "000001", "percent": 1.0}],
                    "redeemOrders": [],
                    "convertOrders": [],
                    "extra": {},
                }
            ],
        }
        _, instructions, snapshots, projected_events, projected_deltas, quality = normalise_signal_history(
            "SI_TEST", "测试发车", payload, "run-1", "2026-08-05", {}, {}
        )
        self.assertEqual(len(instructions), 1)
        self.assertEqual(snapshots, [])
        self.assertEqual(projected_events, [])
        self.assertEqual(projected_deltas, [])
        self.assertFalse(quality["position_history_complete"])
        self.assertEqual(quality["status"], "events_complete_positions_partially_disclosed")


if __name__ == "__main__":
    unittest.main()
