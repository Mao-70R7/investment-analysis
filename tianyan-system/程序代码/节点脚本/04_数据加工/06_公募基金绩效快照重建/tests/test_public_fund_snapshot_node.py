from __future__ import annotations

import codecs
import gzip
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PRODUCTION_DIR = next(
    parent / "_共享组件" / "生产程序"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "生产程序" / "build_public_fund_performance_snapshot.py").is_file()
)
sys.path.insert(0, str(PRODUCTION_DIR))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PRODUCTION_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SNAPSHOT = load_module(
    "public_fund_snapshot_node_test",
    "build_public_fund_performance_snapshot.py",
)
NODE_DIR = Path(__file__).resolve().parents[1]


class PublicFundSnapshotNodeTests(unittest.TestCase):
    def test_windows_powershell_entry_has_utf8_bom(self) -> None:
        self.assertTrue((NODE_DIR / "run.ps1").read_bytes().startswith(codecs.BOM_UTF8))

    def test_snapshot_write_requires_and_persists_risk_asset_fields(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            SNAPSHOT.write_snapshot(
                conn,
                [
                    {
                        "基金代码": "000001",
                        "基准风险资产权重": "L2",
                        "基准风险资产权重来源": "FOF基准解析",
                    }
                ],
            )
            columns = {
                row[1]
                for row in conn.execute('PRAGMA table_info("公募基金产品绩效快照")')
            }
            self.assertTrue(SNAPSHOT.REQUIRED_SNAPSHOT_COLUMNS.issubset(columns))
            self.assertEqual(
                conn.execute('SELECT COUNT(*) FROM "公募基金产品绩效快照"').fetchone()[0],
                1,
            )
        finally:
            conn.close()

    def test_invalid_rebuild_does_not_drop_existing_snapshot(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute('CREATE TABLE "公募基金产品绩效快照" ("基金代码" TEXT PRIMARY KEY)')
            conn.execute('INSERT INTO "公募基金产品绩效快照" VALUES ("existing")')

            with self.assertRaises(RuntimeError):
                SNAPSHOT.write_snapshot(conn, [{"基金代码": "replacement"}])

            self.assertEqual(
                conn.execute('SELECT "基金代码" FROM "公募基金产品绩效快照"').fetchone()[0],
                "existing",
            )
        finally:
            conn.close()

    def test_artifact_failure_happens_before_database_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "analysis.sqlite"
            with closing(sqlite3.connect(database)) as conn:
                conn.execute(
                    'CREATE TABLE "公募基金产品绩效快照" ("基金代码" TEXT PRIMARY KEY)'
                )
                conn.execute(
                    'INSERT INTO "公募基金产品绩效快照" VALUES ("existing")'
                )
                conn.commit()

            args = SimpleNamespace(
                db_path=database,
                output_root=root / "out",
                site_dir=None,
                end_date="2026-08-11",
                chunk_size=100,
            )
            replacement = {
                "基金代码": "replacement",
                "基准风险资产权重": "L1",
                "基准风险资产权重来源": "测试",
                "本地净值记录数": 0,
            }
            with (
                patch.object(SNAPSHOT, "load_fund_universe", return_value=[{"基金代码": "replacement"}]),
                patch.object(SNAPSHOT, "load_external_fund_audit", return_value={}),
                patch.object(SNAPSHOT, "load_supplemental_metrics", return_value={}),
                patch.object(SNAPSHOT, "load_nav_series", return_value={}),
                patch.object(SNAPSHOT, "load_nav_close_rows", return_value={}),
                patch.object(SNAPSHOT, "build_row", return_value=replacement),
                patch.object(
                    SNAPSHOT,
                    "prepare_snapshot_artifacts",
                    side_effect=OSError("simulated artifact failure"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "simulated artifact failure"):
                    SNAPSHOT.build_snapshot(args)

            with closing(sqlite3.connect(database)) as conn:
                self.assertEqual(
                    conn.execute(
                        'SELECT "基金代码" FROM "公募基金产品绩效快照"'
                    ).fetchone()[0],
                    "existing",
                )

    def test_compact_artifact_names_avoid_nested_long_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir, snapshot_path, summary_path = SNAPSHOT.prepare_snapshot_artifacts(
                Path(temporary),
                [{"基金代码": "000001"}],
                {"fund_count": 1},
            )
            self.assertEqual(snapshot_path.name, "snapshot.json.gz")
            self.assertEqual(summary_path.name, "summary.json")
            self.assertEqual(output_dir.parent, Path(temporary))
            with gzip.open(snapshot_path, "rt", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), [{"基金代码": "000001"}])
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["snapshot_encoding"], "gzip")
            self.assertGreater(summary["snapshot_uncompressed_bytes"], 2)
            self.assertGreater(summary["snapshot_compressed_bytes"], 2)


if __name__ == "__main__":
    unittest.main()
