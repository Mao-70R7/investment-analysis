import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "生产程序" / "load_analysis_zh_current_sqlite.py"
SPEC = importlib.util.spec_from_file_location("load_analysis_percent_semantics", SCRIPT)
assert SPEC and SPEC.loader
sys.path.insert(0, str(SCRIPT.parent))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PercentSemanticsTest(unittest.TestCase):
    def test_zocaifu_return_is_decimal_but_weight_is_percent_point(self):
        self.assertEqual(0.1, MODULE.to_return_percent("zocaifu", 0.001))
        self.assertEqual(9.09, MODULE.to_weight_percent("zocaifu", 9.09))

    def test_southern_migrated_return_is_decimal(self):
        self.assertEqual(0.1, MODULE.to_return_percent("southern", 0.001))
        self.assertEqual(9.09, MODULE.to_weight_percent("southern", 9.09))

    def test_southern_authenticated_return_uses_explicit_percent_point_unit(self):
        self.assertEqual(0.69, MODULE.to_return_percent("southern", 0.69, "percent_point"))
        self.assertEqual(69.0, MODULE.to_return_percent("southern", 0.69, "decimal"))

    def test_decimal_weight_channels_still_convert(self):
        self.assertEqual(25.0, MODULE.to_weight_percent("qieman", 0.25))
        self.assertEqual(25.0, MODULE.to_weight_percent("huaxia_tougu", 0.25))

    def test_legacy_to_percent_keeps_return_semantics(self):
        self.assertEqual(0.1, MODULE.to_percent("zocaifu", 0.001))


if __name__ == "__main__":
    unittest.main()
