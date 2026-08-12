from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "_共享组件"
    / "生产程序"
    / "build_basic_data_report_packs.py"
)
SPEC = importlib.util.spec_from_file_location("build_basic_data_report_packs", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FofSourceBootstrapTests(unittest.TestCase):
    @staticmethod
    def write_summary(path: Path, *, strategy_total: int, data_date: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"overview": {"策略总数": strategy_total, "数据更新至": data_date}, "strategies": []}
        path.write_text(
            "window.__BASIC_DATA__.summary = " + json.dumps(payload, ensure_ascii=False) + ";\n",
            encoding="utf-8",
        )

    @staticmethod
    def write_fof_source(path: Path, *, strategy_total: int, nav_date: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"meta": {"策略总数": strategy_total, "实际FOF净值最新日": nav_date}}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_existing_source_is_reused_without_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_root = root / "fof_benchmark_ranking"
            source = output_root / "existing" / "fof_benchmark_classified_ranking_data.json"
            source.parent.mkdir(parents=True)
            source.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(MODULE, "FOF_BENCHMARK_OUTPUT_ROOT", output_root),
                mock.patch.object(MODULE.subprocess, "run") as run,
            ):
                self.assertEqual(MODULE.ensure_fof_benchmark_source(root / "report"), source)
                run.assert_not_called()

    def test_missing_source_is_bootstrapped_once_without_database_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_root = root / "report"
            summary = report_root / "basic_data" / "data" / "basic_summary.js"
            summary.parent.mkdir(parents=True)
            summary.write_text("window.__BASIC_DATA__={};", encoding="utf-8")
            h1_script = root / "generate.py"
            enrich_script = root / "enrich.py"
            benchmark_input = root / "benchmark.json"
            for path in (h1_script, enrich_script, benchmark_input):
                path.write_text("{}", encoding="utf-8")
            h1_output = root / "fof_h1"
            benchmark_output = root / "fof_benchmark"
            calls: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                calls.append(command)
                if len(calls) == 1:
                    h1_output.mkdir(parents=True)
                    (h1_output / "latest_fof_h1_strategy_ranking_data.json").write_text("{}", encoding="utf-8")
                else:
                    dated = benchmark_output / "run"
                    dated.mkdir(parents=True)
                    (dated / "fof_benchmark_classified_ranking_data.json").write_text("{}", encoding="utf-8")
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(MODULE, "FOF_H1_SOURCE_SCRIPT_PATH", h1_script),
                mock.patch.object(MODULE, "FOF_BENCHMARK_ENRICH_SCRIPT_PATH", enrich_script),
                mock.patch.object(MODULE, "FOF_BENCHMARK_DATA_PATH", benchmark_input),
                mock.patch.object(MODULE, "FOF_H1_OUTPUT_ROOT", h1_output),
                mock.patch.object(MODULE, "FOF_BENCHMARK_OUTPUT_ROOT", benchmark_output),
                mock.patch.object(MODULE.subprocess, "run", side_effect=fake_run),
            ):
                generated = MODULE.ensure_fof_benchmark_source(report_root)

            self.assertTrue(generated.is_file())
            self.assertEqual(len(calls), 2)
            self.assertIn("--skip-db", calls[1])

    def test_stale_source_is_rebuilt_when_summary_is_newer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_root = root / "report"
            summary = report_root / "basic_data" / "data" / "basic_summary.js"
            summary.parent.mkdir(parents=True)
            summary.write_text("window.__BASIC_DATA__={};", encoding="utf-8")
            h1_script = root / "generate.py"
            enrich_script = root / "enrich.py"
            benchmark_input = root / "benchmark.json"
            for path in (h1_script, enrich_script, benchmark_input):
                path.write_text("{}", encoding="utf-8")
            h1_output = root / "fof_h1"
            benchmark_output = root / "fof_benchmark"
            old_source = benchmark_output / "old" / "fof_benchmark_classified_ranking_data.json"
            old_source.parent.mkdir(parents=True)
            old_source.write_text("{}", encoding="utf-8")
            os.utime(old_source, ns=(1, 1))
            calls: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                calls.append(command)
                if len(calls) == 1:
                    h1_output.mkdir(parents=True)
                    (h1_output / "latest_fof_h1_strategy_ranking_data.json").write_text("{}", encoding="utf-8")
                else:
                    dated = benchmark_output / "new"
                    dated.mkdir(parents=True)
                    generated = dated / "fof_benchmark_classified_ranking_data.json"
                    generated.write_text("{}", encoding="utf-8")
                    os.utime(generated, None)
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(MODULE, "DB_PATH", root / "missing.sqlite"),
                mock.patch.object(MODULE, "FOF_H1_SOURCE_SCRIPT_PATH", h1_script),
                mock.patch.object(MODULE, "FOF_BENCHMARK_ENRICH_SCRIPT_PATH", enrich_script),
                mock.patch.object(MODULE, "FOF_BENCHMARK_DATA_PATH", benchmark_input),
                mock.patch.object(MODULE, "FOF_H1_OUTPUT_ROOT", h1_output),
                mock.patch.object(MODULE, "FOF_BENCHMARK_OUTPUT_ROOT", benchmark_output),
                mock.patch.object(MODULE.subprocess, "run", side_effect=fake_run),
            ):
                generated = MODULE.ensure_fof_benchmark_source(report_root)

            self.assertEqual(generated.parent.name, "new")
            self.assertEqual(len(calls), 2)
            self.assertIn("--skip-db", calls[1])

    def test_newer_generated_summary_does_not_invalidate_aligned_business_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_root = root / "report"
            summary = report_root / "basic_data" / "data" / "basic_summary.js"
            self.write_summary(summary, strategy_total=1954, data_date="2026-08-10")
            output_root = root / "fof_benchmark"
            source = output_root / "latest_fof_benchmark_classified_ranking_data.json"
            self.write_fof_source(source, strategy_total=1954, nav_date="2026-08-10")
            os.utime(source, ns=(1, 1))

            with (
                mock.patch.object(MODULE, "FOF_BENCHMARK_OUTPUT_ROOT", output_root),
                mock.patch.object(MODULE, "FOF_H1_OUTPUT_ROOT", root / "missing_h1"),
                mock.patch.object(MODULE, "FOF_BENCHMARK_DATA_PATH", root / "missing_benchmark.json"),
                mock.patch.object(MODULE.subprocess, "run") as run,
            ):
                selected = MODULE.ensure_fof_benchmark_source(report_root)

            self.assertEqual(selected, source)
            run.assert_not_called()

    def test_strategy_count_mismatch_forces_refresh_even_when_files_are_older(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = root / "report" / "basic_data" / "data" / "basic_summary.js"
            source = root / "fof.json"
            self.write_summary(summary, strategy_total=1955, data_date="2026-08-10")
            self.write_fof_source(source, strategy_total=1954, nav_date="2026-08-10")
            self.assertFalse(MODULE.fof_benchmark_source_is_fresh(source, [], summary))


if __name__ == "__main__":
    unittest.main()
