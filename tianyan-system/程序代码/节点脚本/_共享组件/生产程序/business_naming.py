from __future__ import annotations

from typing import Any


GFSEC_SOURCE_CHANNEL_IDS = frozenset({"gfsec_fima", "gfsec_robot"})
GFFUNDS_SOURCE_CHANNEL_IDS = frozenset({"gffunds"})
QIEMAN_SOURCE_CHANNEL_IDS = frozenset({"qieman"})

BUSINESS_CHANNEL_BY_SOURCE_ID = {
    "huaxia_tougu": "华夏投顾/华夏财富查理智投",
    "zocaifu": "中欧财富/中欧钱滚滚",
    "gffunds": "广发基金",
    "gfsec_fima": "广发证券",
    "gfsec_robot": "广发证券",
    "gfbank_cgb": "广发银行发现精彩",
    "ttfund": "天天基金/投顾",
    "harvestwm": "嘉实财富",
    "southern": "南方基金",
    "cmfchina": "招商基金/招财乐投顾",
    "efundcf": "易方达财富/e钱包",
    "fullgoal": "富国基金/富钱包星投顾",
    "fund99": "汇添富基金/现金宝投顾",
    "qieman": "盈米基金",
}

GFSEC_CHANNEL_ALIASES = frozenset(
    {
        "广发证券",
        "广发证券易淘金/财富管家",
        "广发证券易淘金/贝塔牛理财",
        "易淘金",
        "贝塔牛理财",
    }
)
GFFUNDS_NAME_ALIASES = frozenset(
    {
        "广发基金",
        "广发基金-广发投顾",
        "广发基金投顾",
        "广发基金有限公司",
        "广发基金管理有限公司",
    }
)
QIEMAN_CHANNEL_ALIASES = frozenset(
    {
        "且慢",
        "盈米基金",
        "且慢/盈米基金",
        "盈米基金/且慢",
    }
)

UNDISCLOSED_INSTITUTION_VALUES = frozenset(
    {
        "",
        "-",
        "--",
        "未披露",
        "未披露投顾机构",
        "未披露管理机构",
        "未知",
        "未知机构",
        "未识别",
        "未识别机构",
        "待核验",
        "待确认",
        "未分类",
    }
)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().replace("－", "-")


def canonical_business_channel(channel_id: Any, channel_name: Any = None) -> str:
    """Return the business-facing channel while retaining source IDs elsewhere."""

    source_id = _clean_text(channel_id)
    name = _clean_text(channel_name)
    if source_id in GFSEC_SOURCE_CHANNEL_IDS or name in GFSEC_CHANNEL_ALIASES:
        return "广发证券"
    if source_id in GFFUNDS_SOURCE_CHANNEL_IDS or name in GFFUNDS_NAME_ALIASES:
        return "广发基金"
    if source_id in QIEMAN_SOURCE_CHANNEL_IDS or name in QIEMAN_CHANNEL_ALIASES:
        return "盈米基金"
    if source_id in BUSINESS_CHANNEL_BY_SOURCE_ID:
        return BUSINESS_CHANNEL_BY_SOURCE_ID[source_id]
    return name or source_id


def is_undisclosed_institution(value: Any) -> bool:
    """Return whether a manager value carries no disclosed business identity."""

    return _clean_text(value) in UNDISCLOSED_INSTITUTION_VALUES


def canonical_advisor_institution(
    value: Any,
    channel_id: Any = None,
    channel_name: Any = None,
) -> str:
    """Return the business manager name, falling back to the selling channel.

    A disclosed third-party manager is never overwritten.  Only empty or
    explicit undisclosed placeholders fall back to the canonical business
    channel; Qieman is presented under its business institution name
    ``盈米基金``.
    """

    name = _clean_text(value)
    if name in GFFUNDS_NAME_ALIASES:
        return "广发基金"
    if name in QIEMAN_CHANNEL_ALIASES:
        return "盈米基金"
    if not is_undisclosed_institution(name):
        return name
    return canonical_business_channel(channel_id, channel_name)
