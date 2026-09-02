from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


FRAMEWORK_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "resume_plan.py").is_file()
)
sys.path.insert(0, str(FRAMEWORK_ROOT))

from resume_plan import build_resume_plan  # noqa: E402
from node_runner import fingerprint  # noqa: E402
from state_store import StateStore  # noqa: E402
from workspace import load_workspace  # noqa: E402


class ResumePlanTest(unittest.TestCase):
    @staticmethod
    def _workspace(root: Path) -> None:
        node_root = root / "节点脚本"
        entries = []
        for node_id, name in (("preflight", "运行预检"), ("collect", "渠道采集"), ("publish", "发布")):
            directory = node_root / node_id
            directory.mkdir(parents=True)
            manifest = {
                "schemaVersion": 1,
                "id": node_id,
                "name": name,
                "phase": "fixture",
                "entrypoint": "run.ps1",
                "dependencies": [],
                "criticality": "critical",
                "timeoutSeconds": 60,
                "maxProcessAttempts": 1,
                "resourceLock": None,
                "supportsResume": node_id != "publish",
                "progressUnit": "items",
                "validator": {"type": "node_result", "requiredStatus": "passed"},
            }
            (directory / "node.json").write_text(json.dumps(manifest), encoding="utf-8")
            entries.append(
                {
                    "id": node_id,
                    "directory": node_id,
                    "dependencies": [],
                    "enabledWhen": {"daily": True},
                }
            )
        (node_root / "pipeline.json").write_text(
            json.dumps({"schemaVersion": 1, "version": "fixture", "nodes": entries}),
            encoding="utf-8",
        )

    @staticmethod
    def _finish_success(
        state: StateStore,
        run_id: str,
        node_id: str,
        result_path: Path,
        log_path: Path,
    ) -> None:
        payload = {
            "schemaVersion": 1,
            "nodeId": node_id,
            "runId": run_id,
            "status": "success",
            "returncode": 0,
            "artifacts": [],
            "validation": {"status": "passed"},
        }
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        state.start_node(run_id, node_id, 1, "input", log_path, None)
        state.finish_node(
            run_id,
            node_id,
            1,
            "success",
            0,
            result_path,
            fingerprint(payload),
            None,
        )

    def test_latest_interrupted_run_resumes_from_current_node(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._workspace(root)
            workspace = load_workspace(root)
            state = StateStore(workspace.state_db)
            run_dir = root / "logs" / "daily_update" / "2026-09-01" / "run-1"
            run_dir.mkdir(parents=True)
            state.create_run("run-1", run_dir, {"mode": "daily", "dryRun": False})
            result = run_dir / "preflight.json"
            self._finish_success(
                state,
                "run-1",
                "preflight",
                result,
                run_dir / "preflight.log",
            )
            state.start_node("run-1", "collect", 1, "input", run_dir / "collect.log", None)
            state.close()
            workspace.lock_root.mkdir(parents=True)
            (workspace.lock_root / "daily_update.lock").write_text(
                json.dumps({"pid": 999999999, "runId": "run-1", "acquiredAt": "2026-09-01T00:00:00+08:00"}),
                encoding="utf-8",
            )

            plan = build_resume_plan(root)

            self.assertTrue(plan["available"])
            self.assertTrue(plan["staleLock"])
            self.assertFalse(plan["active"])
            self.assertEqual(plan["runId"], "run-1")
            self.assertEqual(plan["suggestedFromNode"], "collect")
            self.assertEqual(plan["completedNodes"], 1)
            self.assertEqual(plan["totalNodes"], 3)

    def test_completed_latest_run_is_not_offered_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._workspace(root)
            workspace = load_workspace(root)
            state = StateStore(workspace.state_db)
            run_dir = root / "logs" / "run-complete"
            run_dir.mkdir(parents=True)
            state.create_run("run-complete", run_dir, {"mode": "daily", "dryRun": False})
            state.update_run("run-complete", status="success", finished_at="2026-09-01T01:00:00+08:00")
            state.close()

            plan = build_resume_plan(root)

            self.assertFalse(plan["available"])
            self.assertEqual(plan["runStatus"], "success")
            self.assertIn("已经完成", plan["reason"])

    def test_dry_run_is_not_selected_as_latest_real_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._workspace(root)
            workspace = load_workspace(root)
            state = StateStore(workspace.state_db)
            real_dir = root / "logs" / "real"
            dry_dir = root / "logs" / "dry"
            real_dir.mkdir(parents=True)
            dry_dir.mkdir(parents=True)
            state.create_run("real", real_dir, {"mode": "daily", "dryRun": False})
            state.update_run("real", status="interrupted", current_stage="collect")
            state.create_run("dry", dry_dir, {"mode": "daily", "dryRun": True})
            state.close()

            plan = build_resume_plan(root)

            self.assertEqual(plan["runId"], "real")

    def test_recommended_plan_prefers_more_advanced_same_day_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._workspace(root)
            workspace = load_workspace(root)
            state = StateStore(workspace.state_db)
            for run_id in ("advanced", "newer"):
                run_dir = root / "logs" / run_id
                run_dir.mkdir(parents=True)
                state.create_run(run_id, run_dir, {"mode": "daily", "dryRun": False})
            state.connection.execute(
                "UPDATE daily_update_run SET started_at='2026-09-01T10:00:00+08:00' WHERE run_id='advanced'"
            )
            state.connection.execute(
                "UPDATE daily_update_run SET started_at='2026-09-01T11:00:00+08:00' WHERE run_id='newer'"
            )
            for run_id, successful_nodes, current_node in (
                ("advanced", ("preflight", "collect"), "publish"),
                ("newer", ("preflight",), "collect"),
            ):
                for node_id in successful_nodes:
                    log_path = root / "logs" / run_id / f"{node_id}.log"
                    result_path = root / "logs" / run_id / f"{node_id}.json"
                    self._finish_success(
                        state,
                        run_id,
                        node_id,
                        result_path,
                        log_path,
                    )
                state.start_node(
                    run_id,
                    current_node,
                    1,
                    "input",
                    root / "logs" / run_id / f"{current_node}.log",
                    None,
                )
            state.connection.commit()
            state.close()

            from resume_plan import build_recommended_resume_plan

            plan = build_recommended_resume_plan(root)

            self.assertEqual(plan["runId"], "advanced")
            self.assertEqual(plan["latestRunId"], "newer")
            self.assertFalse(plan["isLatestRun"])
            self.assertEqual(plan["completedNodes"], 2)
            self.assertEqual(plan["suggestedFromNode"], "publish")


if __name__ == "__main__":
    unittest.main()
