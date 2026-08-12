from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

from check_runtime_database_health import (  # noqa: E402
    DATE_QUERIES,
    REQUIRED_TABLES,
    check_database,
    resolve_integrity_mode,
)
from recover_ttfund_failed_stage import (  # noqa: E402
    EXIT_NOT_RECOVERABLE,
    EXIT_RETRY_DEVICE,
    EXIT_SOURCE_NOT_READY,
    classify,
)


class DatabasePreflightTests(unittest.TestCase):
    def build_db(self, path: Path) -> None:
        date_column_by_table: dict[str, str] = {}
        for query in DATE_QUERIES.values():
            table = query.split('FROM "', 1)[1].split('"', 1)[0]
            column = query.split('MAX("', 1)[1].split('"', 1)[0]
            date_column_by_table[table] = column
        conn = sqlite3.connect(path)
        try:
            for table in REQUIRED_TABLES:
                column = date_column_by_table.get(table, "测试值")
                conn.execute(f'CREATE TABLE "{table}" ("{column}" TEXT)')
                conn.execute(f'INSERT INTO "{table}" VALUES (?)', ("2026-07-21",))
            conn.commit()
        finally:
            conn.close()

    def test_light_check_skips_full_quick_check_but_checks_schema_and_dates(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.sqlite"
            self.build_db(db_path)

            result = check_database(db_path, integrity_mode="light", quiet=True)

            self.assertEqual(result["quick_check"], "skipped_recent_full_check")
            self.assertFalse(result["errors"])
            self.assertTrue(all(item["has_rows"] for item in result["required_tables"].values()))

    def test_auto_mode_runs_full_after_abnormal_exit_and_light_after_recent_full(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "last_full.json"
            abnormal = root / "abnormal.flag"
            marker.write_text(
                json.dumps({"finished_at": datetime.now().isoformat(timespec="seconds")}),
                encoding="utf-8",
            )

            self.assertEqual(
                resolve_integrity_mode(
                    "auto", marker_path=marker, abnormal_exit_flag=abnormal, interval_days=7
                ),
                ("light", "recent_full_check_available"),
            )
            abnormal.write_text("failed", encoding="utf-8")
            self.assertEqual(
                resolve_integrity_mode(
                    "auto", marker_path=marker, abnormal_exit_flag=abnormal, interval_days=7
                ),
                ("full", "abnormal_exit_flag"),
            )


class TTFundFailureClassificationTests(unittest.TestCase):
    def test_only_device_failures_request_full_collection_retry(self) -> None:
        self.assertEqual(classify({"failure_class": "device_or_login", "failed_stage": "device_cache"})[1], EXIT_RETRY_DEVICE)
        self.assertEqual(
            classify({"failure_class": "source_not_ready", "failed_stage": "03b_official_performance_curve"})[1],
            EXIT_SOURCE_NOT_READY,
        )
        self.assertEqual(classify({"failure_class": "program_error", "failed_stage": "03_collect"})[1], EXIT_NOT_RECOVERABLE)

    def test_official_data_quality_failure_retries_only_official_stage(self) -> None:
        action, exit_code = classify(
            {"failure_class": "data_quality", "failed_stage": "03b_official_performance_curve"}
        )

        self.assertEqual(action, "retry_official_curve_stage")
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
