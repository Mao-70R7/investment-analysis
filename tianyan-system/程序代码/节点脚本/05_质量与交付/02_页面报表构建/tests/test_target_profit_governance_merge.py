from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_target_profit_analysis_pack.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("target_profit_pack", SCRIPT_PATH)
assert SPEC and SPEC.loader
target_profit_pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(target_profit_pack)


def test_governance_target_rows_are_forced_into_page_pack_scope() -> None:
    summary_rows = [
        {
            "统一策略ID": "ttfund__1",
            "策略名称": "摘要名称",
            "策略治理状态": "旧状态",
            "累计收益率": 5.0,
        }
    ]
    governance_rows = [
        {
            "统一策略ID": "ttfund__1",
            "策略名称": "治理名称",
            "策略治理状态": "目标盈期次",
            "分析分组": "目标盈期次-运行中",
        },
        {
            "统一策略ID": "gffunds__2",
            "策略名称": "治理新增期次",
            "策略治理状态": "目标盈期次",
            "分析分组": "目标盈期次-运行中",
        },
    ]

    merged = target_profit_pack.merge_governance_target_rows(summary_rows, governance_rows)

    assert [row["统一策略ID"] for row in merged] == ["ttfund__1", "gffunds__2"]
    assert merged[0]["策略名称"] == "摘要名称"
    assert merged[0]["累计收益率"] == 5.0
    assert merged[0]["策略治理状态"] == "目标盈期次"
    assert merged[1]["策略名称"] == "治理新增期次"


def test_target_profit_periods_group_institution_aliases_together() -> None:
    rows = [
        {
            "统一策略ID": "gffunds__1",
            "策略名称": "幸福小心愿目标盈第1期",
            "投顾机构": "广发基金投顾",
        },
        {
            "统一策略ID": "gffunds__2",
            "策略名称": "幸福小心愿目标盈第2期",
            "投顾机构": "广发基金有限公司",
        },
    ]

    periods, _ = target_profit_pack.build_period_rows(rows, {}, {})

    assert {row["投顾机构"] for row in periods} == {"广发基金"}
    assert {row["投顾机构原始值"] for row in periods} == {"广发基金投顾", "广发基金有限公司"}
    assert len({row["系列ID"] for row in periods}) == 1


if __name__ == "__main__":
    test_governance_target_rows_are_forced_into_page_pack_scope()
    test_target_profit_periods_group_institution_aliases_together()
    print("ok")
