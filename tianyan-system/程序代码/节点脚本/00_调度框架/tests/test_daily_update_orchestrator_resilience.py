from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from unittest.mock import Mock


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

from daily_update_orchestrator import (
    CHILD_SIGNAL_PATTERN,
    Orchestrator,
    atomic_write_json,
    extract_child_progress,
    strip_wrapper_timestamps,
)


class ChildProgressSignalTests(unittest.TestCase):
    def test_standard_progress_line_is_forwarded_without_wrapper_timestamp(self) -> None:
        line = (
            "[2026-07-21 15:00:00] [PROGRESS] 天天投顾当前仓位采集 | "
            "已完成策略 328/653 (50.2%) | 成功 328 | 失败 0"
        )

        self.assertIsNotNone(CHILD_SIGNAL_PATTERN.search(line))
        self.assertEqual(
            extract_child_progress(line),
            "[PROGRESS] 天天投顾当前仓位采集 | 已完成策略 328/653 (50.2%) | 成功 328 | 失败 0",
        )

    def test_legacy_json_progress_is_converted_to_readable_progress(self) -> None:
        line = '[2026-07-21 15:00:00] {"event":"strategy_done","progress":"10/40"}'

        self.assertIsNotNone(CHILD_SIGNAL_PATTERN.search(line))
        self.assertEqual(
            extract_child_progress(line),
            "[PROGRESS] 子任务 | 已完成策略 10/40 (25.0%)",
        )

    def test_nested_wrapper_timestamps_are_collapsed(self) -> None:
        line = "[2026-07-21 15:00:00] [2026-07-21 15:00:01] [INFO] current stage"

        self.assertEqual(strip_wrapper_timestamps(line), "[INFO] current stage")


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_retries_transient_windows_permission_error(self) -> None:
        real_replace = Path.replace
        attempts = 0

        def flaky_replace(source: Path, target: Path) -> Path:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError(5, "temporarily locked", str(target))
            return real_replace(source, target)

        with TemporaryDirectory() as directory:
            target = Path(directory) / "daily_update.lock"
            with patch.object(Path, "replace", new=flaky_replace), patch(
                "daily_update_orchestrator.time.sleep", return_value=None
            ):
                atomic_write_json(target, {"run_id": "test", "pid": 1})

            self.assertEqual(attempts, 3)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["run_id"], "test")

    def test_heartbeat_write_failure_does_not_interrupt_core_stage(self) -> None:
        orchestrator = Orchestrator.__new__(Orchestrator)
        orchestrator.lock = Mock()
        orchestrator.lock.heartbeat.side_effect = PermissionError(5, "temporarily locked")
        orchestrator.state = Mock()
        orchestrator.console = Mock()
        orchestrator.run_id = "test-run"
        orchestrator.lock_heartbeat_failure_total = 0

        orchestrator.heartbeat()

        orchestrator.state.update_run.assert_called_once_with("test-run")
        orchestrator.console.assert_called_once()
        self.assertEqual(orchestrator.lock_heartbeat_failure_total, 1)


class IncrementalRecoveryTests(unittest.TestCase):
    def test_reconcile_completed_child_summary_without_rerunning_collection(self) -> None:
        with TemporaryDirectory() as directory:
            project_root = Path(directory)
            summary_path = project_root / "logs" / "incremental_update" / "2026-07-19" / "child" / "summary.json"
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                json.dumps(
                    {
                        "runId": "child",
                        "parentRunId": "parent",
                        "status": "success",
                        "exitCode": 0,
                        "startedAt": "2026-07-19T01:00:00",
                        "projectRoot": str(project_root),
                        "deploy": {"status": "ready", "missing": []},
                        "database": {"exists": True},
                        "postUpdate": {"status": "completed"},
                        "ttfund": {"target_trade_date": "2026-07-17"},
                    }
                ),
                encoding="utf-8",
            )
            orchestrator = Orchestrator.__new__(Orchestrator)
            orchestrator.project_root = project_root
            orchestrator.run_id = "parent"
            orchestrator.state = Mock()
            orchestrator.state.load_stage.return_value = {
                "status": "running",
                "started_at": "2026-07-19T01:00:00+08:00",
                "log_path": str(project_root / "outer.log"),
            }
            orchestrator.state.load_run.return_value = {"source_target_date": "2026-07-17"}
            orchestrator.event = Mock()
            orchestrator.console = Mock()

            self.assertTrue(orchestrator.reconcile_incremental_stage())
            orchestrator.state.stage_finish.assert_called_once_with("parent", "01_incremental_update", 0)

    def test_reconcile_rejects_target_date_mismatch(self) -> None:
        orchestrator = Orchestrator.__new__(Orchestrator)
        orchestrator.project_root = Path(".")
        orchestrator.run_id = "parent"
        orchestrator.state = Mock()
        orchestrator.state.load_stage.return_value = {
            "status": "running",
            "started_at": "2026-07-19T01:00:00+08:00",
            "log_path": "",
        }
        orchestrator.state.load_run.return_value = {"source_target_date": "2026-07-18"}
        orchestrator.find_completed_incremental_summary = Mock(return_value=Path("child-summary.json"))
        orchestrator.read_json_file = Mock(
            return_value={"runId": "child", "ttfund": {"target_trade_date": "2026-07-17"}}
        )

        with self.assertRaisesRegex(RuntimeError, "target date mismatch"):
            orchestrator.reconcile_incremental_stage()


if __name__ == "__main__":
    unittest.main(verbosity=2)
