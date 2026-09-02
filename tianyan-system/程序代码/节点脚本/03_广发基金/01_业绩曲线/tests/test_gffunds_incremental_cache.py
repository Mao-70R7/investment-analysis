from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PYTHON_ROOT = next(
    parent / "_共享组件" / "python_src"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "python_src" / "advisor_monitor").is_dir()
)
sys.path.insert(0, str(PYTHON_ROOT))

from advisor_monitor.collectors.gffunds_public import GFFundsPublicCollector  # noqa: E402


class GFFundsIncrementalCacheTest(unittest.TestCase):
    def test_latest_adjustment_default_refreshes_daily(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collector = GFFundsPublicCollector(Path(temporary), run_id="new-run")
            self.assertEqual(collector.latest_adjustment_refresh_days, 1)

    def test_valid_historical_adjustment_is_reused_into_exact_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cached = (
                root
                / "data"
                / "raw"
                / "gffunds"
                / "public_api"
                / "2026-07-22"
                / "old-run"
                / "products"
                / "GFJJ000001_strategy"
                / "adjustments"
                / "20260701.json"
            )
            cached.parent.mkdir(parents=True)
            payload = {
                "RETCODE": "0000",
                "advisor_comb_list": [
                    {"comb_fund_list": [{"fund_code": "000001", "new_percent": "10"}]}
                ],
            }
            cached.write_text(json.dumps(payload), encoding="utf-8")
            collector = GFFundsPublicCollector(root, run_id="new-run")
            collector.raw_base_dir.mkdir(parents=True)
            collector.build_adjustment_cache_index()
            response, age_days = collector.cached_adjustment_response(
                "GFJJ000001",
                "20260701",
                Path("products/GFJJ000001_strategy/adjustments/20260701.json"),
            )

            self.assertIsNotNone(response)
            self.assertIsNotNone(age_days)
            self.assertEqual(response.json_data, payload)
            self.assertTrue(response.raw_path.is_file())
            self.assertEqual(response.raw_path, cached)
            self.assertFalse(
                (
                    collector.raw_base_dir
                    / "products"
                    / "GFJJ000001_strategy"
                    / "adjustments"
                    / "20260701.json"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
