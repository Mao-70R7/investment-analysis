from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROBE_ROOT = Path(__file__).resolve().parents[1]
if str(PROBE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROBE_ROOT))

from normalize_qieman_signed_history_sample import action_type, duplicate_count, shanghai_date


class NormalizeQiemanSignedHistorySampleTests(unittest.TestCase):
    def test_shanghai_date_uses_channel_timezone(self) -> None:
        self.assertEqual(shanghai_date(1484841600000), "2017-01-20")

    def test_action_type_covers_all_weight_movements(self) -> None:
        self.assertEqual(action_type(0, 0.1), "buy")
        self.assertEqual(action_type(0.1, 0), "sell")
        self.assertEqual(action_type(0.1, 0.2), "increase")
        self.assertEqual(action_type(0.2, 0.1), "decrease")
        self.assertEqual(action_type(0.1, 0.1), "keep")

    def test_duplicate_count_uses_declared_business_key(self) -> None:
        rows = [{"a": 1, "b": 2}, {"a": 1, "b": 2}, {"a": 1, "b": 3}]
        self.assertEqual(duplicate_count(rows, ("a", "b")), 1)


if __name__ == "__main__":
    unittest.main()
