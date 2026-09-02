from __future__ import annotations

import unittest
from pathlib import Path


ASSET = Path(__file__).resolve().parents[4] / "basic_data" / "assets" / "ai-strategy.js"


class AiStrategyModelCallPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = ASSET.read_text(encoding="utf-8")

    def test_hybrid_mode_calls_model_only_when_local_parse_needs_help(self) -> None:
        self.assertIn("function localParseNeedsModel(parsed)", self.source)
        self.assertIn("return !hasBusinessFilter(parsed);", self.source)
        self.assertIn("return localParseNeedsModel(localParsed);", self.source)
        self.assertIn("shouldUseModelParser(allowModel, parsed)", self.source)

    def test_common_local_parse_records_zero_model_calls(self) -> None:
        self.assertIn('status: "local-rule", callCount: 0, decision: "local-sufficient"', self.source)
        self.assertIn("本地规则已完整解析（模型 0 次）", self.source)

    def test_each_model_search_uses_two_controlled_stages(self) -> None:
        self.assertEqual(self.source.count("await requestModelRoute(queryText)"), 1)
        self.assertEqual(self.source.count("await requestModelIntent(queryText, route)"), 1)
        self.assertNotIn("shouldReviewEmptyModelIntent", self.source)
        self.assertIn("callCount: Math.max(1, modelCallCount)", self.source)
        self.assertIn("单次执行最多 2 次", self.source)

    def test_first_stage_only_receives_business_routing_catalog(self) -> None:
        self.assertIn("function businessRoutingPromptCatalog()", self.source)
        self.assertIn("业务实体核心框架", self.source)
        self.assertIn("不生成筛选字段、SQL、策略名单或答案", self.source)

    def test_second_stage_uses_hydrated_business_field_cards(self) -> None:
        self.assertIn("function hydrateBusinessRoute(route, queryText)", self.source)
        self.assertIn("相关实体字段卡", self.source)
        self.assertIn("fieldCards", self.source)
        self.assertIn("unsupportedConditions", self.source)
        self.assertNotIn('可用字段：${filterFieldNames().map(fieldLabel).join("、")}', self.source)

    def test_low_overseas_preference_is_resolved_locally(self) -> None:
        self.assertIn("function hasLowOverseasPreference(query)", self.source)
        self.assertIn("function addLowOverseasPreferenceFilter(parsed, query)", self.source)
        self.assertIn('field: "海外配置中枢"', self.source)
        self.assertIn("海外配置中枢不超过10%", self.source)
        self.assertIn("parsed.thresholds.preferLowOverseas", self.source)

    def test_negative_cue_does_not_cross_business_conjunctions(self) -> None:
        self.assertIn('sameClauseChars = "[^\\\\s，。；、,.但且并同最好而又]"', self.source)

    def test_relative_established_date_is_normalized_before_execution(self) -> None:
        self.assertIn("function relativeDateBoundary(expression, asOf)", self.source)
        self.assertIn("function establishedRelativeDateCondition(query, asOf)", self.source)
        self.assertIn("function normalizeParsedDateFilters(parsed)", self.source)
        self.assertIn('field: "成立日期"', self.source)
        self.assertIn('label: `成立日期 >= ${recentEstablished.value}`', self.source)
        self.assertIn("normalizeParsedDateFilters(parsed);", self.source)
        self.assertIn("查询基准日期：${dateText(asOfDate())}", self.source)
        self.assertIn("禁止把自然语言日期直接用于最终比较", self.source)

    def test_explicit_business_date_comparison_is_resolved_locally(self) -> None:
        self.assertIn("function explicitDateFilters(query)", self.source)
        self.assertIn('aliases: ["成立日期", "成立时间", "成立日", "设立日期", "设立时间", "设立日"]', self.source)
        self.assertIn('if (/^(?:大于等于|不早于|不少于|>=|≥)$/.test(text)) return ">=";', self.source)
        self.assertIn("explicitDateFilters(query).forEach((filter) => addGenericFilter(parsed, filter));", self.source)

    def test_default_candidate_pool_uses_performance_completeness_only(self) -> None:
        self.assertIn("function isPerformanceCompleteStrategy(row)", self.source)
        self.assertIn('row?.业绩完整 === "是"', self.source)
        self.assertIn('{ field: "业绩完整", op: "=", value: "是", label: "仅业绩完整策略", system: true }', self.source)
        self.assertIn("base = base.filter(isPerformanceCompleteStrategy)", self.source)
        self.assertNotIn("function isCompleteStrategy(row)", self.source)
        self.assertNotIn('label: "仅完整可比数据"', self.source)

    def test_relative_date_uses_data_snapshot_and_date_aware_scoring(self) -> None:
        self.assertIn("return latest || new Date();", self.source)
        self.assertIn("const marginDays = Math.max(0, (dateFrom(value).getTime() - dateFrom(filter.value).getTime()) / 86400000);", self.source)

    def test_scatter_filters_drawable_rows_before_display_cap(self) -> None:
        self.assertIn("const drawableRows = (rows || []).map", self.source)
        self.assertIn("const sourceRows = drawableRows.slice(0, 500);", self.source)
        self.assertNotIn("(rows || []).slice(0, 500).map", self.source)


if __name__ == "__main__":
    unittest.main()
