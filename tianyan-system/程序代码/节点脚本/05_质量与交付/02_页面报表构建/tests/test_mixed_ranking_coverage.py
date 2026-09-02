from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[3] / "_共享组件" / "生产程序"
SCRIPT_PATH = SCRIPT_DIR / "export_advisor_public_fund_mixed_performance_source.py"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("advisor_public_fund_mixed_source", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

AUDIT_PATH = SCRIPT_DIR / "标准化数据稽核.py"
AUDIT_SPEC = importlib.util.spec_from_file_location("standardized_data_audit", AUDIT_PATH)
assert AUDIT_SPEC and AUDIT_SPEC.loader
AUDIT_MODULE = importlib.util.module_from_spec(AUDIT_SPEC)
AUDIT_SPEC.loader.exec_module(AUDIT_MODULE)


class MixedRankingCoverageTests(unittest.TestCase):
    def test_audit_visibility_does_not_drop_incomplete_rankable_strategy(self) -> None:
        strategy = {
            "统一策略ID": "gfsec_fima__regular:gffund:GFAXHB",
            "数据完整性": "不完整",
            "风险等级": "R3 均衡稳健",
            "研报产品类型": "股债混合型",
            "基准风险资产权重": "L4",
            "是否纳入常规排名": 1,
        }

        self.assertEqual(
            AUDIT_MODULE.mixed_ranking_visible_strategy_rows([strategy]),
            [strategy],
        )

    def test_audit_guangfa_scope_includes_gfsec_shelf_with_third_party_advisor(self) -> None:
        self.assertTrue(
            AUDIT_MODULE.mixed_ranking_is_guangfa_strategy(
                {
                    "投顾机构": "南方基金管理股份有限公司",
                    "渠道": "广发证券易淘金/财富管家",
                }
            )
        )
        self.assertFalse(
            AUDIT_MODULE.mixed_ranking_is_guangfa_strategy(
                {
                    "投顾机构": "南方基金管理股份有限公司",
                    "渠道": "天天基金/投顾",
                }
            )
        )

    def test_bucketed_strategy_with_incomplete_holdings_remains_visible_to_ranking_gate(self) -> None:
        strategy_id = "gfsec_fima__regular:gffund:GFAXHB"

        visible = MODULE.strategy_list_visible_ids(
            {
                "strategies": [
                    {
                        "统一策略ID": strategy_id,
                        "数据完整性": "不完整",
                        "风险等级": "R3 均衡稳健",
                        "研报产品类型": "股债混合型",
                        "基准风险资产权重": "L4",
                    }
                ]
            }
        )

        self.assertEqual(visible, {strategy_id})

    def test_visible_strategy_without_official_performance_is_recorded_not_fatal(self) -> None:
        strategy_id = "gffunds__ZY00000014"
        rows, unexpected = MODULE.partition_missing_visible_strategies(
            [strategy_id],
            {
                strategy_id: {
                    "统一策略ID": strategy_id,
                    "策略名称": "幸福小列车第0期",
                    "投顾机构": "广发基金投顾",
                    "渠道": "广发基金投顾",
                }
            },
            {},
            {},
            set(),
            date(2026, 7, 16),
        )

        self.assertEqual(unexpected, [])
        self.assertEqual(len(rows), 1)
        self.assertIn("无官方披露业绩", rows[0]["剔除原因"])

    def test_current_rankable_strategy_missing_from_source_remains_fatal(self) -> None:
        strategy_id = "gffunds__GFJJ000001"
        rows, unexpected = MODULE.partition_missing_visible_strategies(
            [strategy_id],
            {
                strategy_id: {
                    "统一策略ID": strategy_id,
                    "策略名称": "广发货币加",
                    "投顾机构": "广发基金投顾",
                    "渠道": "广发基金投顾",
                }
            },
            {strategy_id: {"是否纳入常规排名": 1, "治理状态": "正常运行"}},
            {strategy_id: "2026-07-15"},
            set(),
            date(2026, 7, 16),
        )

        self.assertEqual(rows, [])
        self.assertEqual(unexpected, [strategy_id])

    def test_governance_excluded_strategy_is_recorded_not_fatal(self) -> None:
        strategy_id = "ttfund__STOPPED"
        rows, unexpected = MODULE.partition_missing_visible_strategies(
            [strategy_id],
            {
                strategy_id: {
                    "统一策略ID": strategy_id,
                    "策略名称": "已停止策略",
                    "投顾机构": "示例机构",
                    "渠道": "天天基金/投顾",
                }
            },
            {strategy_id: {"是否纳入常规排名": 0, "治理状态": "已停止策略"}},
            {strategy_id: "2026-07-15"},
            set(),
            date(2026, 7, 16),
        )

        self.assertEqual(unexpected, [])
        self.assertIn("已停止策略", rows[0]["剔除原因"])

    def test_signal_strategy_with_nonrankable_flag_is_excluded(self) -> None:
        strategy_id = "ttfund__SIGNAL"
        row = {
            "id": strategy_id,
            "code": strategy_id,
            "entityType": "投顾策略",
            "name": "信号策略",
            "channel": "天天基金/投顾",
        }

        reason = MODULE.strategy_exclusion_reason(
            row,
            {strategy_id: {"是否纳入常规排名": 0, "治理状态": "信号类策略"}},
            {strategy_id: "2026-07-15"},
            set(),
            date(2026, 7, 16),
        )

        self.assertEqual(reason, "信号类策略")
        self.assertFalse(
            MODULE.should_export_strategy(
                row,
                {strategy_id: {"是否纳入常规排名": 0, "治理状态": "信号类策略"}},
                {strategy_id: "2026-07-15"},
                set(),
                date(2026, 7, 16),
            )
        )

    def test_page_legacy_archive_boundary_is_preserved_in_ranking_stub(self) -> None:
        strategy_id = "gfsec_robot__moneyfund"
        stub = MODULE.ranking_stub_from_summary(
            {
                "统一策略ID": strategy_id,
                "策略名称": "财富管家历史接口货币策略",
                "投顾机构": "广发证券",
                "渠道": "广发证券",
                "是否纳入常规排名": 0,
                "仅列表展示": 1,
                "是否历史接口留档": 1,
                "策略治理状态": "历史接口留档",
            }
        )

        reason = MODULE.strategy_exclusion_reason(
            stub,
            {strategy_id: {"是否纳入常规排名": 1, "治理状态": "正常运行"}},
            {strategy_id: "2026-07-15"},
            set(),
            date(2026, 7, 16),
        )

        self.assertEqual(reason, "历史接口留档")
        self.assertEqual(stub["channel"], "广发证券")

    def test_page_governance_boundary_overrides_raw_ranking_row(self) -> None:
        strategy_id = "gfsec_robot__moneyfund"
        raw_row = {
            "id": strategy_id,
            "code": strategy_id,
            "entityType": "投顾策略",
            "name": "小白理财",
            "channel": "广发证券",
        }
        summary_row = {
            "统一策略ID": strategy_id,
            "策略名称": "小白理财",
            "投顾机构": "广发证券",
            "渠道": "广发证券",
            "是否纳入常规排名": 0,
            "仅列表展示": 1,
            "是否历史接口留档": 1,
            "策略治理状态": "历史接口留档",
        }

        merged = MODULE.apply_page_governance_boundary(raw_row, summary_row)
        reason = MODULE.strategy_exclusion_reason(
            merged,
            {},
            {strategy_id: "2026-07-15"},
            set(),
            date(2026, 7, 16),
        )

        self.assertEqual(merged["pageRankable"], 0)
        self.assertEqual(reason, "历史接口留档")

    def test_strategy_summary_business_facts_override_stale_zero_asset_placeholder(self) -> None:
        stale_ranking_row = {
            "产品ID": "gffunds__ZY00000014",
            "产品名称": "幸福小列车第0期",
            "基准风险资产权重": "L0",
            "基准风险资产权重_百分比": 0.0,
            "基准权益权重": None,
            "基准债券权重": None,
            "基准互斥权重合计_百分比": 0.0,
            "正式可比池": "",
        }
        summary_row = {
            "统一策略ID": "gffunds__ZY00000014",
            "策略名称": "幸福小列车第0期",
            "渠道": "广发基金",
            "投顾机构": "广发基金",
            "有基准": "是",
            "有业绩走势": "是",
            "有历史仓位": "是",
            "对客未终止": "是",
            "基准风险资产权重": "L1",
            "基准风险资产权重_百分比": 10,
            "基准权益权重": 10,
            "基准债券权重": 90,
            "基准货币权重": 0,
            "基准资产大类-商品": 0,
            "基准资产大类-另类": 0,
            "基准互斥权重合计_百分比": 100,
            "基准结构类型": "权益+债券主导",
            "非权益比较轨道": "债券主导",
            "正式可比池": "L1+债券主导",
            "可比池样本资格": "是",
            "可比池说明": "权益分档=L1",
            "业绩基准": "沪深300指数收益率*10%+中债综合全价指数收益率*90%",
            "基准映射置信度": "高",
        }

        merged = MODULE.apply_strategy_summary_business_facts(
            stale_ranking_row,
            summary_row,
        )

        self.assertEqual(merged["基准风险资产权重"], "L1")
        self.assertEqual(merged["基准风险资产权重_百分比"], 0.1)
        self.assertEqual(merged["基准权益权重"], 0.1)
        self.assertEqual(merged["基准债券权重"], 0.9)
        self.assertEqual(merged["基准互斥权重合计_百分比"], 100)
        self.assertEqual(merged["正式可比池"], "L1+债券主导")
        self.assertEqual(merged["基准风险资产权重来源"], "策略列表统一业务事实")
        self.assertEqual(merged["解析置信度"], "高")


if __name__ == "__main__":
    unittest.main()
