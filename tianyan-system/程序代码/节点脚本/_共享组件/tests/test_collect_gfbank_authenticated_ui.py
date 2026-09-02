from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
os.environ.setdefault("ADVISOR_CODE_ROOT", str(CODE_ROOT))
MODULE_PATH = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "collect_gfbank_authenticated_ui.py"
SPEC = importlib.util.spec_from_file_location("collect_gfbank_authenticated_ui", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)

from advisor_monitor.collectors import gfbank_authenticated_ui as normalized_collector  # noqa: E402


def master(strategy_id: str, name: str, captured_at: str) -> dict[str, object]:
    return {
        "channel_id": "gfbank_cgb",
        "source_strategy_id": strategy_id,
        "strategy_name": name,
        "first_seen_at": captured_at,
        "last_seen_at": captured_at,
        "extra": {"detail_observed": True},
    }


def daily(strategy_id: str, trade_date: str, cumulative_return: float) -> dict[str, object]:
    return {
        "channel_id": "gfbank_cgb",
        "source_strategy_id": strategy_id,
        "trade_date": trade_date,
        "nav": 1.0 + cumulative_return / 100.0,
        "daily_return": None,
        "cumulative_return": cumulative_return,
        "benchmark_return": cumulative_return / 2.0,
        "section_type": "gfbank_authenticated_ui_curve_tooltip",
        "run_id": "test",
    }


