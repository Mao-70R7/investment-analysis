from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULE_PATH = ROOT / "enumerate_qieman_search_catalog.py"
SPEC = importlib.util.spec_from_file_location("enumerate_qieman_search_catalog", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class EnumerateQiemanSearchCatalogTests(unittest.TestCase):
    def test_parse_search_rows_keeps_snapshot_metrics_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            xml = Path(temp_dir) / "page.xml"
            xml.write_text(
                """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy>
  <node text="更多策略" resource-id="title" bounds="[0,0][100,20]" />
  <node text="示例组合" resource-id="pkg:id/tvName" bounds="[10,30][200,60]" />
  <node text="示例机构V" resource-id="pkg:id/tvTag" bounds="[10,60][200,90]" />
  <node text="已运行3年20天" resource-id="duration" bounds="[200,60][400,90]" />
  <node text="本策略为中风险，适合持有3年以上&amp;#10;历史最大回撤-12.50%，年化收益6.25%" resource-id="summary" bounds="[10,90][500,140]" />
</hierarchy>""",
                encoding="utf-8",
            )
            parsed = MODULE.parse_search_result_xml(xml)
            self.assertTrue(parsed["is_more_strategy_page"])
            self.assertEqual(parsed["visible_signature"], ("示例组合",))
            row = parsed["rows"][0]
            self.assertEqual(row["advisor_name"], "示例机构")
            self.assertEqual(row["risk_level"], "中风险")
            self.assertEqual(row["suggested_holding_period"], "3年以上")
            self.assertAlmostEqual(row["historical_max_drawdown"], -0.125)
            self.assertAlmostEqual(row["historical_annualized_return"], 0.0625)

    def test_merge_candidate_deduplicates_name_and_accumulates_queries(self) -> None:
        rows: dict[str, dict] = {}
        source = {
            "strategy_name": "示例组合",
            "advisor_name": "示例机构",
            "run_duration_text": "已运行3年",
            "risk_level": "中风险",
            "suggested_holding_period": "3年以上",
            "historical_max_drawdown": -0.1,
            "historical_annualized_return": 0.05,
            "visible_summary_text": "摘要",
        }
        MODULE.merge_candidate(rows, source, query="组", page_index=0, captured_at="now", run_id="run")
        MODULE.merge_candidate(rows, source, query="基金", page_index=1, captured_at="now", run_id="run")
        self.assertEqual(len(rows), 1)
        candidate = rows["示例组合"]
        self.assertIsNone(candidate["source_strategy_id"])
        self.assertEqual(candidate["extra"]["search_queries"], ["组", "基金"])
        self.assertTrue(candidate["extra"]["metrics_are_search_snapshot_not_daily_performance"])


if __name__ == "__main__":
    unittest.main()
