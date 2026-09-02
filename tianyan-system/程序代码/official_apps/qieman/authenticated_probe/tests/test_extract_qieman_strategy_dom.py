from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "extract_qieman_strategy_dom.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("extract_qieman_strategy_dom", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ExtractQiemanStrategyDomTests(unittest.TestCase):
    def test_sanitize_drops_tokens_uuid_and_trade_confirmation(self) -> None:
        self.assertIsNone(MODULE.sanitize_dom_text("719f91ad-4739-4f67-8fd0-003771380c7a"))
        self.assertIsNone(MODULE.sanitize_dom_text("已确认，继续了解此策略"))
        self.assertEqual(MODULE.sanitize_dom_text("Authorization: Bearer abc"), "Authorization: <redacted>")

    def test_extract_nodes_never_keeps_raw_xml(self) -> None:
        xml = b'<hierarchy><node text="\xe8\xb4\xa7\xe5\xb8\x81\xe4\xb8\x89\xe4\xbd\xb3" content-desc="" class="android.view.View" bounds="[0,0][1,1]" clickable="false" /></hierarchy>'
        rows = MODULE.extract_sanitized_nodes(xml)
        self.assertEqual(rows[0]["text"], "货币三佳")

    def test_parse_complete_holding_sample_and_no_trade_event(self) -> None:
        texts = [
            "货币三佳", "活钱", "投顾策略", "低风险", "本策略由盈米基金提供", "运行9年203天",
            "+28.40%", "累计收益", "+2.65%", "年化收益", "0.00%", "最大回撤",
            "2026-08-06", "业绩基准：中证货币基金指数", "配置", "基金", "南方天天利货币B", "003474",
            "长城收益宝货币C", "016778", "大成恒丰宝货币B", "001698", "配置占比", "34.09%", "33.05%", "32.86%",
            "日涨跌", "+0.00%", "08-06", "+0.00%", "08-06", "+0.00%", "08-06",
            "2026-07-21", "2026年7月例行调仓，通过量化计算备选货基过去四周非交易日的收益率表现，结合规模费率因素，得出现在持仓的货基仍然排在同类前三，本期不需要调仓",
            "100元起购", "建议持有3年以上，可自行转出", "投顾服务费：0折(折后0%/年)",
        ]
        parsed = MODULE.parse_strategy_data(texts, "ZH012636", "货币三佳", datetime(2026, 8, 7, 2, 30, tzinfo=timezone.utc))
        holdings = parsed["strategy_fund_snapshot"]
        self.assertEqual([row["fund_code"] for row in holdings], ["003474", "016778", "001698"])
        self.assertAlmostEqual(sum(row["fund_weight"] for row in holdings), 100.0)
        self.assertTrue(all(row["position_date"] is None for row in holdings))
        self.assertEqual(holdings[0]["fund_nav_date"], "2026-08-06")
        self.assertEqual(parsed["strategy_master"][0]["benchmark"], "中证货币基金指数")
        self.assertEqual(parsed["strategy_master"][0]["suggested_holding_period"], "3年以上")
        self.assertEqual(parsed["strategy_master"][0]["advisory_fee_rate"], "0%/年（0折）")
        self.assertTrue(parsed["strategy_rebalance_event"][0]["extra"]["no_trade"])
        self.assertEqual(parsed["strategy_rebalance_fund_delta"], [])
        self.assertEqual(parsed["strategy_performance_daily"], [])

    def test_fund_list_does_not_misclassify_daily_change_as_weight(self) -> None:
        texts = [
            "全球丰收", "2026-08-06", "基金类型分布", "QDII", "48.22%", "债券型", "37.99%",
            "基金", "华夏海外收益债券A", "001061", "国泰大宗商品A", "160216",
            "日涨跌", "+0.13%", "08-05", "+2.80%", "08-05",
        ]
        parsed = MODULE.parse_strategy_data(texts, "ZH155420", "全球丰收", datetime(2026, 8, 7, tzinfo=timezone.utc))
        holdings = parsed["strategy_fund_snapshot"]
        self.assertEqual([row["fund_code"] for row in holdings], ["001061", "160216"])
        self.assertTrue(all(row["fund_weight"] is None for row in holdings))
        self.assertTrue(all(not row["is_precise_weight"] for row in holdings))
        self.assertEqual(parsed["coverage_assessment"]["entities"]["strategy_fund_snapshot"]["weight_sum"], None)
        self.assertEqual(parsed["strategy_asset_allocation_sample"][0]["asset_weight"], 48.22)

    def test_split_minimum_fee_and_wide_holding_table(self) -> None:
        texts = [
            "周周同行", "持仓日涨跌", "08-06", "基金", "景顺长城策略精选C", "017167", "天弘上证50联接C", "001549",
            "配置占比", "60.00%", "40.00%", "日涨跌", "+0.60%", "08-06", "-0.10%", "08-06",
            "100", "元起购", "投顾服务费：", "0.35%-0.5%/年", "，每月满", "2000", "元封顶",
        ]
        parsed = MODULE.parse_strategy_data(texts, "SI000035", "周周同行", datetime(2026, 8, 7, tzinfo=timezone.utc))
        master = parsed["strategy_master"][0]
        self.assertEqual(master["minimum_amount"], 100.0)
        self.assertEqual(master["advisory_fee_rate"], "0.35%-0.5%/年，每月满2000元封顶")
        self.assertEqual(master["extra"]["performance_summary"]["as_of_date"], "2026-08-06")
        holdings = parsed["strategy_fund_snapshot"]
        self.assertEqual([row["fund_weight"] for row in holdings], [60.0, 40.0])
        self.assertEqual([row["fund_nav_date"] for row in holdings], ["2026-08-06", "2026-08-06"])


if __name__ == "__main__":
    unittest.main()
