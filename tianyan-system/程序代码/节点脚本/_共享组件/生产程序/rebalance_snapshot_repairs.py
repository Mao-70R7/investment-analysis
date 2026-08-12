from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any


WEIGHT_SUM_TARGET = 100.0
WEIGHT_SUM_TOLERANCE = 1.0


@dataclass
class DetailRepairResult:
    details: list[dict[str, Any]]
    repairs: list[str]
    excluded_detail_count: int = 0
    excluded_weight_pct: float = 0.0
    repaired_weight_sum_pct: float | None = None
    is_liquidation: bool = False


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def norm_code(value: Any) -> str | None:
    text = norm_text(value)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits and len(digits) <= 6:
        return digits.zfill(6)
    return text


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text == "--":
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def profile_max_date(profile: Any) -> str | None:
    if profile is None:
        return None
    if isinstance(profile, dict):
        value = profile.get("max_date") or profile.get("最大净值日期")
    else:
        value = getattr(profile, "max_date", None)
    return str(value) if value else None


def positive_after_sum(details: list[dict[str, Any]]) -> float:
    return sum(max(to_float(row.get("调后权重_百分比")) or 0.0, 0.0) for row in details)


def positive_before_sum(details: list[dict[str, Any]]) -> float:
    return sum(max(to_float(row.get("调前权重_百分比")) or 0.0, 0.0) for row in details)


def has_liquidation_hint(event: dict[str, Any]) -> bool:
    text = " ".join(
        norm_text(event.get(key)) or ""
        for key in ["策略名称", "调仓标题", "调仓原因"]
    )
    keywords = ["目标盈", "小目标", "止盈", "到期", "清盘", "目标收益", "等待止盈", "运作到期"]
    if any(keyword in text for keyword in keywords):
        return True
    status = norm_text(event.get("策略状态"))
    return status in {"2", "已结束", "终止", "清盘"}


def is_liquidation_details(event: dict[str, Any], details: list[dict[str, Any]]) -> bool:
    if not details or positive_after_sum(details) > 0:
        return False
    return positive_before_sum(details) > 0 or has_liquidation_hint(event)


def is_suspect_other_placeholder(
    event: dict[str, Any],
    detail: dict[str, Any],
    fund_profiles: dict[str, Any] | None,
) -> bool:
    after_weight = to_float(detail.get("调后权重_百分比")) or 0.0
    if after_weight <= 0:
        return False

    group_name = norm_text(detail.get("分组名称")) or ""
    if "其他" not in group_name:
        return False

    before_weight = to_float(detail.get("调前权重_百分比"))
    if before_weight is not None and before_weight > 0.0001:
        return False

    code = norm_code(detail.get("基金代码"))
    fund_name = norm_text(detail.get("基金名称")) or ""
    name_missing_or_code = not fund_name or (code is not None and fund_name == code)
    max_nav_date = profile_max_date((fund_profiles or {}).get(code)) if code else None
    event_date = norm_text(event.get("调仓日期") or event.get("本次仓位日期"))
    stale_before_event = bool(max_nav_date and event_date and max_nav_date < event_date)

    return name_missing_or_code or stale_before_event


def missing_before_positive_after_indexes(details: list[dict[str, Any]]) -> list[int]:
    indexes: list[int] = []
    for index, detail in enumerate(details):
        after_weight = to_float(detail.get("调后权重_百分比")) or 0.0
        before_weight = to_float(detail.get("调前权重_百分比"))
        if after_weight > 0 and before_weight is None:
            indexes.append(index)
    return indexes


def choose_exclusion_combo(
    total_weight: float,
    candidate_weights: list[float],
    *,
    target: float = WEIGHT_SUM_TARGET,
    tolerance: float = WEIGHT_SUM_TOLERANCE,
) -> tuple[int, ...] | None:
    if not candidate_weights:
        return None

    best_combo: tuple[int, ...] | None = None
    best_gap = abs(total_weight - target)
    max_pick = min(4, len(candidate_weights))
    indexes = range(len(candidate_weights))
    for size in range(1, max_pick + 1):
        for combo in itertools.combinations(indexes, size):
            repaired_sum = total_weight - sum(candidate_weights[index] for index in combo)
            gap = abs(repaired_sum - target)
            if gap <= tolerance and gap < best_gap:
                best_gap = gap
                best_combo = combo
    return best_combo


