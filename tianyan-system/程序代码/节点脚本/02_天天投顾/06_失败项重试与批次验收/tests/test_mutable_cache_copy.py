from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(CODE_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors import ttfund_loggedin  # noqa: E402


class MutableCacheCopyTests(unittest.TestCase):
    def test_disappeared_source_entries_are_counted_without_failing(self) -> None:
        errors = [
            ("source-a", "target-a", "[WinError 3] path not found"),
            ("source-b", "target-b", "[Errno 2] No such file or directory"),
        ]
        with patch.object(ttfund_loggedin.shutil, "copytree", side_effect=shutil.Error(errors)):
            skipped = ttfund_loggedin.copy_mutable_cache_tree(Path("source"), Path("target"))
        self.assertEqual(skipped, 2)

    def test_non_missing_copy_error_still_fails(self) -> None:
        errors = [("source-a", "target-a", "[WinError 5] Access is denied")]
        with patch.object(ttfund_loggedin.shutil, "copytree", side_effect=shutil.Error(errors)):
            with self.assertRaises(shutil.Error):
                ttfund_loggedin.copy_mutable_cache_tree(Path("source"), Path("target"))

    def test_successful_copy_reports_no_skips(self) -> None:
        with patch.object(ttfund_loggedin.shutil, "copytree") as copytree:
            skipped = ttfund_loggedin.copy_mutable_cache_tree(Path("source"), Path("target"))
        self.assertEqual(skipped, 0)
        copytree.assert_called_once_with(
            Path("source"),
            Path("target"),
            dirs_exist_ok=True,
            ignore=ttfund_loggedin.ignore_transient_cache_entries,
        )


if __name__ == "__main__":
    unittest.main()
