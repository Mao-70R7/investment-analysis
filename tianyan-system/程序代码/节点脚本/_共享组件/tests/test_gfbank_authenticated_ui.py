from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(CODE_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors.gfbank_authenticated_ui import (  # noqa: E402
    OcrLine,
    build_normalized,
    extract_strategy_cards,
    interval_code_from_path,
    match_details_for_card,
    parse_curve_point_xml,
    parse_detail_xml,
    source_strategy_id,
)


class GfbankAuthenticatedUiTest(unittest.TestCase):
    def test_extract_strategy_cards_pairs_company_and_product(self) -> None:
        rows = [
            OcrLine("南方基金", 0.99, ((200, 550), (380, 550), (380, 610), (200, 610))),
            OcrLine("智投宝180天", 0.99, ((520, 550), (900, 550), (900, 610), (520, 610))),
            OcrLine("持有满180天零赎回费", 0.99, ((500, 700), (900, 700), (900, 750), (500, 750))),
        ]
        cards = extract_strategy_cards(rows, screenshot_name="combo.png", screenshot_snapshot_id="snapshot")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["strategy_name"], "南方智投宝180天")
        self.assertEqual(cards[0]["advisor_name"], "南方基金管理股份有限公司")
        self.assertEqual(cards[0]["suggested_holding_period"], "180天以上")

    def test_parse_detail_xml_treats_chart_return_as_interval_return(self) -> None:
        texts = [
            "投顾策略详情",
            "南方智投宝180天",
            "PR2偏低风险",
            "本策略方案由 南方基金管理股份有限公司 提供",
            "28.96",
            "%",
            "成立以来收益率",
            "1.2896",
            "最新净值(07.27)",
            "6个月以上",
            "建议持有",
            "业绩表现",
            "组合涨跌幅：",
            "+0.07%",
            "基准涨跌幅",
            "-0.07%",
            "2026.06.26",
            "2026.07.27",
            "每个投资者的投顾组合互相独立运行",
            "投顾服务费率：0.4%/年，按日计提。",
            "转入费用：1折 有效期：2025.09.29-2099.12.31",
        ]
        xml = "<?xml version='1.0' encoding='UTF-8'?><hierarchy>" + "".join(
            f'<node text="{text}" content-desc="" />' for text in texts
        ) + "</hierarchy>"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "detail_000_1m.xml"
            path.write_text(xml, encoding="utf-8")
            row = parse_detail_xml(path, captured_at=datetime.fromisoformat("2026-07-29T00:44:08+08:00"))
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["trade_date"], "2026-07-27")
        self.assertAlmostEqual(row["cumulative_return"], 28.96)
        self.assertIsNone(row["daily_return"])
        self.assertIsNone(row["benchmark_return"])
        self.assertEqual(row["interval_code"], "1m")
        self.assertAlmostEqual(row["interval_return"], 0.07)
        self.assertAlmostEqual(row["interval_benchmark_return"], -0.07)
        self.assertEqual(row["interval_start_date"], "2026-06-26")
        self.assertEqual(row["interval_end_date"], "2026-07-27")
        self.assertAlmostEqual(row["nav"], 1.0 + row["cumulative_return"] / 100.0)
        self.assertEqual(row["advisory_fee_rate"], "0.4%/年")

    def test_interval_code_from_capture_filename(self) -> None:
        self.assertEqual(interval_code_from_path(Path("detail_000_1m.xml")), "1m")
        self.assertEqual(interval_code_from_path(Path("detail_000_6m.xml")), "6m")
        self.assertEqual(interval_code_from_path(Path("detail_000_1y.xml")), "1y")
        self.assertEqual(interval_code_from_path(Path("detail_000_since_inception.xml")), "since_inception")
        self.assertIsNone(interval_code_from_path(Path("detail_first.xml")))

    def test_parse_since_inception_curve_tooltip_as_exact_daily_point(self) -> None:
        texts = [
            "投顾策略详情",
            "招商佳薪90天",
            "业绩表现",
            "2026.07.29",
            "组合涨跌幅：",
            "+8.50%",
            "基准涨跌幅",
            "+6.49%",
            "2025.01.02",
            "2026.07.29",
            "每个投资者的投顾组合互相独立运行",
        ]
        xml = "<?xml version='1.0' encoding='UTF-8'?><hierarchy>" + "".join(
            f'<node text="{text}" content-desc="" />' for text in texts
        ) + "</hierarchy>"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "curve_招商佳薪90天_since_inception_20260729.xml"
            path.write_text(xml, encoding="utf-8")
            row = parse_curve_point_xml(path)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["strategy_name"], "招商佳薪90天")
        self.assertEqual(row["trade_date"], "2026-07-29")
        self.assertAlmostEqual(row["cumulative_return"], 8.50)
        self.assertAlmostEqual(row["benchmark_return"], 6.49)
        self.assertAlmostEqual(row["nav"], 1.085)

    def test_curve_parser_rejects_non_since_inception_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "curve_招商佳薪90天_1m_20260729.xml"
            path.write_text("<?xml version='1.0'?><hierarchy />", encoding="utf-8")
            self.assertIsNone(parse_curve_point_xml(path))

    def test_build_uses_detail_text_without_ocr_and_derives_daily_return(self) -> None:
        detail_texts = [
            "投顾策略详情",
            "招商佳薪90天",
            "本策略方案由 招商基金管理有限公司 提供",
            "8.50",
            "%",
            "成立以来收益率",
            "1.0850",
            "最新净值(07.29)",
            "业绩表现",
            "2026.07.29",
            "组合涨跌幅：",
            "+8.50%",
            "基准涨跌幅",
            "+6.49%",
            "每个投资者的投顾组合互相独立运行",
        ]

        def xml_payload(texts: list[str]) -> str:
            return "<?xml version='1.0' encoding='UTF-8'?><hierarchy>" + "".join(
                f'<node text="{text}" content-desc="" />' for text in texts
            ) + "</hierarchy>"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "combo_00.png"
            image.write_bytes(b"not-used-when-ocr-is-unavailable")
            detail = root / "detail_招商佳薪90天_since_inception.xml"
            detail.write_text(xml_payload(detail_texts), encoding="utf-8")
            curve_paths = []
            for date_text, return_text, benchmark_text in (
                ("2026.07.28", "+8.00%", "+6.20%"),
                ("2026.07.29", "+8.50%", "+6.49%"),
            ):
                curve_texts = [
                    "投顾策略详情",
                    "招商佳薪90天",
                    "业绩表现",
                    date_text,
                    "组合涨跌幅：",
                    return_text,
                    "基准涨跌幅",
                    benchmark_text,
                    "每个投资者的投顾组合互相独立运行",
                ]
                curve = root / f"curve_招商佳薪90天_since_inception_{date_text.replace('.', '')}.xml"
                curve.write_text(xml_payload(curve_texts), encoding="utf-8")
                curve_paths.append(curve)
            with patch(
                "advisor_monitor.collectors.gfbank_authenticated_ui.ocr_strategy_cards",
                side_effect=RuntimeError("ocr unavailable"),
            ):
                normalized, diagnostics = build_normalized(
                    image_paths=[image],
                    detail_paths=[detail],
                    curve_point_paths=curve_paths,
                    run_id="test",
                    captured_at=datetime.fromisoformat("2026-07-29T12:00:00+08:00"),
                )
        self.assertEqual(diagnostics["strategy_card_source"], "authenticated_detail_xml_fallback")
        self.assertEqual(diagnostics["curve_point_total"], 2)
        self.assertEqual(len(normalized["strategy_performance_daily"]), 2)
        first, second = normalized["strategy_performance_daily"]
        self.assertIsNone(first["daily_return"])
        self.assertAlmostEqual(second["daily_return"], (1.085 / 1.08 - 1.0) * 100.0)

    def test_match_detail_uses_official_detail_name_when_list_card_is_shortened(self) -> None:
        card = {
            "strategy_name": "广发活期佳30天",
            "product_card_text": "活期佳30天",
            "advisor_name": "广发基金管理有限公司",
        }
        rows = [
            {
                "strategy_name": "广发优配活期佳30天",
                "advisor_name": "广发基金管理有限公司",
            }
        ]
        matched, method = match_details_for_card(card, {"广发优配活期佳30天": rows})
        self.assertEqual(matched, rows)
        self.assertEqual(method, "product_card_text_with_advisor")

    def test_source_strategy_id_is_stable_and_name_scoped(self) -> None:
        first = source_strategy_id("南方智投宝180天")
        self.assertEqual(first, source_strategy_id("南方智投宝180天"))
        self.assertNotEqual(first, source_strategy_id("南方智投宝90天"))

    def test_target_child_detail_hidden_performance_does_not_create_fake_curve(self) -> None:
        detail_texts = [
            "投顾策略详情",
            "南方智投小目标134期",
            "本策略方案由 南方基金管理股份有限公司 提供",
            "目标止盈年化收益率4.50%",
        ]
        special_texts = [
            "南方智投小目标134期",
            "4.50",
            "目标止盈年化收益率",
        ]

        def xml_payload(texts: list[str]) -> str:
            return "<hierarchy>" + "".join(f'<node text="{text}" />' for text in texts) + "</hierarchy>"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detail = root / "detail_南方智投小目标134期_since_inception.xml"
            detail.write_text(xml_payload(detail_texts), encoding="utf-8")
            special = root / "special_目标盈_南方_current.xml"
            special.write_text(xml_payload(special_texts), encoding="utf-8")
            with patch(
                "advisor_monitor.collectors.gfbank_authenticated_ui.ocr_strategy_cards",
                return_value=[],
            ):
                normalized, _diagnostics = build_normalized(
                    image_paths=[],
                    detail_paths=[detail],
                    special_entry_paths=[special],
                    capture_summary={
                        "h5_route_metadata": [
                            {
                                "strategy_name": "南方智投小目标134期",
                                "route_role": "target_profit_child_detail",
                                "app_id": "20181028",
                                "h5_path": "/cgb_smart_invest/new_smart_invest/pro_detail.html",
                                "group_code": "A043010003",
                                "sp_group_code": "SP20260802",
                            }
                        ],
                        "performance_lineages": [
                            {
                                "strategy_name": "南方智投小目标134期",
                                "performance_disclosure_status": "official_child_detail_performance_hidden",
                                "performance_entity_scope": "underlying_parent_group_code",
                                "performance_lineage_evidence": (
                                    "official_h5_mp8768_group_and_sp_group_code_mp8769_group_code_only"
                                ),
                            }
                        ],
                    },
                    run_id="target-lineage-test",
                    captured_at=datetime.fromisoformat("2026-08-02T10:00:00+08:00"),
                )

        row = normalized["strategy_master"][0]
        self.assertEqual(row["extra"]["strategy_entry"], "目标盈")
        self.assertTrue(row["extra"]["detail_observed"])
        self.assertEqual(
            row["extra"]["performance_disclosure_status"],
            "official_child_detail_performance_hidden",
        )
        self.assertEqual(row["extra"]["performance_entity_scope"], "underlying_parent_group_code")
        self.assertEqual(row["extra"]["official_group_code"], "A043010003")
        self.assertEqual(row["extra"]["official_sp_group_code"], "SP20260802")
        self.assertEqual(normalized["strategy_performance_daily"], [])
        self.assertEqual(normalized["strategy_performance_interval"], [])


if __name__ == "__main__":
    unittest.main()
