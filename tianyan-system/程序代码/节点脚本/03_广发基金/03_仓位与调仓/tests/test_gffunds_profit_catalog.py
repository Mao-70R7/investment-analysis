from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PYTHON_ROOT = next(
    parent / "_共享组件" / "python_src"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "python_src" / "advisor_monitor").is_dir()
)
sys.path.insert(0, str(PYTHON_ROOT))

from advisor_monitor.collectors.gffunds_public import (  # noqa: E402
    GFFundsPublicCollector,
    RawResponse,
)


def raw_response(root: Path, payload: dict) -> RawResponse:
    return RawResponse(
        json_data=payload,
        text="{}",
        snapshot={"snapshot_id": "snapshot-test", "parse_status": "success"},
        raw_path=root / "raw.json",
    )


class GFFundsProfitCatalogTests(unittest.TestCase):
    def test_profit_catalog_closes_on_official_has_next_false(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector = GFFundsPublicCollector(root, collect_protocol_pdf=False)
            response = raw_response(
                root,
                {
                    "RETCODE": "0000",
                    "is_has_next_page": False,
                    "invest_profit_list": [
                        {"adv_id": "ZY00000089", "adv_name": "小心愿Pro目标盈07期"},
                        {"adv_id": "ZY00000090", "adv_name": "小心愿目标盈46期"},
                    ],
                },
            )
            with patch.object(collector, "post_form", return_value=response):
                rows, summary = collector.collect_profit_strategy_catalog()

        self.assertEqual([row["adv_id"] for row in rows], ["ZY00000089", "ZY00000090"])
        self.assertTrue(summary["catalog_complete"])
        self.assertEqual(summary["catalog_stop_reason"], "has_next_false")

    def test_profit_issue_normalization_excludes_parent_entities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector = GFFundsPublicCollector(root, collect_protocol_pdf=False, run_id="test-run")
            response = raw_response(root, {"RETCODE": "0000"})
            normalized = collector.normalize(
                {
                    "ZY00000089": {
                        "strategy_name": "小心愿Pro目标盈07期",
                        "seed": {"adv_id": "ZY00000089", "adv_target_rate": "5.00"},
                        "config": {
                            "adv_id": "ZY00000089",
                            "adv_name": "小心愿Pro目标盈07期",
                            "adv_type": "稳健理财",
                        },
                        "config_response": response,
                        "protocol_meta": {"benchmark": "中债综合财富指数"},
                        "profit_issue": True,
                        "yield_response": None,
                        "adjustment_record_response": None,
                        "adjustment_details": {},
                        "adjustments": [],
                    }
                },
                {},
            )

        self.assertEqual(len(normalized["strategy_master"]), 1)
        master = normalized["strategy_master"][0]
        self.assertIsNone(master["launch_date"])
        self.assertEqual(master["extra"]["entity_scope"], "target_profit_issue")
        self.assertIn("parent_strategy_curve", master["extra"]["performance_lineage"])
        self.assertEqual(normalized["strategy_performance_daily"], [])
        self.assertEqual(normalized["strategy_fund_snapshot"], [])
        self.assertEqual(normalized["strategy_rebalance_event"], [])
        self.assertEqual(normalized["strategy_rebalance_fund_delta"], [])

    def test_incomplete_protocol_pdf_uses_last_successful_official_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector = GFFundsPublicCollector(root, collect_protocol_pdf=False)
            protocol_url = "https://example.invalid/strategy.pdf"
            cached_path = root / "cached.pdf"
            cached_path.write_bytes(b"%PDF-cached-official")
            collector._protocol_pdf_cache_index[protocol_url] = (
                cached_path,
                {"snapshot_id": "snapshot-cached", "parse_status": "success"},
            )
            partial_path = root / "partial.pdf"
            partial_path.write_bytes(b"partial")
            with patch.object(
                collector,
                "fetch_binary",
                return_value=(
                    b"partial",
                    {"snapshot_id": "snapshot-partial", "parse_status": "partial"},
                    partial_path,
                ),
            ):
                meta = collector.collect_protocol_meta(
                    adv_id="ZY00000089",
                    strategy_name="小心愿Pro目标盈07期",
                    protocol_payload={
                        "protocol_list": [
                            {
                                "protocol_type": "3",
                                "protocol_name": "策略说明书",
                                "protocol_url": protocol_url,
                            }
                        ]
                    },
                    product_dir=Path("products") / "ZY00000089",
                    force_collect=True,
                )

        self.assertEqual(meta["protocol_snapshot_id"], "snapshot-cached")
        self.assertEqual(meta["protocol_fetch_snapshot_id"], "snapshot-partial")
        self.assertEqual(meta["protocol_cache_fallback_path"], str(cached_path))
        self.assertEqual(collector.protocol_pdf_cache_fallback_total, 1)


if __name__ == "__main__":
    unittest.main()
