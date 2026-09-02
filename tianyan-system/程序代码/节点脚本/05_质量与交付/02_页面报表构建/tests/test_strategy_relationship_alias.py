from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path


SCRIPT_PATH = next(
    parent / "_共享组件" / "生产程序" / "export_basic_data_pages.py"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "生产程序" / "export_basic_data_pages.py").is_file()
)
SPEC = importlib.util.spec_from_file_location("export_basic_data_pages_relationship_test", SCRIPT_PATH)
assert SPEC and SPEC.loader
sys.path.insert(0, str(SCRIPT_PATH.parent))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StrategyRelationshipAliasTests(unittest.TestCase):
    def test_performance_completeness_is_independent_of_benchmark_and_holdings(self) -> None:
        result = MODULE.performance_completeness_fields(
            "2026-08-06",
            date(2026, 8, 7),
            30,
            11,
        )
        self.assertEqual(result["业绩完整"], "是")
        self.assertEqual(result["业绩完整性"], "完整")

    def test_performance_completeness_rejects_stale_sparse_or_broken_curves(self) -> None:
        stale = MODULE.performance_completeness_fields("2026-07-20", date(2026, 8, 7), 30, 11)
        sparse = MODULE.performance_completeness_fields("2026-08-06", date(2026, 8, 7), 1, None)
        broken = MODULE.performance_completeness_fields("2026-08-06", date(2026, 8, 7), 30, 46)
        self.assertEqual(stale["业绩完整"], "否")
        self.assertIn("超过5天", stale["业绩完整性说明"])
        self.assertEqual(sparse["业绩完整"], "否")
        self.assertIn("少于2个", sparse["业绩完整性说明"])
        self.assertEqual(broken["业绩完整"], "否")
        self.assertIn("超过45天", broken["业绩完整性说明"])

    def test_calendar_month_interval_uses_same_day_and_clamps_month_end(self) -> None:
        self.assertEqual(MODULE.calendar_months_ago(date(2026, 7, 28), 3), date(2026, 4, 28))
        self.assertEqual(MODULE.calendar_months_ago(date(2026, 3, 31), 1), date(2026, 2, 28))

    def test_only_proven_official_domain_is_aliased(self) -> None:
        values = {"parent": {"近三月": -0.2586}}
        relationships = {
            "child": {
                "官方业绩策略ID": "parent",
                "持仓策略ID": "another-parent",
            }
        }
        result = MODULE.apply_relationship_aliases(values, relationships)
        self.assertEqual(result["child"]["近三月"], -0.2586)
        self.assertIsNot(result["child"], result["parent"])

    def test_scalar_benchmark_alias_does_not_turn_text_into_character_list(self) -> None:
        values = {"parent": "中证800指数收益率×60%+中债综合指数收益率×40%"}
        relationships = {"child": {"官方业绩策略ID": "parent"}}
        result = MODULE.apply_relationship_aliases(values, relationships)
        self.assertEqual(result["child"], values["parent"])

    def test_missing_benchmark_text_is_inherited_but_child_disclosure_is_preserved(self) -> None:
        values = {
            "parent": "母策略官方基准",
            "empty-child": "",
            "own-child": "子策略独立披露基准",
        }
        relationships = {
            "empty-child": {"官方业绩策略ID": "parent"},
            "own-child": {"官方业绩策略ID": "parent"},
        }
        result = MODULE.fill_missing_relationship_aliases(values, relationships)
        self.assertEqual(result["empty-child"], "母策略官方基准")
        self.assertEqual(result["own-child"], "子策略独立披露基准")

    def test_rebalance_event_uses_canonical_institution_even_when_raw_alias_is_present(self) -> None:
        events = [{"统一策略ID": "child", "投顾机构": "广发基金投顾", "策略名称": "期次策略"}]
        context = {
            "child": {
                "listRow": {
                    "统一策略ID": "child",
                    "投顾机构": "广发基金",
                    "策略名称": "期次策略",
                    "渠道": "天天基金/投顾",
                }
            }
        }
        result = MODULE.enrich_rebalance_events_with_strategy_fields(events, context)
        self.assertEqual(result[0]["投顾机构"], "广发基金")
        self.assertEqual(result[0]["是否广发"], "是")

    def test_placeholder_benchmark_record_is_filled_from_proven_source(self) -> None:
        values = {
            "parent": {"基准可用状态": "文本+曲线", "业绩基准文本": "母策略官方基准"},
            "child": {"基准可用状态": "缺失", "业绩基准文本": ""},
        }
        relationships = {"child": {"官方业绩策略ID": "parent"}}
        result = MODULE.fill_missing_relationship_records(
            values,
            relationships,
            "基准可用状态",
        )
        self.assertEqual(result["child"]["基准可用状态"], "文本+曲线")
        self.assertEqual(result["child"]["业绩基准文本"], "母策略官方基准")
        self.assertIsNot(result["child"], result["parent"])

    def test_equivalent_child_benchmark_inherits_curve_domain_without_losing_child_fee(self) -> None:
        strategies = {
            "parent": {"业绩基准": "中证800指数×60% + 中债综合指数×40%"},
            "child": {"业绩基准": "中证800指数*60%+中债综合指数*40%"},
        }
        relationships = {"child": {"官方业绩策略ID": "parent"}}
        statuses = {
            "parent": {
                "业绩基准文本": strategies["parent"]["业绩基准"],
                "基准可用状态": "文本+曲线",
                "基准曲线状态": "有日度基准曲线",
                "年化投顾费率_百分比": 0.5,
            },
            "child": {
                "业绩基准文本": strategies["child"]["业绩基准"],
                "基准可用状态": "仅文本",
                "基准曲线状态": "缺失",
                "年化投顾费率_百分比": 0.2,
                "费率状态": "已结构化",
            },
        }
        assets = {
            "parent": {"统一策略ID": "parent", "业绩基准文本": strategies["parent"]["业绩基准"], "基准资产大类-权益": 60.0}
        }

        text_map, status_map, asset_map, inherited, conflicts = MODULE.resolve_relationship_benchmark_domains(
            strategies,
            relationships,
            statuses,
            assets,
        )

        self.assertEqual(text_map["child"], strategies["child"]["业绩基准"])
        self.assertEqual(status_map["child"]["基准可用状态"], "文本+曲线")
        self.assertEqual(status_map["child"]["年化投顾费率_百分比"], 0.2)
        self.assertEqual(status_map["child"]["基础数据等级"], "A")
        self.assertEqual(asset_map["child"]["基准资产大类-权益"], 60.0)
        self.assertEqual(inherited, {"child": "parent"})
        self.assertEqual(conflicts, {})

    def test_conflicting_child_benchmark_is_preserved_and_not_inherited(self) -> None:
        strategies = {
            "parent": {"业绩基准": "中证800指数×60%+中债综合指数×40%"},
            "child": {"业绩基准": "沪深300指数×100%"},
        }
        relationships = {"child": {"官方业绩策略ID": "parent"}}
        statuses = {
            "parent": {"业绩基准文本": strategies["parent"]["业绩基准"], "基准可用状态": "文本+曲线"},
            "child": {"业绩基准文本": strategies["child"]["业绩基准"], "基准可用状态": "仅文本"},
        }

        text_map, status_map, _, inherited, conflicts = MODULE.resolve_relationship_benchmark_domains(
            strategies,
            relationships,
            statuses,
            {},
        )

        self.assertEqual(text_map["child"], strategies["child"]["业绩基准"])
        self.assertEqual(status_map["child"]["基准可用状态"], "仅文本")
        self.assertEqual(inherited, {})
        self.assertIn("child", conflicts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
