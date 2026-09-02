from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULE_PATH = ROOT / "capture_qieman_strategy_scroll.py"
SPEC = importlib.util.spec_from_file_location("capture_qieman_strategy_scroll", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CaptureQiemanStrategyScrollTests(unittest.TestCase):
    def test_assessment_does_not_upgrade_ocr_to_exact_data(self) -> None:
        result = MODULE.assess_visible_text(
            ["业绩基准：货币基金指数", "近一年 2.30%", "当前持仓", "基金 000001 占比 25%", "投顾服务费"]
        )
        self.assertTrue(result["benchmark_keyword_seen"])
        self.assertTrue(result["performance_keyword_seen"])
        self.assertTrue(result["holding_keyword_seen"])
        self.assertEqual(result["fund_codes"], ["000001"])
        self.assertIn("不能单独形成正式", result["quality_note"])


if __name__ == "__main__":
    unittest.main()

