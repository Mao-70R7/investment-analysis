from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parents[4]
PROGRAM_DIR = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序"
SCRIPT = PROGRAM_DIR / "export_basic_data_pages.py"
os.environ.setdefault("ADVISOR_CODE_ROOT", str(CODE_ROOT))
sys.path.insert(0, str(PROGRAM_DIR))
SPEC = importlib.util.spec_from_file_location("export_basic_data_pages_curve_test", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StrategyDetailCurveCompletenessTests(unittest.TestCase):
    def test_detail_curve_limit_is_disabled(self) -> None:
        self.assertIsNone(MODULE.DETAIL_CURVE_MAX_POINTS)

    def test_no_limit_preserves_every_valid_date_level_point(self) -> None:
        rows = [
            {"日期": f"2026-08-{day:02d}", "数值": 1 + day / 1000}
            for day in range(1, 21)
        ]
        result = MODULE.sample_series(rows, "日期", "数值", max_points=None)
        self.assertEqual(len(result), len(rows))
        self.assertEqual([row["日期"] for row in result], [row["日期"] for row in rows])

    def test_invalid_rows_are_removed_but_valid_rows_are_not_sampled(self) -> None:
        rows = [
            {"日期": "2026-08-05", "数值": 1.63},
            {"日期": "2026-08-06", "数值": 1.64},
            {"日期": "", "数值": 1.65},
            {"日期": "2026-08-07", "数值": None},
        ]
        result = MODULE.sample_series(rows, "日期", "数值", max_points=None)
        self.assertEqual([row["日期"] for row in result], ["2026-08-05", "2026-08-06"])

    def test_latest_evaluable_event_skips_newer_unavailable_event(self) -> None:
        rows = [
            {
                "统一策略ID": "ttfund__S1",
                "调仓事件ID": "event-new-unavailable",
                "调仓日期": "2026-08-31",
                "评估状态": "暂不可评估",
            },
            {
                "统一策略ID": "ttfund__S1",
                "调仓事件ID": "event-latest-evaluable",
                "调仓日期": "2026-08-20",
                "评估状态": "可评估",
            },
            {
                "统一策略ID": "ttfund__S1",
                "调仓事件ID": "event-old-evaluable",
                "调仓日期": "2026-08-01",
                "评估状态": "可评估",
            },
            {
                "统一策略ID": "ttfund__S2",
                "调仓事件ID": "event-s2",
                "调仓日期": "2026-07-15",
                "评估状态": "可评估",
            },
        ]
        self.assertEqual(
            MODULE.latest_evaluable_event_ids(rows),
            {"event-latest-evaluable", "event-s2"},
        )


if __name__ == "__main__":
    unittest.main()
