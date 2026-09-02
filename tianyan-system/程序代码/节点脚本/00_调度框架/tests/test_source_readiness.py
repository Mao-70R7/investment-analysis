from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
PROGRAM_ROOT = ROOT / "节点脚本" / "_共享组件" / "生产程序"
sys.path.insert(0, str(PROGRAM_ROOT))

from check_daily_source_readiness import assess_readiness  # noqa: E402


class SourceReadinessTests(unittest.TestCase):
    def assess(self, **overrides):
        values = {
            "target_trade_date": None,
            "quote_ratio": 0.0,
            "local_max_date": "2026-07-21",
            "source_effective_date": "2026-07-22",
            "success_ratio": 1.0,
            "latest_ratio": 1.0,
            "benchmark_ratio": 1.0,
            "min_latest_ratio": 0.8,
            "min_benchmark_ratio": 0.75,
            "source_lag_business_days": None,
            "max_source_lag_business_days": 1,
        }
        values.update(overrides)
        return assess_readiness(**values)

    def test_official_curve_can_confirm_readiness_when_quote_target_is_absent(self) -> None:
        result = self.assess()
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["readiness_mode"], "official_curve_fallback")
        self.assertTrue(result["official_ready"])
        self.assertFalse(result["quote_ready"])
        self.assertEqual(result["reasons"], [])

    def test_existing_quote_target_keeps_quote_coverage_as_hard_gate(self) -> None:
        result = self.assess(target_trade_date="2026-07-22", quote_ratio=0.5, source_lag_business_days=0)
        self.assertEqual(result["state"], "waiting")
        self.assertIn("quote coverage ratio is 0.5000", result["reasons"])

    def test_absent_quote_does_not_bypass_incomplete_official_curve(self) -> None:
        result = self.assess(success_ratio=0.5)
        self.assertEqual(result["state"], "waiting")
        self.assertIn("quote endpoint did not return a target trade date", result["reasons"])
        self.assertIn("official curve success ratio is 0.5000", result["reasons"])


if __name__ == "__main__":
    unittest.main()
