from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


FRAMEWORK_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "orchestrator.py").is_file())
sys.path.insert(0, str(FRAMEWORK_ROOT))

from progress import parse_progress  # noqa: E402
from node_runner import (  # noqa: E402
    NodeExecution,
    NodeRunner,
    acquire_resource_lock,
    is_ephemeral_runtime_context_key,
    release_resource_lock,
)
import orchestrator as orchestrator_module  # noqa: E402
from orchestrator import (  # noqa: E402
    Orchestrator,
    classify_run,
    dependency_blockers,
    validate_pipeline,
)
from state_store import StateStore  # noqa: E402
from workspace import load_workspace  # noqa: E402


class NodeFrameworkTest(unittest.TestCase):
    def test_ephemeral_channel_context_cannot_leak_from_parent_environment(self) -> None:
        self.assertTrue(is_ephemeral_runtime_context_key("TTFUND_COLLECT_RUN_ID"))
        self.assertTrue(is_ephemeral_runtime_context_key("GFFUNDS_GATE_PASSED"))
        self.assertTrue(is_ephemeral_runtime_context_key("GFSEC_FIMA_GATE_PASSED"))
        self.assertTrue(
            is_ephemeral_runtime_context_key("ADVISOR_NODE_STATUS_TTFUND_INCREMENTAL")
        )
        self.assertTrue(is_ephemeral_runtime_context_key("ADVISOR_REPORT_STAGING_ROOT"))
        self.assertFalse(is_ephemeral_runtime_context_key("ADVISOR_DEVICE_ID"))
        self.assertFalse(is_ephemeral_runtime_context_key("TTFUND_DEVICE_ID"))
        self.assertFalse(
            is_ephemeral_runtime_context_key("TTFUND_CURRENT_HOLDING_DEVICE_IDS")
        )
        self.assertFalse(is_ephemeral_runtime_context_key("HTTPS_PROXY"))

    @staticmethod
    def manifest(directory: Path, *, attempts: int = 1, timeout: int = 30) -> dict:
        return {
            "schemaVersion": 1,
            "id": "fixture",
            "name": "fixture",
            "phase": "test",
            "entrypoint": "run.ps1",
            "dependencies": [],
            "criticality": "critical",
            "timeoutSeconds": timeout,
            "maxProcessAttempts": attempts,
            "resourceLock": None,
            "supportsResume": True,
            "progressUnit": "items",
            "validator": {"type": "node_result", "requiredStatus": "passed"},
            "_directory": str(directory),
        }

    @staticmethod
    def runner(root: Path) -> tuple[NodeRunner, StateStore]:
        workspace = load_workspace(root)
        state = StateStore(workspace.state_db)
        state.create_run("fixture-run", root / "logs", {"mode": "test"})
        runner = NodeRunner(workspace, state, "fixture-run", root / "run", lambda _message: None)
        return runner, state

    def test_progress_parses_structured_and_legacy_counts(self) -> None:
        self.assertEqual(parse_progress('PROGRESS {"completed":2,"total":5}')["completed"], 2)
        self.assertEqual(parse_progress("progress 3/9 strategies")["total"], 9)

    def test_state_store_records_node_attempt_and_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = StateStore(root / "state.sqlite")
            state.create_run("run1", root / "logs", {"mode": "test"})
            state.start_node("run1", "node1", 1, "input", root / "node.log", "device")
            result = root / "node_result.json"
            result.write_text("{}", encoding="utf-8")
            state.finish_node("run1", "node1", 1, "success", 0, result, "output", None)
            state.record_artifacts(
                "run1", "node1", [{"key": "summary", "path": str(result), "validationStatus": "passed"}]
            )
            state.close()
            connection = sqlite3.connect(root / "state.sqlite")
            self.assertEqual(connection.execute("SELECT status FROM daily_update_node").fetchone()[0], "success")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM daily_update_node_attempt").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM daily_update_artifact").fetchone()[0], 1)
            connection.close()

    def test_state_store_marks_unfinished_previous_runs_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = StateStore(root / "state.sqlite")
            state.create_run("old-run", root / "old", {"mode": "daily"})
            state.start_node("old-run", "source_readiness", 1, "input", root / "old.log", None)
            state.create_run("current-run", root / "current", {"mode": "daily"})
            interrupted = state.interrupt_stale_runs("current-run", "intentional stop detected")
            old_run = state.get_run("old-run")
            current_run = state.get_run("current-run")
            node = state.get_node("old-run", "source_readiness")
            attempt = state.connection.execute(
                "SELECT status,returncode FROM daily_update_node_attempt "
                "WHERE run_id='old-run' AND node_id='source_readiness' AND attempt=1"
            ).fetchone()
            state.close()
            self.assertEqual(interrupted, ["old-run"])
            self.assertEqual(old_run["status"], "interrupted")
            self.assertEqual(node["status"], "interrupted")
            self.assertEqual(node["returncode"], 130)
            self.assertEqual(tuple(attempt), ("interrupted", 130))
            self.assertEqual(current_run["status"], "created")

    def test_state_store_marks_same_run_orphan_interrupted_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = StateStore(root / "state.sqlite")
            state.create_run("same-run", root / "logs", {"mode": "daily"})
            state.start_node("same-run", "collect", 1, "input", root / "node.log", None)
            interrupted = state.interrupt_running_nodes("same-run")
            node = state.get_node("same-run", "collect")
            attempt = state.connection.execute(
                "SELECT status,returncode FROM daily_update_node_attempt "
                "WHERE run_id='same-run' AND node_id='collect' AND attempt=1"
            ).fetchone()
            state.close()
            self.assertEqual(interrupted, ["collect"])
            self.assertEqual(node["status"], "interrupted")
            self.assertEqual(tuple(attempt), ("interrupted", 130))

    def test_workspace_supports_development_and_runtime_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            development = load_workspace(root)
            self.assertEqual(development.code_root, root.resolve())
            config_dir = root / "本机配置"
            config_dir.mkdir()
            (config_dir / "runtime.local.json").write_text(
                json.dumps(
                    {
                        "codeRoot": "程序代码",
                        "databaseRoot": "数据库",
                        "rawRoot": "采集数据/raw",
                        "normalizedRoot": "采集数据/normalized",
                        "logRoot": "运行状态/logs",
                        "outputRoot": "运行状态/outputs",
                        "reportRoot": "结果文件/平台",
                        "publishRoot": "结果文件/发布",
                        "backupRoot": "数据库备份",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            runtime = load_workspace(root)
            self.assertEqual(runtime.code_root, (root / "程序代码").resolve())
            self.assertEqual(runtime.state_db, (root / "数据库" / "update_state.sqlite").resolve())

    def test_zero_exit_with_failed_output_validator_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            (node / "run.ps1").write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "'{\"validation\":{\"status\":\"failed\",\"detail\":\"injected\"}}' | "
                "Set-Content -LiteralPath (Join-Path $NodeRunDir 'node_result.json') -Encoding UTF8\nexit 0\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            try:
                result = runner.run(self.manifest(node), {}, {}, allow_skip=False)
            finally:
                state.close()
            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.status, "failed")

    def test_zero_exit_without_node_result_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            (node / "run.ps1").write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "exit 0\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            try:
                result = runner.run(self.manifest(node), {}, {}, allow_skip=False)
                payload = json.loads(result.result_path.read_text(encoding="utf-8-sig"))
            finally:
                state.close()
            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.status, "failed")
            self.assertIn("node_result.json is missing", payload["error"])

    def test_retry_uses_second_attempt_after_retryable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            (node / "run.ps1").write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "if($NodeRunDir -like '*attempt_01'){exit 1}\n"
                "'{\"validation\":{\"status\":\"passed\"}}' | "
                "Set-Content -LiteralPath (Join-Path $NodeRunDir 'node_result.json') -Encoding UTF8\nexit 0\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            try:
                result = runner.run(self.manifest(node, attempts=2), {}, {}, allow_skip=False)
                attempts = state.connection.execute(
                    "SELECT COUNT(*) FROM daily_update_node_attempt WHERE run_id=? AND node_id=?",
                    ("fixture-run", "fixture"),
                ).fetchone()[0]
            finally:
                state.close()
            self.assertEqual(result.returncode, 0)
            self.assertEqual(attempts, 2)

    def test_rerun_preserves_previous_attempt_directory_and_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            script = node / "run.ps1"
            script.write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "exit 1\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            try:
                first = runner.run(self.manifest(node), {}, {}, allow_skip=False)
                script.write_text(
                    "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                    "'{\"validation\":{\"status\":\"passed\"}}' | "
                    "Set-Content -LiteralPath (Join-Path $NodeRunDir 'node_result.json') -Encoding UTF8\n"
                    "exit 0\n",
                    encoding="utf-8-sig",
                )
                second = runner.run(self.manifest(node), {}, {}, allow_skip=False)
                attempts = state.connection.execute(
                    "SELECT attempt,status FROM daily_update_node_attempt "
                    "WHERE run_id=? AND node_id=? ORDER BY attempt",
                    ("fixture-run", "fixture"),
                ).fetchall()
            finally:
                state.close()
            self.assertNotEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 0)
            self.assertEqual([tuple(row) for row in attempts], [(1, "failed"), (2, "success")])
            self.assertTrue((root / "run" / "nodes" / "fixture" / "attempt_01" / "console.log").is_file())
            self.assertTrue((root / "run" / "nodes" / "fixture" / "attempt_02" / "console.log").is_file())

    def test_runner_persists_artifact_hash_and_node_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            (node / "run.ps1").write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "$artifact=Join-Path $NodeRunDir 'output.txt'\n"
                "Set-Content -LiteralPath $artifact -Value 'ready' -Encoding UTF8\n"
                "$payload=@{validation=@{status='passed'};watermarks=@{latest='2026-07-17'};"
                "artifacts=@(@{key='output';path=$artifact;validationStatus='passed'})}\n"
                "$payload|ConvertTo-Json -Depth 5|Set-Content -LiteralPath "
                "(Join-Path $NodeRunDir 'node_result.json') -Encoding UTF8\nexit 0\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            try:
                result = runner.run(self.manifest(node), {}, {}, allow_skip=False)
                rows = state.connection.execute(
                    "SELECT artifact_key,artifact_hash,watermark FROM daily_update_artifact "
                    "WHERE run_id=? AND node_id=? ORDER BY artifact_key",
                    ("fixture-run", "fixture"),
                ).fetchall()
            finally:
                state.close()
            self.assertEqual(result.returncode, 0)
            self.assertEqual([row[0] for row in rows], ["node_result", "output"])
            self.assertTrue(all(len(row[1]) == 64 for row in rows))
            self.assertIn("2026-07-17", rows[0][2])

    def test_resume_reruns_when_artifact_hash_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            (node / "run.ps1").write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "$artifact=Join-Path $WorkspaceRoot 'stable-output.txt'\n"
                "Set-Content -LiteralPath $artifact -Value $NodeRunDir -Encoding UTF8\n"
                "$payload=@{validation=@{status='passed'};"
                "artifacts=@(@{key='output';path=$artifact;validationStatus='passed'})}\n"
                "$payload|ConvertTo-Json -Depth 5|Set-Content -LiteralPath "
                "(Join-Path $NodeRunDir 'node_result.json') -Encoding UTF8\nexit 0\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            try:
                first = runner.run(self.manifest(node), {}, {}, allow_skip=False)
                artifact = root / "stable-output.txt"
                artifact.write_text("tampered", encoding="utf-8")
                second = runner.run(self.manifest(node), {}, {}, allow_skip=True)
                attempts = state.connection.execute(
                    "SELECT COUNT(*) FROM daily_update_node_attempt WHERE run_id=? AND node_id=?",
                    ("fixture-run", "fixture"),
                ).fetchone()[0]
            finally:
                state.close()
            self.assertEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 0)
            self.assertEqual(attempts, 2)
            self.assertNotEqual(artifact.read_text(encoding="utf-8-sig"), "tampered")

    def test_explicit_from_node_restore_reuses_intact_result_after_code_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            (node / "run.ps1").write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "$artifact=Join-Path $NodeRunDir 'output.txt'\n"
                "Set-Content -LiteralPath $artifact -Value 'ready' -Encoding UTF8\n"
                "$payload=@{validation=@{status='passed'};"
                "contextUpdates=@{RECOVERED='yes'};"
                "artifacts=@(@{key='output';path=$artifact;validationStatus='passed'})}\n"
                "$payload|ConvertTo-Json -Depth 5|Set-Content -LiteralPath "
                "(Join-Path $NodeRunDir 'node_result.json') -Encoding UTF8\nexit 0\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            try:
                first = runner.run(self.manifest(node), {}, {}, allow_skip=False)
                (root / "config").mkdir()
                (root / "config" / "changed.json").write_text('{"changed":true}\n', encoding="utf-8")
                changed_runner = NodeRunner(
                    runner.workspace,
                    state,
                    "fixture-run",
                    root / "run",
                    lambda _message: None,
                )
                self.assertNotEqual(runner.code_fingerprint, changed_runner.code_fingerprint)
                restored = changed_runner.restore_previous(self.manifest(node))
                attempts = state.connection.execute(
                    "SELECT COUNT(*) FROM daily_update_node_attempt "
                    "WHERE run_id=? AND node_id=?",
                    ("fixture-run", "fixture"),
                ).fetchone()[0]
            finally:
                state.close()
            self.assertEqual(restored.output_fingerprint, first.output_fingerprint)
            self.assertEqual(restored.context_updates["RECOVERED"], "yes")
            self.assertEqual(attempts, 1)

    def test_explicit_from_node_restore_falls_back_to_intact_earlier_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            (node / "run.ps1").write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "$artifact=Join-Path $NodeRunDir 'output.txt'\n"
                "Set-Content -LiteralPath $artifact -Value 'ready' -Encoding UTF8\n"
                "$payload=@{validation=@{status='passed'};"
                "contextUpdates=@{RECOVERED='earlier'};"
                "artifacts=@(@{key='output';path=$artifact;validationStatus='passed'})}\n"
                "$payload|ConvertTo-Json -Depth 5|Set-Content -LiteralPath "
                "(Join-Path $NodeRunDir 'node_result.json') -Encoding UTF8\nexit 0\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            manifest = self.manifest(node)
            try:
                first = runner.run(manifest, {}, {}, allow_skip=False)
                # Simulate a pre-migration successful attempt whose fingerprint
                # was not stored before a later attempt became interrupted.
                state.connection.execute(
                    "UPDATE daily_update_node_attempt SET output_fingerprint=NULL "
                    "WHERE run_id=? AND node_id=? AND attempt=1",
                    ("fixture-run", "fixture"),
                )
                state.connection.commit()
                state.start_node(
                    "fixture-run",
                    "fixture",
                    2,
                    "new-input",
                    root / "attempt_02.log",
                    None,
                )
                state.interrupt_running_nodes("fixture-run")
                restored = runner.restore_previous(manifest)
            finally:
                state.close()
            self.assertEqual(first.status, "success")
            self.assertEqual(restored.status, "success")
            self.assertEqual(restored.context_updates["RECOVERED"], "earlier")

    def test_explicit_from_node_restore_rejects_tampered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            (node / "run.ps1").write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "$artifact=Join-Path $NodeRunDir 'output.txt'\n"
                "Set-Content -LiteralPath $artifact -Value 'ready' -Encoding UTF8\n"
                "$payload=@{validation=@{status='passed'};"
                "artifacts=@(@{key='output';path=$artifact;validationStatus='passed'})}\n"
                "$payload|ConvertTo-Json -Depth 5|Set-Content -LiteralPath "
                "(Join-Path $NodeRunDir 'node_result.json') -Encoding UTF8\nexit 0\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            try:
                result = runner.run(self.manifest(node), {}, {}, allow_skip=False)
                Path(json.loads(result.result_path.read_text(encoding="utf-8-sig"))["artifacts"][0]["path"]).write_text(
                    "tampered",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(RuntimeError, "no intact validated result"):
                    runner.restore_previous(self.manifest(node))
            finally:
                state.close()

    def test_explicit_from_node_restore_preserves_optional_channel_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            (node / "run.ps1").write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "$artifact=Join-Path $NodeRunDir 'partial.txt'\n"
                "Set-Content -LiteralPath $artifact -Value 'partial' -Encoding UTF8\n"
                "$payload=@{validation=@{status='failed';detail='partial channel'};"
                "artifacts=@(@{key='partial';path=$artifact;validationStatus='failed'})}\n"
                "$payload|ConvertTo-Json -Depth 5|Set-Content -LiteralPath "
                "(Join-Path $NodeRunDir 'node_result.json') -Encoding UTF8\nexit 20\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            manifest = self.manifest(node)
            manifest.update({"criticality": "optional", "failureImpact": "channel"})
            try:
                first = runner.run(manifest, {}, {}, allow_skip=False)
                restored = runner.restore_previous(manifest)
                attempts = state.connection.execute(
                    "SELECT COUNT(*) FROM daily_update_node_attempt "
                    "WHERE run_id=? AND node_id=?",
                    ("fixture-run", "fixture"),
                ).fetchone()[0]
            finally:
                state.close()
            self.assertEqual(first.status, "failed")
            self.assertEqual(restored.status, "failed")
            self.assertEqual(restored.returncode, 20)
            self.assertEqual(attempts, 1)

    def test_explicit_from_node_restore_rejects_critical_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            (node / "run.ps1").write_text(
                "param([string]$WorkspaceRoot,[string]$RunId,[string]$NodeRunDir,[switch]$DryRun)\n"
                "'{\"validation\":{\"status\":\"failed\",\"detail\":\"critical failure\"}}' | "
                "Set-Content -LiteralPath (Join-Path $NodeRunDir 'node_result.json') -Encoding UTF8\n"
                "exit 20\n",
                encoding="utf-8-sig",
            )
            runner, state = self.runner(root)
            try:
                result = runner.run(self.manifest(node), {}, {}, allow_skip=False)
                self.assertEqual(result.status, "failed")
                with self.assertRaisesRegex(RuntimeError, "no intact validated result"):
                    runner.restore_previous(self.manifest(node))
            finally:
                state.close()

    def test_explicit_from_node_restore_preserves_validated_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node"
            node.mkdir()
            runner, state = self.runner(root)
            manifest = self.manifest(node)
            try:
                skipped = runner.skip(manifest, {}, "dependency unavailable")
                restored = runner.restore_previous(manifest)
            finally:
                state.close()
            self.assertEqual(skipped.status, "skipped")
            self.assertEqual(restored.status, "skipped")
            self.assertEqual(restored.error, "dependency unavailable")

    def test_resource_lock_rejects_active_owner_and_reclaims_stale_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = load_workspace(root)
            lock, token = acquire_resource_lock(workspace, "device", "run1", "node1")
            try:
                with self.assertRaises(RuntimeError):
                    acquire_resource_lock(workspace, "device", "run2", "node2")
            finally:
                release_resource_lock(lock, token)
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"pid": 999999999, "token": "stale"}), encoding="utf-8")
            reclaimed, reclaimed_token = acquire_resource_lock(workspace, "device", "run3", "node3")
            try:
                self.assertEqual(json.loads(reclaimed.read_text(encoding="utf-8"))["pid"], os.getpid())
            finally:
                release_resource_lock(reclaimed, reclaimed_token)

    def test_critical_node_can_depend_on_optional_node_for_degraded_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "run.ps1").write_text("exit 0\n", encoding="utf-8-sig")
            (directory / "SKILL.md").write_text("# fixture\n", encoding="utf-8")
            optional = self.manifest(directory)
            optional.update({"id": "optional", "criticality": "optional"})
            critical = self.manifest(directory)
            critical.update({"id": "critical", "dependencies": ["optional"]})
            validate_pipeline([optional, critical])

    def test_failed_optional_dependency_requires_explicit_degraded_opt_in(self) -> None:
        result = NodeExecution(
            "optional",
            "failed",
            20,
            Path("result.json"),
            "hash",
            {},
            "fixture failure",
        )
        optional = {"id": "optional", "criticality": "optional", "dependencies": []}
        strict = {"id": "strict", "criticality": "critical", "dependencies": ["optional"]}
        degraded = {
            "id": "degraded",
            "criticality": "critical",
            "dependencies": ["optional"],
            "allowFailedOptionalDependencies": True,
        }
        self.assertEqual(
            dependency_blockers(strict, {"optional": optional}, {"optional": result}),
            ["optional"],
        )
        self.assertEqual(
            dependency_blockers(degraded, {"optional": optional}, {"optional": result}),
            [],
        )
        self.assertEqual(
            dependency_blockers(strict, {"optional": optional}, {}, []),
            [],
        )

    def test_run_classification_separates_channel_publish_and_critical_failures(self) -> None:
        result_path = Path("result.json")

        def execution(node_id: str, status: str, returncode: int) -> NodeExecution:
            return NodeExecution(node_id, status, returncode, result_path, node_id, {}, None)

        nodes = [
            {"id": "channel", "criticality": "optional", "failureImpact": "channel"},
            {"id": "core", "criticality": "critical"},
            {"id": "publish", "criticality": "publish"},
        ]
        status, _, _, channel_failures = classify_run(
            nodes,
            {
                "channel": execution("channel", "failed", 20),
                "core": execution("core", "success", 0),
                "publish": execution("publish", "success", 0),
            },
            {},
        )
        self.assertEqual(status, "partial_success")
        self.assertEqual(channel_failures, ["channel"])

        status, critical, _, _ = classify_run(
            nodes,
            {
                "channel": execution("channel", "success", 0),
                "core": execution("core", "failed", 1),
                "publish": execution("publish", "skipped", 0),
            },
            {},
        )
        self.assertEqual(status, "failed_critical")
        self.assertEqual(critical, ["core"])

    def test_daily_run_continues_to_aggregate_after_channel_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = load_workspace(root)
            run_dir = root / "logs" / "daily_update" / "2026-07-23" / "run"
            run_dir.mkdir(parents=True)
            state = StateStore(workspace.state_db)
            state.create_run("run", run_dir, {"mode": "daily", "dryRun": False})
            channel = {
                "id": "channel",
                "name": "channel",
                "dependencies": [],
                "criticality": "optional",
                "failureImpact": "channel",
            }
            aggregate = {
                "id": "aggregate",
                "name": "aggregate",
                "dependencies": ["channel"],
                "criticality": "critical",
                "allowFailedOptionalDependencies": True,
            }

            class FakeRunner:
                def __init__(self, *_args, **_kwargs):
                    pass

                def run(self, manifest, _dependencies, _context, *, allow_skip):
                    self.assert_allow_skip = allow_skip
                    if manifest["id"] == "channel":
                        return NodeExecution(
                            "channel",
                            "failed",
                            20,
                            root / "channel.json",
                            "channel-hash",
                            {},
                            "temporary channel failure",
                        )
                    return NodeExecution(
                        "aggregate",
                        "success",
                        0,
                        root / "aggregate.json",
                        "aggregate-hash",
                        {},
                        None,
                    )

                def skip(self, *_args, **_kwargs):
                    raise AssertionError("aggregate should run in degraded mode")

            orchestrator = Orchestrator.__new__(Orchestrator)
            orchestrator.args = type(
                "Args",
                (),
                {"mode": "daily", "dry_run": False, "standalone": False},
            )()
            orchestrator.workspace = workspace
            orchestrator.pipeline = {"version": "fixture"}
            orchestrator.nodes = [channel, aggregate]
            orchestrator.by_id = {"channel": channel, "aggregate": aggregate}
            orchestrator.run_id = "run"
            orchestrator.run_dir = run_dir
            orchestrator.console_path = run_dir / "console.log"
            orchestrator.events_path = run_dir / "events.jsonl"
            orchestrator.summary_path = run_dir / "summary.json"
            orchestrator.summary_md_path = run_dir / "summary.md"
            orchestrator.state = state
            orchestrator.runtime_context = {}
            orchestrator.results = {}
            orchestrator.started = time.monotonic()

            with patch.object(orchestrator_module, "NodeRunner", FakeRunner):
                returncode = orchestrator.run()

            summary = json.loads(orchestrator.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(returncode, 2)
            self.assertEqual(summary["status"], "partial_success")
            self.assertEqual(summary["completedNodes"], ["channel", "aggregate"])
            self.assertEqual(summary["failedChannelNodes"], ["channel"])
            state.close()

    def test_standalone_selection_runs_only_target_node(self) -> None:
        orchestrator = Orchestrator.__new__(Orchestrator)
        dependency = {"id": "dependency", "dependencies": [], "resourceLock": None}
        target = {"id": "target", "dependencies": ["dependency"], "resourceLock": None}
        orchestrator.nodes = [dependency, target]
        orchestrator.by_id = {node["id"]: node for node in orchestrator.nodes}
        orchestrator.args = type(
            "Args",
            (),
            {"mode": "node", "node_id": "target", "standalone": True},
        )()
        self.assertEqual([node["id"] for node in orchestrator.selected_nodes()], ["target"])

    def test_standalone_selection_rejects_main_database_writer(self) -> None:
        orchestrator = Orchestrator.__new__(Orchestrator)
        target = {"id": "target", "dependencies": [], "resourceLock": "main_db_write"}
        orchestrator.nodes = [target]
        orchestrator.by_id = {"target": target}
        orchestrator.args = type(
            "Args",
            (),
            {"mode": "node", "node_id": "target", "standalone": True},
        )()
        with self.assertRaisesRegex(ValueError, "cannot run standalone"):
            orchestrator.selected_nodes()

    def test_bounded_resume_stops_after_requested_node(self) -> None:
        orchestrator = Orchestrator.__new__(Orchestrator)
        orchestrator.nodes = [
            {"id": "collect", "daily": True},
            {"id": "governance", "daily": True},
            {"id": "audit", "daily": True},
            {"id": "backup", "daily": True},
            {"id": "publish", "daily": True},
        ]
        orchestrator.args = type(
            "Args",
            (),
            {"mode": "resume", "from_node": "governance", "to_node": "backup"},
        )()
        self.assertEqual(
            [node["id"] for node in orchestrator.selected_nodes()],
            ["collect", "governance", "audit", "backup"],
        )

    def test_real_resume_rejects_dry_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = StateStore(root / "data" / "update_state.sqlite")
            state.create_run("dry-run", root / "logs", {"mode": "daily", "dryRun": True})
            state.close()
            node_root = root / "节点脚本"
            fixture = node_root / "fixture"
            fixture.mkdir(parents=True)
            (fixture / "run.ps1").write_text("exit 0\n", encoding="utf-8-sig")
            (fixture / "SKILL.md").write_text("# fixture\n", encoding="utf-8")
            manifest = self.manifest(fixture)
            manifest.pop("_directory")
            (fixture / "node.json").write_text(json.dumps(manifest), encoding="utf-8")
            (node_root / "pipeline.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "version": "fixture",
                        "nodes": [
                            {
                                "id": "fixture",
                                "directory": "fixture",
                                "dependencies": [],
                                "enabledWhen": {"daily": True},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = type(
                "Args",
                (),
                {
                    "workspace_root": root,
                    "mode": "resume",
                    "run_id": "dry-run",
                    "node_id": None,
                    "dry_run": False,
                },
            )()
            with self.assertRaisesRegex(RuntimeError, "dry-run results"):
                Orchestrator(args)

    def test_code_fingerprint_uses_content_not_only_size_or_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_dir = root / "config"
            config_dir.mkdir(parents=True)
            source = config_dir / "fixture.json"
            source.write_text('{"value":1}\n', encoding="utf-8")
            original_stat = source.stat()
            workspace = load_workspace(root)
            state = StateStore(workspace.state_db)
            first = NodeRunner(workspace, state, "run1", root / "run1", lambda _message: None)

            os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000))
            touched = NodeRunner(workspace, state, "run2", root / "run2", lambda _message: None)
            self.assertEqual(first.code_fingerprint, touched.code_fingerprint)

            source.write_text('{"value":2}\n', encoding="utf-8")
            os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            changed = NodeRunner(workspace, state, "run3", root / "run3", lambda _message: None)
            self.assertNotEqual(first.code_fingerprint, changed.code_fingerprint)
            state.close()


if __name__ == "__main__":
    unittest.main()
