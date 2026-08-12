from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

from report_periods import (
    monthly_rebalance_asset_directory,
    monthly_rebalance_report_page,
    monthly_rebalance_snapshot_name,
    previous_completed_month,
)


class ReportPeriodsTests(unittest.TestCase):
    def test_previous_completed_month(self) -> None:
        self.assertEqual(previous_completed_month(date(2026, 8, 7)), "2026-07")

    def test_previous_completed_month_crosses_year(self) -> None:
        self.assertEqual(previous_completed_month(date(2026, 1, 10)), "2025-12")

    def test_monthly_report_names_share_one_period(self) -> None:
        self.assertEqual(monthly_rebalance_report_page("2026-07"), "monthly-rebalance-report-202607.html")
        self.assertEqual(monthly_rebalance_asset_directory("2026-07"), "monthly-rebalance-report-202607")
        self.assertEqual(monthly_rebalance_snapshot_name("2026-07"), "monthly-rebalance-report-202607.snapshot.json")


if __name__ == "__main__":
    unittest.main()
