from __future__ import annotations

import os
import sqlite3
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

from backup_successful_analysis_db import (
    MAX_SUCCESSFUL_BACKUPS,
    ORPHAN_FINAL_GRACE_SECONDS,
    REQUIRED_TABLES,
    cleanup_incomplete_artifacts,
    cleanup_unmanaged_backup_artifacts,
    create_backup,
    prune_backups,
    successful_backups,
    validate_backup,
)


class SuccessfulBackupRotationTests(unittest.TestCase):
    def test_validation_does_not_create_wal_or_shm_sidecars(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "analysis_zh_current_wal.sqlite"
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                for table in REQUIRED_TABLES:
                    conn.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
                conn.commit()

            validation = validate_backup(database, REQUIRED_TABLES)

            self.assertTrue(validation["valid"])
            self.assertFalse(Path(f"{database}-wal").exists())
            self.assertFalse(Path(f"{database}-shm").exists())

    def test_removes_interrupted_partial_database_and_journal(self) -> None:
        with TemporaryDirectory() as directory:
            backup_dir = Path(directory)
            partial = backup_dir / ".analysis.sqlite.123.partial"
            journal = backup_dir / ".analysis.sqlite.123.partial-journal"
            partial.write_bytes(b"")
            journal.write_bytes(b"journal")

            removed = cleanup_incomplete_artifacts(backup_dir)

            self.assertEqual(set(removed), {str(partial), str(journal)})
            self.assertFalse(partial.exists())
            self.assertFalse(journal.exists())

    def test_removes_only_old_orphan_final_backup(self) -> None:
        with TemporaryDirectory() as directory:
            backup_dir = Path(directory)
            old_orphan = backup_dir / "analysis_zh_current_20260720T000000+0800_old.sqlite"
            recent_orphan = backup_dir / "analysis_zh_current_20260723T000000+0800_recent.sqlite"
            old_orphan.write_bytes(b"old")
            recent_orphan.write_bytes(b"recent")
            old_time = time.time() - ORPHAN_FINAL_GRACE_SECONDS - 60
            os.utime(old_orphan, (old_time, old_time))

            removed = cleanup_incomplete_artifacts(backup_dir)

            self.assertIn(str(old_orphan), removed)
            self.assertFalse(old_orphan.exists())
            self.assertTrue(recent_orphan.exists())

    def create_source_db(self, path: Path) -> None:
        with closing(sqlite3.connect(path)) as conn:
            for table in REQUIRED_TABLES:
                conn.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, value TEXT)')
                conn.execute(f'INSERT INTO "{table}" (value) VALUES (?)', ("ok",))
            conn.commit()

    def create_state_db(self, path: Path) -> None:
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                """
                CREATE TABLE daily_update_node (
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    returncode INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE daily_update_run (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    metadata_json TEXT
                )
                """
            )
            conn.commit()

    def set_run_state(self, path: Path, run_id: str, *, audit_success: bool) -> None:
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("DELETE FROM daily_update_node WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM daily_update_run WHERE run_id=?", (run_id,))
            conn.execute(
                "INSERT INTO daily_update_run VALUES (?, ?, ?)",
                (run_id, "running" if audit_success else "failed_critical", '{"dryRun": false}'),
            )
            conn.execute(
                "INSERT INTO daily_update_node VALUES (?, ?, 'success', 0)",
                (run_id, "process_load"),
            )
            conn.execute(
                "INSERT INTO daily_update_node VALUES (?, ?, ?, ?)",
                (run_id, "data_audit", "success" if audit_success else "failed", 0 if audit_success else 1),
            )
            conn.commit()

    def args(self, source: Path, backup_dir: Path, state_db: Path, run_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            db_path=source,
            backup_dir=backup_dir,
            retain=1,
            run_id=run_id,
            required_table=[],
            state_db=state_db,
            require_stage=["process_load", "data_audit"],
            minimum_free_gib=0,
            result_path=None,
            dry_run=False,
        )

    def test_keeps_only_latest_successful_version_and_rejects_failed_run(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "analysis.sqlite"
            state_db = root / "state.sqlite"
            backup_dir = root / "backups"
            self.create_source_db(source)
            self.create_state_db(state_db)

            for index, run_id in enumerate(("run1", "run2", "run3"), start=1):
                self.set_run_state(state_db, run_id, audit_success=True)
                with closing(sqlite3.connect(source)) as conn:
                    conn.execute(
                        f'INSERT INTO "{REQUIRED_TABLES[0]}" (value) VALUES (?)',
                        (f"version-{index}",),
                    )
                    conn.commit()
                result = create_backup(self.args(source, backup_dir, state_db, run_id))
                self.assertEqual(result["status"], "success")

            backups_before_failure = successful_backups(backup_dir)
            self.assertEqual([item[2]["run_id"] for item in backups_before_failure], ["run3"])

            self.set_run_state(state_db, "run4", audit_success=False)
            with self.assertRaisesRegex(RuntimeError, "must not create a new backup"):
                create_backup(self.args(source, backup_dir, state_db, "run4"))

            backups_after_failure = successful_backups(backup_dir)
            self.assertEqual([item[2]["run_id"] for item in backups_after_failure], ["run3"])

    def test_hard_caps_requested_retention_at_one(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "analysis.sqlite"
            state_db = root / "state.sqlite"
            backup_dir = root / "backups"
            self.create_source_db(source)
            self.create_state_db(state_db)

            args = self.args(source, backup_dir, state_db, "run1")
            args.retain = 99
            self.set_run_state(state_db, "run1", audit_success=True)
            result = create_backup(args)

            self.assertEqual(result["retain"], MAX_SUCCESSFUL_BACKUPS)
            self.assertEqual(len(successful_backups(backup_dir)), MAX_SUCCESSFUL_BACKUPS)

    def test_prune_removes_unmanaged_snapshots_and_sidecars(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "analysis.sqlite"
            state_db = root / "state.sqlite"
            backup_dir = root / "backups"
            self.create_source_db(source)
            self.create_state_db(state_db)
            self.set_run_state(state_db, "run1", audit_success=True)
            created = create_backup(self.args(source, backup_dir, state_db, "run1"))

            unmanaged = backup_dir / "analysis_zh_current_before_performance_governance.sqlite"
            stale_wal = backup_dir / "analysis_zh_current_old.sqlite-wal"
            stale_shm = backup_dir / "analysis_zh_current_old.sqlite-shm"
            unmanaged.write_bytes(b"unmanaged")
            stale_wal.write_bytes(b"")
            stale_shm.write_bytes(b"")

            removed = cleanup_unmanaged_backup_artifacts(
                backup_dir, Path(created["backup_path"])
            )
            self.assertEqual(set(removed), {str(unmanaged), str(stale_wal), str(stale_shm)})
            self.assertTrue(Path(created["backup_path"]).is_file())

            prune_args = self.args(source, backup_dir, state_db, "unused")
            prune_args.dry_run = True
            dry_run = prune_backups(prune_args)
            self.assertEqual(dry_run["status"], "dry_run")
            self.assertTrue(Path(created["backup_path"]).is_file())

            prune_args.dry_run = False
            pruned = prune_backups(prune_args)
            self.assertEqual(pruned["status"], "success")
            self.assertEqual(pruned["remaining_successful_versions"], 1)

    def test_reuses_existing_success_for_same_run(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "analysis.sqlite"
            state_db = root / "state.sqlite"
            backup_dir = root / "backups"
            self.create_source_db(source)
            self.create_state_db(state_db)
            self.set_run_state(state_db, "run1", audit_success=True)

            first = create_backup(self.args(source, backup_dir, state_db, "run1"))
            second = create_backup(self.args(source, backup_dir, state_db, "run1"))

            self.assertEqual(first["backup_path"], second["backup_path"])
            self.assertTrue(second["reused_unchanged_source"])
            self.assertEqual(second["requested_run_id"], "run1")
            self.assertEqual(len(successful_backups(backup_dir)), 1)

    def test_same_run_creates_new_backup_when_database_changed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "analysis.sqlite"
            state_db = root / "state.sqlite"
            backup_dir = root / "backups"
            self.create_source_db(source)
            self.create_state_db(state_db)
            self.set_run_state(state_db, "run1", audit_success=True)

            first = create_backup(self.args(source, backup_dir, state_db, "run1"))
            with closing(sqlite3.connect(source)) as conn:
                conn.execute(
                    f'INSERT INTO "{REQUIRED_TABLES[0]}" (value) VALUES (?)',
                    ("same-run-new-version",),
                )
                conn.commit()
            second = create_backup(self.args(source, backup_dir, state_db, "run1"))

            self.assertNotEqual(first["backup_path"], second["backup_path"])
            self.assertFalse(Path(first["backup_path"]).exists())
            self.assertTrue(Path(second["backup_path"]).exists())
            self.assertEqual(
                second["source_mtime_ns"],
                source.stat().st_mtime_ns,
            )
            self.assertEqual(len(successful_backups(backup_dir)), 1)

    def test_reuses_latest_valid_backup_when_database_is_unchanged_across_runs(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "analysis.sqlite"
            state_db = root / "state.sqlite"
            backup_dir = root / "backups"
            self.create_source_db(source)
            self.create_state_db(state_db)
            self.set_run_state(state_db, "run1", audit_success=True)
            first = create_backup(self.args(source, backup_dir, state_db, "run1"))
            self.set_run_state(state_db, "run2", audit_success=True)
            second = create_backup(self.args(source, backup_dir, state_db, "run2"))

            self.assertEqual(first["backup_path"], second["backup_path"])
            self.assertTrue(second["reused_unchanged_source"])
            self.assertEqual(second["requested_run_id"], "run2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
