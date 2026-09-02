from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
SCRIPT_PATH = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "构建信号类策略事件.py"
SPEC = importlib.util.spec_from_file_location("build_signal_strategy_events", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SignalEventPathTests(unittest.TestCase):
    def test_workspace_separated_raw_file_uses_workspace_relative_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            code_root = workspace / "程序代码"
            source = workspace / "采集数据" / "raw" / "ttfund" / "strategy" / "events.json"
            code_root.mkdir()
            source.parent.mkdir(parents=True)
            source.write_text('{"data": [{"dateStr": "2026-07-23", "reason": "test"}]}', encoding="utf-8")

            with patch.dict(os.environ, {"ADVISOR_WORKSPACE_ROOT": str(workspace)}):
                events, used_files = MODULE.load_events_from_files([source], code_root)

            expected = str(source.relative_to(workspace))
            self.assertEqual(used_files, [expected])
            self.assertEqual(events[0]["_raw_file"], expected)

    def test_external_raw_file_keeps_provenance_without_interrupting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            code_root = root / "code"
            source = root / "external" / "events.json"
            code_root.mkdir()
            source.parent.mkdir()
            source.write_text('{"data": [{"dateStr": "2026-07-23", "reason": "test"}]}', encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                events, used_files = MODULE.load_events_from_files([source], code_root)

            self.assertEqual(used_files, [str(source.resolve())])
            self.assertEqual(events[0]["_raw_file"], str(source.resolve()))


if __name__ == "__main__":
    unittest.main()
