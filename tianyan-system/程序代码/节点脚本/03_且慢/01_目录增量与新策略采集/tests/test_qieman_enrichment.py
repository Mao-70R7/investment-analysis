from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[4]
    / "official_apps"
    / "qieman"
    / "production"
    / "enrich_qieman_strategy_master.py"
)
SPEC = importlib.util.spec_from_file_location("qieman_enrichment", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_parse_holding_period_from_official_text() -> None:
    assert MODULE.parse_holding_period("本策略适合持有3年以上") == "3年以上"
    assert MODULE.parse_holding_period("投资期限建议：2个月以上") == "2个月以上"


def test_parse_holding_period_does_not_infer_unlabelled_duration() -> None:
    assert MODULE.parse_holding_period("策略已运行6年87天") is None


def test_official_detail_lineage_is_promoted_without_inference() -> None:
    row = {"advisor_name": "示例投顾", "strategy_type": None}
    extra = {
        "stargate_detail_lineage": {
            "advisor_name": "official_stargate_detail:策略管理人",
            "strategy_type": "official_stargate_detail:策略类型",
        }
    }
    lineage: dict[str, str] = {}
    sources: Counter[str] = Counter()
    MODULE.promote_official_detail_lineage(row, extra, lineage, sources)
    assert lineage == {"advisor_name": "official_stargate_detail:策略管理人"}
    assert sources["advisor_name:stargate_detail"] == 1
    assert sources["strategy_type:stargate_detail"] == 0