class CollectGfbankAuthenticatedUiTest(unittest.TestCase):
    def test_target_profit_and_super_invest_use_explicit_bank_entry_lineage(self) -> None:
        def write_detail(path: Path, strategy_name: str) -> None:
            path.write_text(
                "<hierarchy>"
                '<node text="投顾策略详情" />'
                f'<node text="{strategy_name}" />'
                '<node text="PR2中低风险" />'
                '<node text="本策略方案由 广发基金管理有限公司 提供" />'
                '<node text="1.23" />'
                '<node text="成立以来收益率" />'
                '<node text="1.0123" />'
                '<node text="最新净值(07.30)" />'
                '<node text="业绩表现" />'
                '<node text="2025.01.01" />'
                '<node text="2026.07.30" />'
                '<node text="组合涨跌幅：+1.23%" />'
                '<node text="基准涨跌幅：+0.80%" />'
                '<node text="业绩基准" />'
                '<node text="80%中债综合财富指数+20%沪深300指数" />'
                "</hierarchy>",
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir)
            image_path = source_dir / "combo_目标盈_test_00.png"
            image_path.write_bytes(b"evidence-placeholder")
            target_path = source_dir / "detail_target_1m.xml"
            super_path = source_dir / "detail_super_1m.xml"
            write_detail(target_path, "幸福小心愿目标盈01期")
            write_detail(super_path, "超级定投家")
            with patch.object(normalized_collector, "ocr_strategy_cards", return_value=[]):
                normalized, diagnostics = normalized_collector.build_normalized(
                    image_paths=[image_path],
                    detail_paths=[target_path, super_path],
                    run_id="entry-lineage-test",
                    captured_at=datetime.fromisoformat("2026-08-02T10:00:00+08:00"),
                )
        self.assertEqual(diagnostics["strategy_total"], 2)
        rows = {row["strategy_name"]: row for row in normalized["strategy_master"]}
        self.assertEqual(rows["幸福小心愿目标盈01期"]["extra"]["strategy_entry"], "目标盈")
        self.assertEqual(rows["超级定投家"]["extra"]["strategy_entry"], "超级定投")
        self.assertIn("目标盈", rows["幸福小心愿目标盈01期"]["tags"])
        self.assertIn("超级定投", rows["超级定投家"]["tags"])
        self.assertEqual(
            rows["幸福小心愿目标盈01期"]["benchmark"],
            "80%中债综合财富指数+20%沪深300指数",
        )
        self.assertEqual(
            {row["title"] for row in normalized["app_public_entry"]},
            {"广发智投目标盈", "广发智投超级定投"},
        )

    def test_separate_benchmark_popup_is_linked_by_detail_evidence_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir)
            detail_path = source_dir / "detail_招商佳薪90天_4d928abd_1m.xml"
            detail_path.write_text(
                '<hierarchy><node text="投顾策略详情"/><node text="招商佳薪90天"/>'
                '<node text="本策略方案由 招商基金管理有限公司 提供"/></hierarchy>',
                encoding="utf-8",
            )
            popup_path = source_dir / "detail_招商佳薪90天_4d928abd_benchmark.xml"
            popup_path.write_text(
                '<hierarchy><node text="业绩基准"/><node text="沪深300指数*3% +中债-综合全价指数*97%"/></hierarchy>',
                encoding="utf-8",
            )
            with patch.object(normalized_collector, "ocr_strategy_cards", return_value=[]):
                normalized, _diagnostics = normalized_collector.build_normalized(
                    image_paths=[],
                    detail_paths=[detail_path, popup_path],
                    run_id="benchmark-popup-test",
                    captured_at=datetime.fromisoformat("2026-08-02T10:00:00+08:00"),
                )
        self.assertEqual(
            normalized["strategy_master"][0]["benchmark"],
            "沪深300指数*3% +中债-综合全价指数*97%",
        )

    def test_latest_daily_point_uses_same_date_since_inception_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir)
            common = (
                '<node text="投顾策略详情"/><node text="招商佳薪90天"/>'
                '<node text="8.52"/><node text="成立以来收益率"/>'
                '<node text="1.0852"/><node text="最新净值(07.30)"/>'
                '<node text="业绩表现"/><node text="2022.08.09"/><node text="2026.07.30"/>'
            )
            one_month = source_dir / "detail_招商佳薪90天_4d928abd_1m.xml"
            one_month.write_text(
                f'<hierarchy>{common}<node text="组合涨跌幅："/><node text="+0.29%"/></hierarchy>',
                encoding="utf-8",
            )
            since = source_dir / "detail_招商佳薪90天_4d928abd_since_inception.xml"
            since.write_text(
                f'<hierarchy>{common}<node text="组合涨跌幅："/><node text="+8.52%"/>'
                '<node text="基准涨跌幅"/><node text=":"/><node text="+6.51%"/></hierarchy>',
                encoding="utf-8",
            )
            with patch.object(normalized_collector, "ocr_strategy_cards", return_value=[]):
                normalized, _diagnostics = normalized_collector.build_normalized(
                    image_paths=[],
                    detail_paths=[one_month, since],
                    run_id="same-date-benchmark-test",
                    captured_at=datetime.fromisoformat("2026-08-02T10:00:00+08:00"),
                )
        self.assertEqual(normalized["strategy_performance_daily"][0]["trade_date"], "2026-07-30")
        self.assertEqual(normalized["strategy_performance_daily"][0]["benchmark_return"], 6.51)

    def test_partial_capture_summary_is_visible_but_not_a_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir)
            (source_dir / "capture_summary.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "run_id": "batch-test",
                        "requested_strategy_names": ["策略A", "策略B"],
                        "captured_strategy_names": ["策略A"],
                        "missing_requested_strategy_names": ["策略B"],
                        "failure_total": 1,
                        "failures": [{"strategy_name": "策略B", "error": "temporary UI failure"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            diagnostics = collector.read_capture_batch_diagnostics(source_dir)
            self.assertIsNotNone(diagnostics)
            assert diagnostics is not None
            self.assertTrue(diagnostics["partial_failure"])
            self.assertEqual(diagnostics["captured_strategy_total"], 1)
            self.assertEqual(diagnostics["missing_requested_strategy_names"], ["策略B"])

    def test_partial_capture_merges_without_shrinking_other_strategy_or_history(self) -> None:
        existing = {
            "strategy_master": [
                master("a", "策略A", "2026-07-29T10:00:00+08:00"),
                master("b", "策略B", "2026-07-29T10:00:00+08:00"),
            ],
            "strategy_performance_daily": [daily("a", "2026-07-28", 1.0), daily("a", "2026-07-29", 1.2)],
            "strategy_performance_interval": [],
            "app_public_entry": [],
        }
        incoming = {
            "strategy_master": [master("a", "策略A", "2026-07-30T10:00:00+08:00")],
            "strategy_performance_daily": [daily("a", "2026-07-29", 1.2), daily("a", "2026-07-30", 1.5)],
            "strategy_performance_interval": [],
            "app_public_entry": [],
        }
        merged, diagnostics = collector.merge_authenticated_normalized(existing, incoming)
        self.assertEqual(len(merged["strategy_master"]), 2)
        self.assertEqual(len(merged["strategy_performance_daily"]), 3)
        self.assertEqual(diagnostics["conflict_total"], 0)
        self.assertEqual(diagnostics["history_regression_total"], 0)
        strategy_a = next(row for row in merged["strategy_master"] if row["source_strategy_id"] == "a")
        self.assertEqual(strategy_a["first_seen_at"], "2026-07-29T10:00:00+08:00")
        self.assertEqual(strategy_a["last_seen_at"], "2026-07-30T10:00:00+08:00")
        latest = next(row for row in merged["strategy_performance_daily"] if row["trade_date"] == "2026-07-30")
        self.assertIsNotNone(latest["daily_return"])

    def test_same_strategy_date_value_conflict_blocks_promotion(self) -> None:
        existing = {
            "strategy_master": [],
            "strategy_performance_daily": [daily("a", "2026-07-29", 1.2)],
            "strategy_performance_interval": [],
            "app_public_entry": [],
        }
        incoming = {
            "strategy_master": [],
            "strategy_performance_daily": [daily("a", "2026-07-29", 11.2)],
            "strategy_performance_interval": [],
            "app_public_entry": [],
        }
        _merged, diagnostics = collector.merge_authenticated_normalized(existing, incoming)
        self.assertEqual(diagnostics["conflict_total"], 1)
        self.assertIn("cumulative_return", diagnostics["conflicts"][0]["fields"])

    def test_card_only_partial_capture_does_not_downgrade_validated_detail_master(self) -> None:
        validated = master("a", "策略A", "2026-07-29T10:00:00+08:00")
        validated["risk_level"] = "PR2"
        validated["extra"] = {
            "detail_observed": True,
            "source_evidence_file": "detail_strategy_a.xml",
        }
        card_only = master("a", "策略A", "2026-07-30T10:00:00+08:00")
        card_only["risk_level"] = None
        card_only["run_id"] = "new-run"
        card_only["extra"] = {
            "detail_observed": False,
            "source_evidence_file": "combo_00.png",
        }
        existing = {
            "strategy_master": [validated],
            "strategy_performance_daily": [],
            "strategy_performance_interval": [],
            "app_public_entry": [],
        }
        incoming = {
            "strategy_master": [card_only],
            "strategy_performance_daily": [],
            "strategy_performance_interval": [],
            "app_public_entry": [],
        }
        merged, diagnostics = collector.merge_authenticated_normalized(existing, incoming)
        row = merged["strategy_master"][0]
        self.assertEqual(row["risk_level"], "PR2")
        self.assertTrue(row["extra"]["detail_observed"])
        self.assertEqual(row["extra"]["source_evidence_file"], "detail_strategy_a.xml")
        self.assertEqual(row["last_seen_at"], "2026-07-30T10:00:00+08:00")
        self.assertEqual(row["run_id"], "new-run")
        self.assertEqual(diagnostics["history_regression_total"], 0)

    def test_shortened_card_alias_is_consolidated_into_official_detail_strategy(self) -> None:
        canonical = master("canonical", "广发优配活期佳30天", "2026-07-29T10:00:00+08:00")
        canonical["extra"] = {
            "detail_observed": True,
            "list_strategy_name": "广发活期佳30天",
        }
        alias = master("alias", "广发活期佳30天", "2026-07-30T10:00:00+08:00")
        alias["extra"] = {
            "detail_observed": False,
            "list_strategy_name": "广发活期佳30天",
        }
        existing = {
            "strategy_master": [canonical, alias],
            "strategy_performance_daily": [],
            "strategy_performance_interval": [],
            "app_public_entry": [],
        }
        incoming = {
            "strategy_master": [],
            "strategy_performance_daily": [],
            "strategy_performance_interval": [],
            "app_public_entry": [],
        }
        merged, diagnostics = collector.merge_authenticated_normalized(existing, incoming)
        self.assertEqual(len(merged["strategy_master"]), 1)
        self.assertEqual(merged["strategy_master"][0]["source_strategy_id"], "canonical")
        self.assertEqual(merged["strategy_master"][0]["strategy_name"], "广发优配活期佳30天")
        self.assertEqual(diagnostics["strategy_alias_remap_total"], 1)

    def test_older_interval_capture_does_not_replace_newer_cache(self) -> None:
        existing_row = {
            "source_strategy_id": "a",
            "interval_code": "1m",
            "as_of_date": "2026-07-30",
            "return_value": 1.5,
        }
        incoming_row = {
            "source_strategy_id": "a",
            "interval_code": "1m",
            "as_of_date": "2026-07-29",
            "return_value": 1.4,
        }
        existing = {
            "strategy_master": [],
            "strategy_performance_daily": [],
            "strategy_performance_interval": [existing_row],
            "app_public_entry": [],
        }
        incoming = {
            "strategy_master": [],
            "strategy_performance_daily": [],
            "strategy_performance_interval": [incoming_row],
            "app_public_entry": [],
        }
        merged, diagnostics = collector.merge_authenticated_normalized(existing, incoming)
        self.assertEqual(merged["strategy_performance_interval"][0]["as_of_date"], "2026-07-30")
        self.assertEqual(diagnostics["stale_interval_rows_ignored"], 1)

    def test_same_as_of_date_with_new_rolling_window_replaces_without_conflict(self) -> None:
        existing_row = {
            "source_strategy_id": "a",
            "interval_code": "1m",
            "as_of_date": "2026-07-30",
            "interval_start_date": "2026-07-02",
            "return_value": 0.35,
            "benchmark_return": 0.27,
        }
        incoming_row = {
            "source_strategy_id": "a",
            "interval_code": "1m",
            "as_of_date": "2026-07-30",
            "interval_start_date": "2026-07-03",
            "return_value": 0.29,
            "benchmark_return": 0.26,
        }
        existing = {
            "strategy_master": [],
            "strategy_performance_daily": [],
            "strategy_performance_interval": [existing_row],
            "app_public_entry": [],
        }
        incoming = {
            "strategy_master": [],
            "strategy_performance_daily": [],
            "strategy_performance_interval": [incoming_row],
            "app_public_entry": [],
        }
        merged, diagnostics = collector.merge_authenticated_normalized(existing, incoming)
        self.assertEqual(merged["strategy_performance_interval"][0]["interval_start_date"], "2026-07-03")
        self.assertEqual(merged["strategy_performance_interval"][0]["return_value"], 0.29)
        self.assertEqual(diagnostics["interval_window_updates"], 1)
        self.assertEqual(diagnostics["conflict_total"], 0)

    def test_special_entries_create_master_rows_without_invented_performance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            super_path = root / "special_超级定投_广发_current.xml"
            super_path.write_text(
                '<hierarchy><node text="广发基金超级定投"/><node text="发车日期："/>'
                '<node text="2026.07.30"/><node text="本期投入建议"/><node text="减少投入金额"/></hierarchy>',
                encoding="utf-8",
            )
            target_path = root / "special_目标盈_广发_current.xml"
            target_path.write_text(
                '<hierarchy><node text="幸福小列车目标盈95期"/><node text="广发基金管理"/>'
                '<node text="4.50"/><node text="目标止盈年化收益率"/><node text="中低风险"/></hierarchy>',
                encoding="utf-8",
            )
            rows, diagnostics = normalized_collector.parse_special_entry_xmls(
                [super_path, target_path],
                run_id="test-run",
                captured_at=datetime.fromisoformat("2026-08-02T12:00:00+08:00"),
            )
        self.assertEqual({row["strategy_name"] for row in rows}, {"广发基金超级定投", "幸福小列车目标盈95期"})
        self.assertTrue(all(row["benchmark"] is None for row in rows))
        self.assertTrue(all(row["extra"]["performance_disclosure_status"] == "not_disclosed_on_authenticated_entry_page" for row in rows))
        self.assertEqual(diagnostics["special_entry_strategy_total"], 2)


if __name__ == "__main__":
    unittest.main()
