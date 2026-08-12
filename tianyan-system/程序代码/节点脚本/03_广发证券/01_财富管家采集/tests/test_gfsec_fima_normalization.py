from __future__ import annotations

import sys
import unittest
from pathlib import Path


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(CODE_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors.official_apps_public import OfficialAppsPublicCollector  # noqa: E402


class GfsecFimaNormalizationTests(unittest.TestCase):
    def collector(self) -> OfficialAppsPublicCollector:
        collector = OfficialAppsPublicCollector.__new__(OfficialAppsPublicCollector)
        collector.captured_at = "2026-07-28T10:00:00+08:00"
        collector.day = "2026-07-28"
        collector.run_id = "unit-test"
        return collector

    def test_product_instance_ids_keep_regular_and_target_periods_distinct(self) -> None:
        regular = {"product_type": "regular_strategy", "partner_id": "gffund", "product_id": "ABC"}
        target = {"product_type": "target_period", "partner_id": "gffund", "product_id": "ABC"}
        self.assertEqual(
            OfficialAppsPublicCollector.gfsec_fima_source_strategy_id(regular),
            "regular:gffund:ABC",
        )
        self.assertEqual(
            OfficialAppsPublicCollector.gfsec_fima_source_strategy_id(target),
            "target:gffund:ABC",
        )

    def test_target_master_preserves_underlying_portfolio_and_status(self) -> None:
        collector = self.collector()
        owner = {
            "product_type": "target_period",
            "partner_id": "hxfund",
            "strategy_code": "XFYJH001",
            "product_id": 10009,
            "portfolio_code": "SFG001",
            "catalog": {},
            "target": {
                "targetProfitName": "幸福盈家小目标-华夏多元1期",
                "participationStartDate": "2026-01-05 00:00:00",
                "status": 6,
                "riskLevel": 2,
            },
        }
        result = {
            "endpoints": {
                "portfolio_mix": {
                    "payload": {
                        "advisorOrgName": "上海华夏财富投资管理有限公司",
                        "riskLevelName": "R2（中低风险）",
                        "strategyTypeName": "固收增强",
                        "firstMinAmount": 1000,
                        "fees": [{"feeType": 10, "status": 0, "feeRatio": 0.006}],
                    },
                    "snapshot_id": "mix-snapshot",
                    "source_url": "https://example.invalid/mix",
                },
                "period_performance": {"payload": {"portfolioPerf": {"totalYield": 0.01}}},
            }
        }
        row = collector.gfsec_fima_master_row(owner, result, {})
        self.assertEqual(row["source_strategy_id"], "target:hxfund:10009")
        self.assertEqual(row["launch_date"], "2026-01-05")
        self.assertEqual(row["advisory_fee_rate"], "0.6%/年")
        self.assertEqual(row["extra"]["underlying_portfolio_code"], "SFG001")
        self.assertEqual(row["extra"]["target_period_status"], 6)
        self.assertEqual(
            row["extra"]["current_position_semantics"],
            "official_current_model_allocation_not_customer_actual_holding",
        )

    def test_regular_master_parses_local_midnight_setup_date_in_china_timezone(self) -> None:
        collector = self.collector()
        owner = {
            "product_type": "regular_strategy",
            "partner_id": "efund",
            "strategy_code": "EFMMEGF",
            "product_id": "EFMMEGF",
            "portfolio_code": "MMEGF",
            "catalog": {"displayName": "易方达货币增强"},
        }
        result = {
            "endpoints": {
                "portfolio_mix": {
                    "payload": {"setupDate": 1598198400000},
                    "snapshot_id": "mix-snapshot",
                    "source_url": "https://example.invalid/mix",
                },
                "period_performance": {"payload": {}},
                "official_curve": {"payload": {}},
            }
        }

        row = collector.gfsec_fima_master_row(owner, result, {})

        self.assertEqual(row["launch_date"], "2020-08-24")


if __name__ == "__main__":
    unittest.main()
