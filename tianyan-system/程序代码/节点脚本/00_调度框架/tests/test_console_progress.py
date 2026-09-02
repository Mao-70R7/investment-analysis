from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.progress import ConsoleProgress, format_duration


class ConsoleProgressTests(unittest.TestCase):
    def test_format_duration_keeps_long_running_stage_readable(self) -> None:
        self.assertEqual(format_duration(3661), "01:01:01")
        self.assertEqual(format_duration(None), "--")

    def test_console_progress_contains_counts_percent_elapsed_and_eta(self) -> None:
        progress = ConsoleProgress(
            "天天投顾当前仓位采集",
            100,
            started_monotonic=time.monotonic() - 10,
        )

        line = progress.line(25, success=24, failed=1, current="ABC123")

        self.assertIn("[PROGRESS] 天天投顾当前仓位采集", line)
        self.assertIn("已完成策略 25/100 (25.0%)", line)
        self.assertIn("成功 24", line)
        self.assertIn("失败 1", line)
        self.assertIn("已耗时 00:00:10", line)
        self.assertIn("预计剩余 00:00:30", line)
        self.assertIn("当前 ABC123", line)