def repair_rebalance_details(
    event: dict[str, Any],
    details: list[dict[str, Any]],
    fund_profiles: dict[str, Any] | None = None,
    *,
    target: float = WEIGHT_SUM_TARGET,
    tolerance: float = WEIGHT_SUM_TOLERANCE,
) -> DetailRepairResult:
    repairs: list[str] = []
    total_weight = positive_after_sum(details)
    liquidation = is_liquidation_details(event, details)
    if liquidation:
        repairs.append("调后正基金权重为0，按清盘/止盈空仓处理")
        return DetailRepairResult(details=list(details), repairs=repairs, repaired_weight_sum_pct=0.0, is_liquidation=True)

    if total_weight <= target + tolerance:
        return DetailRepairResult(details=list(details), repairs=repairs, repaired_weight_sum_pct=total_weight)

    excess_weight = total_weight - target
    missing_before_indexes = missing_before_positive_after_indexes(details)
    missing_before_weight = sum(
        to_float(details[index].get("调后权重_百分比")) or 0.0
        for index in missing_before_indexes
    )
    if missing_before_indexes and abs(missing_before_weight - excess_weight) <= tolerance:
        repaired_details = [detail for index, detail in enumerate(details) if index not in set(missing_before_indexes)]
        repaired_sum = positive_after_sum(repaired_details)
        excluded_codes = [
            norm_code(details[index].get("基金代码")) or "-"
            for index in missing_before_indexes
        ]
        repairs.append(
            "剔除调前权重缺失的异常占位行"
            f"{len(missing_before_indexes)}行({','.join(excluded_codes)})，"
            f"剔除权重{missing_before_weight:.4f}%，修复后权重和{repaired_sum:.4f}%"
        )
        return DetailRepairResult(
            details=repaired_details,
            repairs=repairs,
            excluded_detail_count=len(missing_before_indexes),
            excluded_weight_pct=missing_before_weight,
            repaired_weight_sum_pct=repaired_sum,
            is_liquidation=False,
        )

    candidate_indexes: list[int] = []
    candidate_weights: list[float] = []
    for index, detail in enumerate(details):
        if is_suspect_other_placeholder(event, detail, fund_profiles):
            candidate_indexes.append(index)
            candidate_weights.append(to_float(detail.get("调后权重_百分比")) or 0.0)

    combo = choose_exclusion_combo(total_weight, candidate_weights, target=target, tolerance=tolerance)
    if combo is None:
        return DetailRepairResult(details=list(details), repairs=repairs, repaired_weight_sum_pct=total_weight)

    excluded_indexes = {candidate_indexes[index] for index in combo}
    repaired_details = [detail for index, detail in enumerate(details) if index not in excluded_indexes]
    excluded_weight = sum(to_float(details[index].get("调后权重_百分比")) or 0.0 for index in excluded_indexes)
    repaired_sum = positive_after_sum(repaired_details)
    excluded_codes = [
        norm_code(details[index].get("基金代码")) or "-"
        for index in sorted(excluded_indexes)
    ]
    repairs.append(
        "剔除其他分组异常占位行"
        f"{len(excluded_indexes)}行({','.join(excluded_codes)})，"
        f"剔除权重{excluded_weight:.4f}%，修复后权重和{repaired_sum:.4f}%"
    )
    return DetailRepairResult(
        details=repaired_details,
        repairs=repairs,
        excluded_detail_count=len(excluded_indexes),
        excluded_weight_pct=excluded_weight,
        repaired_weight_sum_pct=repaired_sum,
        is_liquidation=False,
    )
