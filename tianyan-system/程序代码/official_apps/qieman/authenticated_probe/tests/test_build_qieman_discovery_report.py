from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "build_qieman_discovery_report.py"
SPEC = importlib.util.spec_from_file_location("build_qieman_discovery_report", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildQiemanDiscoveryReportTests(unittest.TestCase):
    def test_build_rows_prefers_public_code_and_deduplicates_name(self) -> None:
        public = [{"strategy_name": "策略一", "source_strategy_id": "ZH000001", "advisor_name": "机构一"}]
        search = [
            {
                "strategy_name": "策略一",
                "advisor_name": "机构一",
                "risk_level": "中风险",
                "extra": {"search_queries": ["组"]},
            },
            {"strategy_name": "策略二", "advisor_name": "机构二", "extra": {"search_queries": ["新"]}},
        ]
        rows = MODULE.build_rows(public, search, {"策略二": "SI000002"})
        self.assertEqual(len(rows), 2)
        by_name = {row["strategy_name"]: row for row in rows}
        self.assertEqual(by_name["策略一"]["source_strategy_id"], "ZH000001")
        self.assertEqual(by_name["策略二"]["source_strategy_id"], "SI000002")
        self.assertEqual(by_name["策略一"]["search_queries"], ["组"])

    def test_valid_strategy_id_excludes_probe_labels(self) -> None:
        self.assertTrue(MODULE.valid_strategy_id("J7"))
        self.assertTrue(MODULE.valid_strategy_id("SI000193"))
        self.assertFalse(MODULE.valid_strategy_id("qieman_list"))


if __name__ == "__main__":
    unittest.main()
