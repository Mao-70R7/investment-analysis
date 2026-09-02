from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_PATH = next(
    parent / "_共享组件" / "生产程序" / "load_gffunds_incremental_analysis.py"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "生产程序" / "load_gffunds_incremental_analysis.py").is_file()
)
SPEC = importlib.util.spec_from_file_location("load_gffunds_incremental_analysis", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IncrementalLoadTransactionTests(unittest.TestCase):
    def test_gffunds_performance_inputs_are_merged_without_dropping_collector_curves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collect = root / "collect.jsonl"
            performance = root / "performance.jsonl"
            merged = MODULE.merge_entity_files([collect], [performance, collect])
            self.assertEqual(merged, sorted([collect, performance], key=lambda path: str(path.resolve())))

    def test_gffunds_import_failure_rolls_back_partial_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "analysis.sqlite"
            normalized = root / "normalized" / "gffunds"
            summary = normalized / "collection_summary" / "2026-07-23" / "collect-run.json"
            master = normalized / "strategy_master" / "2026-07-23" / "collect-run.jsonl"
            summary.parent.mkdir(parents=True)
            master.parent.mkdir(parents=True)
            summary.write_text(
                json.dumps({"channel_id": "gffunds", "run_id": "collect-run"}),
                encoding="utf-8",
            )
            master.write_text("{}\n", encoding="utf-8")
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    'CREATE TABLE "策略信息" ("渠道ID" TEXT, "渠道策略ID" TEXT)'
                )
                connection.execute("CREATE TABLE rollback_probe(value TEXT)")
                connection.commit()

            args = SimpleNamespace(
                db_path=database,
                schema_path=root / "unused.sql",
                normalized_root=normalized,
                collect_run_id="collect-run",
                performance_run_id=None,
                result_path=None,
            )

            def failing_import(connection: sqlite3.Connection, _channels: list[str]):
                connection.execute("INSERT INTO rollback_probe VALUES ('partial')")
                raise RuntimeError("injected import failure")

            with (
                patch.object(MODULE, "parse_args", return_value=args),
                patch.object(
                    MODULE.loader,
                    "init_db",
                    side_effect=lambda *_args, **_kwargs: sqlite3.connect(database),
                ),
                patch.object(MODULE.loader, "import_channels", side_effect=failing_import),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected import failure"):
                    MODULE.main()

            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM rollback_probe").fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
