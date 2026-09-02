from __future__ import annotations

import json
import contextlib
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

from build_minimal_publish_set import ensure_safe_target
from build_runtime_migration_package import (
    RUNTIME_ROOT_FILES,
    SPARSE_PATTERNS,
    STATIC_REPORT_ASSET_DIR,
    STATIC_REPORT_PAGE,
    clone_sparse_code,
    copy_qieman_accepted_baseline,
    copy_report_seed,
    database_summary,
    runtime_file_excluded,
    runtime_path,
    sanitize_baseline_payload,
    write_checksums,
)
from check_runtime_path_portability import scan
from runtime_workspace import (
    DEFAULT_CONFIG,
    create_default_config,
    ensure_junction,
    load_workspace,
    normalize_relative_path,
    render_legacy_local_bat,
    sqlite_backup,
)
from runtime_workspace_cli import (
    adb_device_health,
    apply_database_migrations,
    ensure_publish_git_identity,
    plan_database_migrations,
    select_device,
    verify_declared_runtime_baselines,
    verify_workspace_checksums,
)
from unittest.mock import patch


class WorkspacePathTests(unittest.TestCase):
    def test_adb_health_rejects_loopback_emulator_serial(self) -> None:
        def completed(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            joined = " ".join(command)
            stdout = ""
            if joined.endswith("get-state"):
                stdout = "device\n"
            elif "pm path" in joined:
                stdout = "package:/data/app/ttfund.apk\n"
            elif "ls -d" in joined:
                stdout = "/sdcard/Android/data/com.eastmoney.android.fund/files/.ttjj_cache\n"
            elif "ls -1" in joined:
                stdout = "strategyDetailPageDataA_app.0\n"
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        with patch("runtime_workspace_cli.run_command", side_effect=completed):
            result = adb_device_health(Path("adb.exe"), "127.0.0.1:16384", launch=False)

        self.assertTrue(result["isEmulator"])
        self.assertFalse(result["ready"])
        self.assertIn("emulator_device_not_allowed", result["blockedReasons"])

    def test_rejects_absolute_and_parent_paths(self) -> None:
        for value in ("C:/runtime/data", "C:\\runtime\\data", "../outside", "/outside", "//server/share"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_relative_path(value, "testPath")

    def test_loads_all_paths_under_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = create_default_config(root)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["physicalDeviceId"] = "device-1"
            config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            layout = load_workspace(root)
            for path in (
                layout.code_root,
                layout.database_root,
                layout.raw_root,
                layout.normalized_root,
                layout.report_root,
                layout.publish_root,
                layout.backup_root,
            ):
                self.assertIn(root.resolve(), path.parents)
            local_bat = render_legacy_local_bat(layout)
            self.assertNotRegex(local_bat, r"[A-Za-z]:[\\/]")
            self.assertIn("%ADVISOR_WORKSPACE_ROOT%", local_bat)

    def test_runtime_daily_selects_only_physical_phone(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = create_default_config(root)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["physicalDeviceId"] = "physical-1"
            config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            layout = load_workspace(root)
            with patch(
                "runtime_workspace_cli.resolve_physical",
                return_value={"deviceType": "physical", "deviceId": "physical-1", "ready": True},
            ) as physical:
                result = select_device(layout)

        self.assertEqual(result["deviceMode"], "physical_only")
        self.assertEqual(result["selected"]["deviceType"], "physical")
        self.assertIsNone(result["fallback"])
        self.assertEqual([item["deviceType"] for item in result["attempts"]], ["physical"])
        physical.assert_called_once()

    def test_runtime_daily_blocks_when_physical_phone_is_offline(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = create_default_config(root)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["physicalDeviceId"] = "physical-1"
            config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            layout = load_workspace(root)
            with patch(
                "runtime_workspace_cli.resolve_physical",
                return_value={
                    "deviceType": "physical",
                    "deviceId": "physical-1",
                    "ready": False,
                    "stateOutput": "not found",
                },
            ), self.assertRaises(RuntimeError):
                select_device(layout)

    def test_runtime_config_rejects_emulator_priority(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = create_default_config(root)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["devicePriority"] = ["mumu", "physical"]
            config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "physical"):
                load_workspace(root)

    @unittest.skipUnless(os.name == "nt", "junctions are a Windows compatibility mechanism")
    def test_junction_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            link = root / "link"
            ensure_junction(link, target)
            ensure_junction(link, target)
            self.assertEqual(link.resolve(), target.resolve())


class RuntimePackageTests(unittest.TestCase):
    def test_runtime_git_scope(self) -> None:
        self.assertTrue(runtime_path("节点脚本/_共享组件/生产程序/runtime_workspace.py"))
        self.assertTrue(runtime_path("basic_data/strategy.html"))
        self.assertFalse(runtime_path("basic_data/data/basic_summary.js"))
        self.assertFalse(runtime_path("site/basic_data/strategies.html"))

    def test_publish_target_must_be_expected_leaf(self) -> None:
        with TemporaryDirectory() as directory:
            parent = Path(directory)
            ensure_safe_target(parent / "最小发布集", parent)
            with self.assertRaises(RuntimeError):
                ensure_safe_target(parent / "unexpected", parent)
            with self.assertRaises(RuntimeError):
                ensure_safe_target(parent / "nested" / "最小发布集", parent)

    def test_portability_scan_detects_old_machine_path(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            program = root / "节点脚本" / "_共享组件" / "生产程序"
            program.mkdir(parents=True)
            (program / "bad.py").write_text("ROOT = r'E:\\\\synctingData\\\\old'\n", encoding="utf-8")
            result = scan(root)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["issueCount"], 1)

    def test_portability_scan_detects_lowercase_legacy_scripts_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            program = root / "节点脚本" / "_共享组件" / "生产程序"
            program.mkdir(parents=True)
            (program / "bad.py").write_text(
                'resolver = project_root / "scripts" / "resolve_mumu_device.py"\n',
                encoding="utf-8",
            )
            result = scan(root)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["issueCount"], 1)

    def test_default_config_is_relative(self) -> None:
        for key, value in DEFAULT_CONFIG.items():
            if key.endswith("Root"):
                self.assertFalse(Path(str(value)).is_absolute(), key)
        self.assertEqual(DEFAULT_CONFIG["devicePriority"], ["physical"])
        self.assertNotIn("mumuVmIndex", DEFAULT_CONFIG)

    def test_runtime_root_dependencies_are_in_sparse_package(self) -> None:
        for name in (
            "00_每日数据更新并发布_唯一入口.bat",
            "AGENTS.md",
            "README_AI.md",
        ):
            with self.subTest(name=name):
                self.assertIn(name, RUNTIME_ROOT_FILES)
                self.assertIn(f"/{name}", SPARSE_PATTERNS)

    def test_report_seed_excludes_monthly_content_from_minimal_runtime(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            (source / "basic_data" / "assets" / STATIC_REPORT_ASSET_DIR).mkdir(parents=True)
            (source / "basic_data" / STATIC_REPORT_PAGE).write_text("ready", encoding="utf-8")
            (source / "basic_data" / "assets" / STATIC_REPORT_ASSET_DIR / "chart.png").write_text("chart", encoding="utf-8")
            (source / "basic_data" / "index.html").write_text("rebuildable", encoding="utf-8")
            (source / ".git").mkdir()
            (source / ".git" / "config").write_text("history", encoding="utf-8")
            (source / "reports").mkdir()
            (source / "reports" / "old.xlsx").write_text("old", encoding="utf-8")
            result = copy_report_seed(source, target)
            self.assertFalse(target.exists())
            self.assertEqual(result["assetFileCount"], 0)
            self.assertEqual(result["status"], "rebuild_required")
            self.assertEqual(result["reason"], "formal_reports_are_rebuilt_from_the_database")

    def test_report_seed_can_be_rebuilt_when_monthly_content_is_absent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = copy_report_seed(root / "source", root / "target")
            self.assertEqual(result["status"], "rebuild_required")
            self.assertEqual(result["assetFileCount"], 0)
            self.assertFalse((root / "target").exists())

    def test_runtime_snapshot_excludes_qieman_reverse_engineering_artifacts(self) -> None:
        self.assertTrue(
            runtime_file_excluded(
                Path("official_apps/qieman/authenticated_probe/_analysis/apk/index.bundle")
            )
        )
        self.assertFalse(
            runtime_file_excluded(
                Path("official_apps/qieman/authenticated_probe/qieman_stargate_sms_session.js")
            )
        )

    def test_qieman_accepted_baseline_is_copied_with_relative_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            run_id = "daily__qieman_collect__attempt_01"
            run_dir = source / "qieman" / "signed_history" / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "summary.json").write_text("{}", encoding="utf-8")
            state = {
                "run_id": run_id,
                "history_run_dir": str(run_dir),
                "summary_path": str(root / "normalized" / "summary.json"),
            }
            (source / "qieman" / "accepted_state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )

            result = copy_qieman_accepted_baseline(source, target)

            self.assertIsNotNone(result)
            copied = json.loads((target / "qieman" / "accepted_state.json").read_text(encoding="utf-8"))
            self.assertEqual(copied["history_run_dir"], f"signed_history/{run_id}")
            self.assertTrue((target / "qieman" / "signed_history" / run_id / "summary.json").is_file())

    def test_checksum_verification_detects_changed_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            create_default_config(root)
            layout = load_workspace(root)
            payload = root / "数据库" / "sample.txt"
            payload.parent.mkdir(parents=True)
            payload.write_text("ready", encoding="utf-8")
            digest = __import__("hashlib").sha256(payload.read_bytes()).hexdigest()
            layout.baseline_root.mkdir(parents=True)
            manifest = layout.baseline_root / "checksums.sha256"
            manifest.write_text(f"{digest}  数据库/sample.txt\n", encoding="utf-8")
            self.assertEqual(verify_workspace_checksums(layout)["status"], "ready")
            payload.write_text("changed", encoding="utf-8")
            result = verify_workspace_checksums(layout)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["errorCount"], 1)

    def test_checksum_manifest_excludes_mutable_runtime_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_db = root / "数据库" / "update_state.sqlite"
            main_db = root / "数据库" / "analysis_zh_current.sqlite"
            runtime_log = root / "运行状态" / "logs" / "run.log"
            runtime_health = root / "数据库" / "runtime_health" / "health.json"
            database_wal = root / "数据库" / "analysis_zh_current.sqlite-wal"
            bytecode = root / "程序代码" / "节点脚本" / "_共享组件" / "生产程序" / "__pycache__" / "runtime.cpython-312.pyc"
            pytest_cache = root / "程序代码" / ".pytest_cache" / "state"
            for path in (state_db, main_db, runtime_log, runtime_health, database_wal, bytecode, pytest_cache):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(path.name, encoding="utf-8")
            manifest = write_checksums(root).read_text(encoding="utf-8")
            self.assertNotIn("数据库/update_state.sqlite", manifest)
            self.assertNotIn("运行状态/logs/run.log", manifest)
            self.assertNotIn("数据库/runtime_health/health.json", manifest)
            self.assertNotIn("数据库/analysis_zh_current.sqlite-wal", manifest)
            self.assertNotIn("__pycache__", manifest)
            self.assertNotIn(".pytest_cache", manifest)
            self.assertIn("数据库/analysis_zh_current.sqlite", manifest)

    def test_declared_runtime_baselines_accept_complete_mutable_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            create_default_config(root)
            layout = load_workspace(root)
            normalized = layout.normalized_root / "ttfund" / "collection_summary" / "2026-07-22" / "run.json"
            tree_file = layout.raw_root / "device_cache" / "one.json"
            latest_file = layout.raw_root / "ttfund" / "loggedin_cache" / "2026-07-22" / "run-1" / "one.json"
            raw_file = layout.raw_root / "fund_f10_benchmark" / "latest.json"
            for path in (normalized, tree_file, latest_file, raw_file):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("ready", encoding="utf-8")
            layout.baseline_root.mkdir(parents=True)
            (layout.baseline_root / "migration_manifest.json").write_text(
                json.dumps(
                    {
                        "normalizedBaselines": [{"files": ["ttfund/collection_summary/2026-07-22/run.json"]}],
                        "rawSelection": {
                            "trees": [{"path": "device_cache", "fileCount": 1}],
                            "latestRuns": [
                                {"path": "ttfund/loggedin_cache", "run": "run-1", "fileCount": 1}
                            ],
                            "files": ["fund_f10_benchmark/latest.json"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = verify_declared_runtime_baselines(layout)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["normalizedPresentFileCount"], 1)
            self.assertEqual(result["rawLatestRuns"][0]["actualFileCount"], 1)

    def test_declared_runtime_baselines_block_missing_or_incomplete_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            create_default_config(root)
            layout = load_workspace(root)
            tree_file = layout.raw_root / "device_cache" / "one.json"
            tree_file.parent.mkdir(parents=True)
            tree_file.write_text("ready", encoding="utf-8")
            layout.baseline_root.mkdir(parents=True)
            (layout.baseline_root / "migration_manifest.json").write_text(
                json.dumps(
                    {
                        "normalizedBaselines": [{"files": ["ttfund/collection_summary/missing.json"]}],
                        "rawSelection": {
                            "trees": [{"path": "device_cache", "fileCount": 2}],
                            "latestRuns": [],
                            "files": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = verify_declared_runtime_baselines(layout)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(any(error.startswith("normalized_baseline_missing:") for error in result["errors"]))
            self.assertTrue(any(error.startswith("raw_tree_incomplete:") for error in result["errors"]))

    def test_declared_runtime_baselines_allow_empty_optional_entity_only(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            create_default_config(root)
            layout = load_workspace(root)
            optional = layout.normalized_root / "gfsec_fima" / "strategy_rebalance_event" / "run.jsonl"
            core = layout.normalized_root / "gfsec_fima" / "strategy_performance_daily" / "run.jsonl"
            optional.parent.mkdir(parents=True)
            core.parent.mkdir(parents=True)
            optional.write_bytes(b"")
            core.write_bytes(b"")
            layout.baseline_root.mkdir(parents=True)
            (layout.baseline_root / "migration_manifest.json").write_text(
                json.dumps(
                    {
                        "normalizedBaselines": [
                            {
                                "files": [
                                    "gfsec_fima/strategy_rebalance_event/run.jsonl",
                                    "gfsec_fima/strategy_performance_daily/run.jsonl",
                                ]
                            }
                        ],
                        "rawSelection": {},
                    }
                ),
                encoding="utf-8",
            )
            result = verify_declared_runtime_baselines(layout)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["normalizedOptionalEmptyFileCount"], 1)
            self.assertTrue(any(error.startswith("normalized_core_baseline_empty:") for error in result["errors"]))

    def test_database_migration_requires_complete_transactional_chain(self) -> None:
        payload = {
            "minimumDatabaseUserVersion": 3,
            "maximumDatabaseUserVersion": 3,
            "databaseMigrations": [
                {"fromVersion": 1, "toVersion": 2, "script": "config/runtime/migrations/1_2.sql", "transactional": True, "idempotent": True},
                {"fromVersion": 2, "toVersion": 3, "script": "config/runtime/migrations/2_3.sql", "transactional": True, "idempotent": True},
            ],
        }
        self.assertEqual([item["toVersion"] for item in plan_database_migrations(payload, 1)], [2, 3])
        payload["databaseMigrations"].pop()
        with self.assertRaises(RuntimeError):
            plan_database_migrations(payload, 1)

    def test_database_migration_runs_after_successful_backup(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            create_default_config(root)
            layout = load_workspace(root)
            (layout.code_root / "config" / "runtime" / "migrations").mkdir(parents=True)
            layout.database_root.mkdir(parents=True)
            with contextlib.closing(sqlite3.connect(layout.main_db)) as connection:
                connection.execute("CREATE TABLE seed(id INTEGER PRIMARY KEY)")
                connection.execute("PRAGMA user_version = 1")
                connection.commit()
            migration_script = layout.code_root / "config" / "runtime" / "migrations" / "1_2.sql"
            migration_script.write_text("CREATE TABLE IF NOT EXISTS migrated(id INTEGER PRIMARY KEY);\n", encoding="utf-8")
            compatibility = {
                "minimumDatabaseUserVersion": 2,
                "maximumDatabaseUserVersion": 2,
                "migrationPolicy": "transactional-idempotent-only",
                "databaseMigrations": [
                    {"fromVersion": 1, "toVersion": 2, "script": "config/runtime/migrations/1_2.sql", "transactional": True, "idempotent": True}
                ],
            }
            (layout.code_root / "config" / "runtime" / "runtime_compatibility.json").write_text(json.dumps(compatibility), encoding="utf-8")
            backup = layout.backup_root / "baseline.sqlite"
            sqlite_backup(layout.main_db, backup)
            self.assertFalse(Path(f"{backup}-shm").exists())
            self.assertFalse(Path(f"{backup}-wal").exists())
            self.assertFalse(any(backup.parent.glob(f".{backup.name}.*.tmp*")))
            (layout.backup_root / "analysis_zh_current_baseline.json").write_text(
                json.dumps({"status": "success", "backup_file": backup.name}), encoding="utf-8"
            )
            result = apply_database_migrations(layout)
            with contextlib.closing(sqlite3.connect(layout.main_db)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name='migrated'").fetchone())
            Path(result["rollbackSnapshot"]).unlink(missing_ok=True)

    def test_baseline_paths_are_rewritten_to_workspace_relative_paths(self) -> None:
        payload = {
            "run": str(ROOT / "logs" / "daily_update" / "summary.json"),
            "report": "Z:/legacy/report/basic_data/index.html",
            "unrelated": "ready",
        }
        sanitized = sanitize_baseline_payload(payload, Path("Z:/legacy/report"))
        self.assertEqual(sanitized["run"], "运行状态/logs/daily_update/summary.json")
        self.assertEqual(sanitized["report"], "结果文件/全市场投顾分析平台/basic_data/index.html")
        self.assertEqual(sanitized["unrelated"], "ready")

    def test_database_summary_uses_real_fund_nav_date_column(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.sqlite"
            with contextlib.closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE "基金日度净值" ("交易日期" TEXT)')
                connection.executemany(
                    'INSERT INTO "基金日度净值" ("交易日期") VALUES (?)',
                    [("2026-07-20",), ("2026-07-21",), ("not-a-date",)],
                )
                connection.commit()
            summary = database_summary(path)
            self.assertEqual(summary["latestBusinessDates"]["基金日度净值"], "2026-07-21")
            cached_summary = database_summary(path, quick_check_result="ok")
            self.assertEqual(cached_summary["quickCheck"], "ok")

    @unittest.skipUnless(shutil.which("git"), "git is required for publish identity setup")
    def test_publish_git_identity_is_configured_without_user_input(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            create_default_config(root)
            layout = load_workspace(root)
            layout.publish_root.mkdir(parents=True)
            subprocess.run(["git", "init"], cwd=layout.publish_root, check=True, capture_output=True)
            ensure_publish_git_identity(layout)
            name = subprocess.run(
                ["git", "config", "--local", "user.name"],
                cwd=layout.publish_root,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout.strip()
            email = subprocess.run(
                ["git", "config", "--local", "user.email"],
                cwd=layout.publish_root,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout.strip()
            self.assertEqual(name, "天眼系统自动更新")
            self.assertEqual(email, "tianyan-system@local.invalid")

    def test_migrated_daily_entry_is_zero_argument_and_self_bootstrapping(self) -> None:
        daily_entry = ROOT / "00_每日数据更新并发布_唯一入口.bat"
        raw_content = daily_entry.read_bytes()
        content = daily_entry.read_text(encoding="ascii")
        self.assertEqual(raw_content.count(b"\n"), raw_content.count(b"\r\n"))
        self.assertFalse(raw_content.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(all(byte < 128 for byte in raw_content))
        self.assertIn("setlocal EnableExtensions DisableDelayedExpansion", content)
        self.assertIn("pause >nul", content.lower())
        self.assertIn("daily_update_launcher.ps1", content)
        self.assertIn("set \"MODE=interactive\"", content)
        self.assertIn("set \"MODE=resumeLatest\"", content)
        self.assertIn("-DryRun", content)
        self.assertIn("-NodeId", content)
        self.assertIn("-Standalone", content)
        launcher = (ROOT / "节点脚本" / "00_调度框架" / "启动.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("install_runtime_environment.ps1", launcher)
        self.assertIn("PYTHONUNBUFFERED", launcher)
        self.assertIn("Start-Transcript", launcher)
        self.assertIn("$arguments += $Mode", launcher)
        self.assertIn("$arguments += '--standalone'", launcher)
        self.assertIn("$arguments += @('--from-node', $ResumeFromNode)", launcher)
        self.assertIn("$arguments += @('--to-node', $ResumeToNode)", launcher)
        self.assertIn("resume_plan.py", launcher)
        self.assertIn("续作推荐断点", launcher)
        self.assertIn("resume ^<run_id^> --from-node ^<node_id^>", content)
        self.assertIn("--to-node ^<node_id^>", content)

    def test_root_only_contains_supported_visible_entry_files(self) -> None:
        visible = {path.name for path in ROOT.iterdir() if path.is_file() and not path.name.startswith(".")}
        self.assertEqual(visible, {"00_每日数据更新并发布_唯一入口.bat", "AGENTS.md", "README_AI.md"})

    @unittest.skipUnless(shutil.which("git"), "git is required for the runtime code checkout")
    def test_sparse_code_clone_excludes_generated_site(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-b", "code"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "runtime-test@example.invalid"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Runtime Test"], cwd=source, check=True)
            program = source / "节点脚本" / "_共享组件" / "生产程序"
            program.mkdir(parents=True)
            (program / "runtime_workspace.py").write_text("VALUE = 1\n", encoding="utf-8")
            (source / "site" / "basic_data").mkdir(parents=True)
            (source / "site" / "basic_data" / "generated.js").write_text("generated\n", encoding="utf-8")
            (source / "basic_data" / "assets").mkdir(parents=True)
            (source / "basic_data" / "assets" / "basic.css").write_text("body{}\n", encoding="utf-8")
            (source / "basic_data" / "data").mkdir(parents=True)
            (source / "basic_data" / "data" / "generated.js").write_text("generated\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=source, check=True, capture_output=True)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True
            ).stdout.strip()
            target = root / "target"
            clone_sparse_code(target, {"remote": str(source), "branch": "code", "commit": commit})
            self.assertTrue((target / "节点脚本" / "_共享组件" / "生产程序" / "runtime_workspace.py").is_file())
            self.assertTrue((target / "basic_data" / "assets" / "basic.css").is_file())
            self.assertFalse((target / "site").exists())
            self.assertFalse((target / "basic_data" / "data").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
