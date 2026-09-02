from __future__ import annotations

import importlib.util
import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "_共享组件"
    / "生产程序"
    / "audit_public_fund_data_validity.py"
)
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("audit_public_fund_data_validity", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublicFundDataValidityTest(unittest.TestCase):
    def test_interval_ranges_keep_first_half_at_june_30(self) -> None:
        ranges = MODULE.interval_ranges(date(2026, 7, 16))
        self.assertEqual(ranges["上半年"], (date(2025, 12, 31), date(2026, 6, 30)))
        self.assertEqual(ranges["今年以来"], (date(2025, 12, 31), date(2026, 7, 16)))
        self.assertEqual(ranges["近1周"], (date(2026, 7, 9), date(2026, 7, 16)))

    def test_snapshot_end_date_defaults_to_database_watermark(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute('CREATE TABLE "公募基金产品绩效快照" ("绩效截止日期" TEXT)')
        conn.executemany(
            'INSERT INTO "公募基金产品绩效快照" VALUES (?)',
            [("2026-07-15",), ("2026-07-16",), ("",)],
        )
        self.assertEqual(MODULE.snapshot_end_date(conn, None), date(2026, 7, 16))
        self.assertEqual(
            MODULE.snapshot_end_date(conn, date(2026, 6, 30)),
            date(2026, 6, 30),
        )

    def test_annual_verified_bucket_source_is_allowed(self) -> None:
        self.assertIn("年度披露权重核验", MODULE.ALLOWED_BUCKET_SOURCES)


if __name__ == "__main__":
    unittest.main()
