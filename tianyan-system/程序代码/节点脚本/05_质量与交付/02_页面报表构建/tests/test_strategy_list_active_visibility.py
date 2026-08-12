from __future__ import annotations

import unittest
from pathlib import Path


ASSET = Path(__file__).resolve().parents[4] / "basic_data" / "assets" / "strategies.js"
COMMON_ASSET = Path(__file__).resolve().parents[4] / "basic_data" / "assets" / "basic-common.js"
THEME_ASSET = Path(__file__).resolve().parents[4] / "basic_data" / "assets" / "basic.css"
INSIGHTS_ASSET = Path(__file__).resolve().parents[4] / "basic_data" / "assets" / "insights.js"


class StrategyListActiveVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = ASSET.read_text(encoding="utf-8")
        cls.common = COMMON_ASSET.read_text(encoding="utf-8")
        cls.theme = THEME_ASSET.read_text(encoding="utf-8")
        cls.insights = INSIGHTS_ASSET.read_text(encoding="utf-8")

    def test_product_scope_presets_always_filter_from_full_universe(self) -> None:
        self.assertNotIn("const rowsBase", self.source)
        self.assertIn("return allStrategies.filter((row) => {", self.source)
        self.assertIn('if (scope === "all") return true;', self.source)
        self.assertIn('if (scope === "stopped") return stopped;', self.source)
        self.assertIn('if (scope === "active") return !stopped;', self.source)
        self.assertIn("return facts.benchmark && facts.performance && facts.history && facts.active;", self.source)
        for scope in ("recommended", "active", "stopped", "all"):
            self.assertIn(f'<option value="{scope}"', self.source)

    def test_default_conditions_are_page_initial_state_not_cross_page_base_pool(self) -> None:
        self.assertIn("const defaultEnabled = isInstitutionOverviewPage();", self.common)
        self.assertIn(": defaultEnabled,", self.common)
        self.assertIn("Boolean(B.hasExplicitGlobalStrategyFilters)", self.source)
        self.assertIn("state.incomingGlobalFiltersActive && !B.matchesGlobalStrategyFilters(row)", self.source)
        self.assertIn('B.byId("productStatusSelect").value = "recommended";', self.source)

    def test_incoming_institution_filters_are_visible_and_clearable(self) -> None:
        self.assertIn("从机构总览带入", self.source)
        self.assertIn('id="clearIncomingScope"', self.source)
        self.assertIn("clearIncomingGlobalFilters", self.source)
        self.assertIn("target.searchParams.delete(config.param)", self.source)

    def test_guangfa_channel_detection_includes_bank_and_securities(self) -> None:
        self.assertIn("广发银行|广发证券", self.source)

    def test_classification_fields_are_trailing_in_required_order(self) -> None:
        self.assertIn(
            '"研报产品类型", "研报股票子类型", "业务分类", "市场地域", "主动被动",\n'
            '      "披露策略类型", "天天当前对客展示", "基准可用状态"',
            self.common,
        )
        self.assertIn('"调仓次数",\n    ...strategyListFieldGroups.trailing', self.common)

    def test_client_facing_header_uses_short_display_label(self) -> None:
        self.assertIn(
            'field === "天天当前对客展示" ? "对客展示" : label(field)',
            self.common,
        )
        self.assertIn('>${label}<span class="sort-arrow">', self.source)
        self.assertNotIn('>${B.esc(label)}<span class="sort-arrow">', self.source)

    def test_ttfund_display_status_follows_performance_metrics(self) -> None:
        self.assertIn(
            '...strategyListFieldGroups.risks,\n    "夏普比率", "风险等级", "业绩基准说明", "最新业绩日期", "天天展示状态",',
            self.common,
        )

    def test_filter_summary_highlights_authoritative_sync_time(self) -> None:
        self.assertIn("summary?.overview?.数据刷新时间", self.source)
        self.assertIn("最近一次数据同步：${dataSyncTime}", self.source)
        self.assertIn("color:var(--pos)", self.source)

    def test_return_colors_follow_chinese_market_convention_with_muted_palette(self) -> None:
        self.assertIn('const cls = number > 0 ? "ret-pos" : number < 0 ? "ret-neg"', self.common)
        self.assertIn("--pos:#B33F46", self.theme)
        self.assertIn("--neg:#3F7B56", self.theme)
        self.assertIn("--brand:#4F7888", self.theme)
        self.assertIn("--entity:#B86B3E", self.theme)
        self.assertIn("--kpi:#264F63", self.theme)
        self.assertNotIn("--brand:#f36b15", self.theme)

    def test_benchmark_bucket_is_the_only_visible_bucket_and_uses_risk_asset_semantics(self) -> None:
        self.assertIn('filterControl("基准风险资产权重"', self.source)
        self.assertIn("权益、商品和另类风险资产合计权重", self.source)
        self.assertNotIn('filterControl("广义权益分档"', self.source)
        self.assertNotIn('id="broadEquityBucketSelect"', self.source)
        self.assertIn('const strategyListInstitutionField = "销售渠道/管理机构"', self.common)
        self.assertIn('"策略名称", strategyListInstitutionField, "基准风险资产权重",', self.common)
        self.assertIn('"夏普比率", "风险等级", "业绩基准说明", "最新业绩日期", "天天展示状态",', self.common)
        self.assertNotIn('"策略名称", "渠道", "投顾机构"', self.common)
        self.assertIn('const strategyChannelDisplayNames = Object.freeze({ "天天基金/投顾": "天天基金" })', self.common)
        self.assertIn('return `${channel}/${manager}`', self.common)
        self.assertIn('field === B.strategyListInstitutionField', self.source)

    def test_strategy_list_drives_comparison_with_two_to_five_selected_rows(self) -> None:
        self.assertIn("const compareMaxCount = 5", self.source)
        self.assertIn('id="selectPageStrategies"', self.source)
        self.assertIn("data-strategy-select", self.source)
        self.assertIn('id="strategyCompareButton"', self.source)
        self.assertIn('params.set("compare", [...state.selectedIds].join(","))', self.source)
        self.assertIn("./compare.html?${params.toString()}", self.source)
        self.assertIn(".strategy-table .sticky-select", self.theme)

    def test_standalone_compare_page_contains_results_without_filter_or_selector_panels(self) -> None:
        self.assertIn('${compareStandalone ? "" : compareSelectorBlock()}', self.insights)
        self.assertIn('${compareStandalone ? "" : `<section class="panel insight-sticky-controls">', self.insights)
        self.assertIn("返回策略列表重新选择", self.insights)
        self.assertIn("尚未收到需要对比的策略", self.insights)


if __name__ == "__main__":
    unittest.main()
