from __future__ import annotations

import math
from typing import Any


ASSET_TOTAL_TOLERANCE_PP = 0.01
UNKNOWN_TOLERANCE_PP = 0.01
DOMINANCE_THRESHOLD = 0.80

TRACK_BY_ASSET = {
    "债券": "债券主导",
    "货币": "货币主导",
    "商品": "商品主导",
    "另类": "另类主导",
}


def as_number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_comparison_pool(
    *,
    bucket: str,
    equity: Any,
    bond: Any,
    cash: Any,
    commodity: Any,
    alternative: Any,
    unknown: Any,
) -> dict[str, Any]:
    values = {
        "权益": as_number(equity),
        "债券": as_number(bond),
        "货币": as_number(cash),
        "商品": as_number(commodity),
        "另类": as_number(alternative),
        "未知": as_number(unknown),
    }
    normalized = {key: (value if value is not None else 0.0) for key, value in values.items()}
    total = sum(normalized.values())
    unknown_value = normalized["未知"]
    valid_bucket = bucket in {f"L{index}" for index in range(11)}
    if not valid_bucket:
        return {
            "基准结构类型": "未知",
            "非权益比较轨道": "未纳入",
            "正式可比池": "",
            "可比池样本资格": "否",
            "可比池说明": "基准权益未分档",
            "基准互斥权重合计_百分比": round(total, 6),
        }
    if unknown_value > UNKNOWN_TOLERANCE_PP:
        return {
            "基准结构类型": "未知",
            "非权益比较轨道": "未纳入",
            "正式可比池": "",
            "可比池样本资格": "否",
            "可比池说明": f"基准未知权重{unknown_value:.4f}%超过0.01%",
            "基准互斥权重合计_百分比": round(total, 6),
        }
    if abs(total - 100.0) > ASSET_TOTAL_TOLERANCE_PP:
        return {
            "基准结构类型": "未知",
            "非权益比较轨道": "未纳入",
            "正式可比池": "",
            "可比池样本资格": "否",
            "可比池说明": f"互斥资产权重合计{total:.4f}%不等于100%",
            "基准互斥权重合计_百分比": round(total, 6),
        }

    residual = {key: normalized[key] for key in ("债券", "货币", "商品", "另类")}
    residual_total = sum(residual.values())
    if residual_total <= ASSET_TOTAL_TOLERANCE_PP:
        track = "纯权益"
    else:
        dominant_asset, dominant_weight = max(residual.items(), key=lambda item: (item[1], item[0]))
        dominant_ratio = dominant_weight / residual_total
        track = TRACK_BY_ASSET[dominant_asset] if dominant_ratio >= DOMINANCE_THRESHOLD else "多资产"

    active_assets = [key for key in ("权益", "债券", "货币", "商品", "另类") if normalized[key] > ASSET_TOTAL_TOLERANCE_PP]
    structure = track if len(active_assets) <= 1 else ("多资产型" if track == "多资产" else f"权益+{track}")
    return {
        "基准结构类型": structure,
        "非权益比较轨道": track,
        "正式可比池": f"{bucket}+{track}",
        "可比池样本资格": "是",
        "可比池说明": f"权益分档={bucket}；非权益资产中80%以上由单一资产贡献时按该资产主导，否则为多资产",
        "基准互斥权重合计_百分比": round(total, 6),
    }

