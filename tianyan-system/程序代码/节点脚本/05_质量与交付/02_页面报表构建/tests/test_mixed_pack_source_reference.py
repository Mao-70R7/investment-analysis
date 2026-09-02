from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "_共享组件"
    / "生产程序"
    / "build_mixed_performance_scatter_pack.py"
)
SPEC = importlib.util.spec_from_file_location("build_mixed_performance_scatter_pack", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MixedPackSourceReferenceTest(unittest.TestCase):
    def test_source_inside_report_root_is_relocatable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_root = Path(temporary) / "staging"
            source = report_root / "reports" / "latest" / "workbook_source.json"
            source.parent.mkdir(parents=True)
            source.write_text("{}", encoding="utf-8")

            self.assertEqual(
                MODULE.portable_source_reference(source, report_root),
                "reports/latest/workbook_source.json",
            )

    def test_source_outside_report_root_remains_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "external" / "workbook_source.json"
            report_root = root / "report"
            source.parent.mkdir(parents=True)
            report_root.mkdir()
            source.write_text("{}", encoding="utf-8")

            self.assertEqual(
                MODULE.portable_source_reference(source, report_root),
                str(source.resolve()),
            )


if __name__ == "__main__":
    unittest.main()
