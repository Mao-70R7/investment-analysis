from __future__ import annotations

from typing import Any


GFSEC_SOURCE_CHANNEL_IDS = frozenset({"gfsec_fima", "gfsec_robot"})
GFFUNDS_SOURCE_CHANNEL_IDS = frozenset({"gffunds"})
QIEMAN_SOURCE_CHANNEL_IDS = frozenset({"qieman"})

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
        "且慢/盈米基金",
        "盈米基金/且慢",
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
        return "且慢"
    return name or source_id


def canonical_advisor_institution(value: Any) -> str:
    """Merge the approved Guangfa Fund institution aliases."""

    name = _clean_text(value)
    return "广发基金" if name in GFFUNDS_NAME_ALIASES else name
