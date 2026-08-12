from __future__ import annotations

import unittest
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parents[4]
EXPORTER = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "export_basic_data_pages.py"
NAVIGATION = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "basic_data_navigation.py"
MINIMAL = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_minimal_publish_set.py"
DETAIL_ASSET = CODE_ROOT / "basic_data" / "assets" / "strategy-detail.js"
INSTITUTION_ASSET = CODE_ROOT / "basic_data" / "assets" / "institutions.js"
COMMON_ASSET = CODE_ROOT / "basic_data" / "assets" / "basic-common.js"
STRATEGY_ASSET = CODE_ROOT / "basic_data" / "assets" / "strategies.js"
AI_STRATEGY_ASSET = CODE_ROOT / "basic_data" / "assets" / "ai-strategy.js"
RANKING_ASSET = CODE_ROOT / "basic_data" / "assets" / "mixed-performance-scatter.js"
THEME_ASSET = CODE_ROOT / "basic_data" / "assets" / "basic.css"
MANIFEST_BUILDER = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "write_analysis_platform_deploy_manifest.py"


class InstitutionListPageTests(unittest.TestCase):
    def test_report_builder_writes_institution_shell_and_asset(self) -> None:
        source = EXPORTER.read_text(encoding="utf-8")
        self.assertIn('write_text(site / "institutions.html"', source)
        self.assertIn('write_text(site / "assets" / "institutions.js"', source)
        self.assertIn('page_html("机构总览", "institutions"', source)

    def test_navigation_and_minimal_package_include_institutions_not_monthly_report(self) -> None:
        navigation = NAVIGATION.read_text(encoding="utf-8")
        minimal = MINIMAL.read_text(encoding="utf-8")
        self.assertIn('("institutions.html", "机构总览", "institutions")', navigation)
        self.assertIn('"institutions.html": {"basic_summary_core.js": "institution_overview_pack.js"}', minimal)
        self.assertIn('"institutionAdjustmentEvents"', minimal)
        self.assertIn('code_asset_dir = PROJECT_ROOT / "basic_data" / "assets"', minimal)
        self.assertIn('("institutions.html", "机构总览")', minimal)
        self.assertNotIn("MONTHLY_REBALANCE_REPORT", minimal)
        self.assertNotIn('"调仓月报"', minimal)

    def test_institution_overview_is_first_navigation_item_and_default_entry(self) -> None:
        exporter = EXPORTER.read_text(encoding="utf-8")
        navigation = NAVIGATION.read_text(encoding="utf-8")
        minimal = MINIMAL.read_text(encoding="utf-8")
        self.assertLess(
            navigation.index('("institutions.html", "机构总览", "institutions")'),
            navigation.index('("strategies.html", "策略列表", "strategies")'),
        )
        self.assertLess(
            minimal.index('("institutions.html", "机构总览")'),
            minimal.index('("strategies.html", "策略列表")'),
        )
        self.assertIn('url=./institutions.html', exporter)
        self.assertIn('entry_html("./basic_data/institutions.html")', minimal)
        self.assertIn('"entry": "basic_data/institutions.html"', minimal)

    def test_institution_page_reconciles_channel_manager_and_strategy_totals(self) -> None:
        source = INSTITUTION_ASSET.read_text(encoding="utf-8")
        self.assertIn('summarize("渠道")', source)
        self.assertIn('summarize("投顾机构")', source)
        self.assertIn('channelTotals.total !== strategies.length', source)
        self.assertIn('销售渠道、投顾管理人和策略清单未对账', source)

    def test_strategy_detail_displays_explicit_sales_and_manager_labels(self) -> None:
        source = DETAIL_ASSET.read_text(encoding="utf-8")
        self.assertIn('<span>销售渠道</span>', source)
        self.assertIn('<span>投顾管理人</span>', source)
        self.assertIn('${ownershipFacts()}', source)

    def test_institution_filters_trend_drilldown_and_ranking_links_are_wired(self) -> None:
        source = INSTITUTION_ASSET.read_text(encoding="utf-8")
        common = COMMON_ASSET.read_text(encoding="utf-8")
        ranking = RANKING_ASSET.read_text(encoding="utf-8")
        for label in ("有基准", "有业绩走势", "有历史仓位", "对客未终止"):
            self.assertIn(label, source)
        for label in ("策略名称", "销售渠道", "投顾管理机构", "基准风险资产权重", "调仓日期", "调仓说明", "近一月收益率"):
            self.assertIn(label, source)
        self.assertIn('data-point-date=', source)
        self.assertIn('managerChannelSummary', source)
        self.assertIn('riskWeight: bucket', source)
        self.assertIn('withGlobalStrategyFilters', common)
        self.assertIn('initialParams.get("riskWeight")', ranking)
        self.assertIn('matchesGlobalPackFilters', ranking)

    def test_institution_filters_only_cross_pages_through_explicit_links(self) -> None:
        source = INSTITUTION_ASSET.read_text(encoding="utf-8")
        common = COMMON_ASSET.read_text(encoding="utf-8")
        self.assertIn('withGlobalStrategyFilters("./strategies.html"', source)
        self.assertIn('withGlobalStrategyFilters("./mixed-performance-scatter.html"', source)
        self.assertNotIn("GLOBAL_STRATEGY_FILTER_STORAGE_KEY", common)
        self.assertNotIn("localStorage.setItem", common)
        self.assertIn('query.has(config.param)', common)

    def test_institution_page_uses_chinese_section_labels_and_fitted_adjustment_table(self) -> None:
        source = INSTITUTION_ASSET.read_text(encoding="utf-8")
        theme = THEME_ASSET.read_text(encoding="utf-8")
        for label in ("数据筛选范围", "市场调仓走势", "基准风险资产权重", "当前机构分布"):
            self.assertIn(label, source)
        for legacy in ("GLOBAL DATA SCOPE", "MARKET ADJUSTMENT PULSE", "BENCHMARK RISK ASSET WEIGHT", "SELECTED DISTRIBUTION"):
            self.assertNotIn(legacy, source)
        self.assertIn("institution-adjustment-col-note", source)
        self.assertIn("table-layout:fixed", theme)
        self.assertIn("overflow-x:hidden", theme)

    def test_ai_candidate_business_fields_share_strategy_list_definition(self) -> None:
        common = COMMON_ASSET.read_text(encoding="utf-8")
        strategies = STRATEGY_ASSET.read_text(encoding="utf-8")
        ai_strategy = AI_STRATEGY_ASSET.read_text(encoding="utf-8")
        self.assertIn("const strategyListHeaders = Object.freeze", common)
        self.assertIn("const headers = B.strategyListHeaders", strategies)
        self.assertIn('return ["命中说明", ...B.strategyListHeaders]', ai_strategy)

    def test_minimal_manifest_and_theme_match_current_product_contract(self) -> None:
        manifest = MANIFEST_BUILDER.read_text(encoding="utf-8")
        theme = THEME_ASSET.read_text(encoding="utf-8")
        self.assertIn('choices=("minimal_publish",)', manifest)
        self.assertIn('"basic_data_institutions"', manifest)
        minimal_block = manifest[manifest.index('if args.page_set == "minimal_publish"'):]
        self.assertNotIn('"basic_data_monthly_rebalance_report"', minimal_block.split('strategy_manifest', 1)[0])
        self.assertIn('--brand:#4F7888', theme)
        self.assertIn('--entity:#B86B3E', theme)
        self.assertIn('--kpi:#264F63', theme)
        self.assertIn('--pos:#B33F46', theme)
        self.assertIn('--neg:#3F7B56', theme)
        self.assertIn('.institution-entity-row b,', theme)
        self.assertIn('color:var(--entity) !important', theme)
        self.assertIn('.institution-trend-kpis strong:not(.is-date)', theme)
        self.assertIn('color:var(--kpi) !important', theme)
        self.assertIn('.strategy-table tbody .strategy-institution-cell', theme)
        self.assertIn('ai-sticky-institution', theme)
        self.assertIn('#B86B3E 55%', theme)
        self.assertIn('flex-direction:row !important', theme)

    def test_maintained_pages_do_not_emit_legacy_bucket_names(self) -> None:
        maintained_assets = [
            "basic-common.js", "strategies.js", "institutions.js", "strategy-detail.js",
            "insights.js", "mixed-performance-scatter.js", "fund-detail.js", "ai-strategy.js",
        ]
        forbidden = ("基准权益分档", "基准权益分类档", "广义权益分档", "广义分档")
        for name in maintained_assets:
            text = (CODE_ROOT / "basic_data" / "assets" / name).read_text(encoding="utf-8")
            for phrase in forbidden:
                self.assertNotIn(phrase, text, f"{name} still emits {phrase}")


if __name__ == "__main__":
    unittest.main()
