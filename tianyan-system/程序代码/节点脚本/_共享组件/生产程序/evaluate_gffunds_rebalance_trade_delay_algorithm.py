from __future__ import annotations

import csv
import argparse
import json
import math
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import audit_current_holding_projection as base
import tune_ttfund_current_position_algorithms as tune


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "ttfund_gffunds_trade_delay_algorithm"

CHANNEL_ID = "ttfund"
ADVISOR_NAME = "广发基金"
PRINCIPAL = Decimal("100000000.00")
CALIBRATION_MAX_DIFF_CUTOFF = 10.0

FEE_MODE_NONE = "不扣基金申赎费"
FEE_MODE_ESTIMATED_REDEEM = "估算赎回费"
WEIGHT_RULE_ROUND2 = "2位四舍五入"
WEIGHT_RULE_ROUND2_RESIDUAL_LARGEST = "2位取整尾差补最大基金"
WEIGHT_RULE_ROUND2_RESIDUAL_BASE = "2位取整尾差分摊底仓"
WEIGHT_RULE_TRUNC2_RESIDUAL_BASE = "2位截断尾差分摊底仓"
END_DATE_DISCLOSED_NAV = "披露基金净值日"
END_DATE_PREVIOUS_EOD = "上一日日终"


@dataclass(frozen=True)
class RebalanceSnapshot:
    event_id: str
    strategy_id: str
    channel_id: str
    source_strategy_id: str
    rebalance_date: str | None
    previous_position_date: str | None
    position_date: str | None
    disclosure_date: str | None
    event_seq: int | None
    event_time: str | None
    title: str | None
    before_weights_pct: dict[str, float]
    after_weights_pct: dict[str, float]
    fund_names: dict[str, str | None]
    raw_row_count: int
    positive_after_row_count: int
    missing_code_count: int
    before_weight_sum_pct: float
    after_weight_sum_pct: float

    @property
    def valid_for_projection(self) -> bool:
        return bool(self.after_weights_pct) and self.missing_code_count == 0 and self.positive_after_row_count > 0


@dataclass(frozen=True)
class DelayVariant:
    code: str
    label: str
    transfer_mode: str
    lag_mode: str
    lag_basis: str
    redeem_confirm_include_day: bool
    trade_date_offset_days: int = 0
    redemption_fee_mode: str = FEE_MODE_NONE
    passive_weight_rule: str = WEIGHT_RULE_ROUND2
    end_date_mode: str = END_DATE_DISCLOSED_NAV
    weight_round_digits: int | None = 2


DELAY_VARIANTS = [
    DelayVariant(
        "gf_delay_default_nav_none",
        "广发调仓延迟-无转换-默认确认到账-基金净值日",
        "不模拟转换",
        "默认",
        "基金净值日",
        True,
    ),
    DelayVariant(
        "gf_delay_default_nav_same_company",
        "广发调仓延迟-同基金公司视作可转换-默认确认到账-基金净值日",
        "同基金公司",
        "默认",
        "基金净值日",
        True,
    ),
    DelayVariant(
        "gf_delay_default_nav_all_transfer",
        "广发调仓延迟-全部视作可转换上界-默认确认到账-基金净值日",
        "全部可转换",
        "默认",
        "基金净值日",
        True,
    ),
    DelayVariant(
        "gf_delay_t1r2_nav_none",
        "广发调仓延迟-无转换-普通确认T1到账T2-基金净值日",
        "不模拟转换",
        "普通确认T1到账T2",
        "基金净值日",
        True,
    ),
    DelayVariant(
        "gf_delay_t1r2_nav_same_company",
        "广发调仓延迟-同基金公司视作可转换-普通确认T1到账T2-基金净值日",
        "同基金公司",
        "普通确认T1到账T2",
        "基金净值日",
        True,
    ),
    DelayVariant(
        "gf_delay_t1r1_nav_none",
        "广发调仓延迟-无转换-普通确认T1到账T1-基金净值日",
        "不模拟转换",
        "普通确认T1到账T1",
        "基金净值日",
        True,
    ),
    DelayVariant(
        "gf_delay_default_calendar_none",
        "广发调仓延迟-无转换-默认确认到账-自然日",
        "不模拟转换",
        "默认",
        "自然日",
        True,
    ),
    DelayVariant(
        "gf_delay_default_calendar_same_company",
        "广发调仓延迟-同基金公司视作可转换-默认确认到账-自然日",
        "同基金公司",
        "默认",
        "自然日",
        True,
    ),
    DelayVariant(
        "gf_delay_default_nav_none_exclusive",
        "广发调仓延迟-无转换-默认确认到账-赎回确认日不含当日收益",
        "不模拟转换",
        "默认",
        "基金净值日",
        False,
    ),
    DelayVariant(
        "gf_delay_default_nav_same_company_exclusive",
        "广发调仓延迟-同基金公司视作可转换-赎回确认日不含当日收益",
        "同基金公司",
        "默认",
        "基金净值日",
        False,
    ),
    DelayVariant(
        "gf_delay_default_nav_none_minus1",
        "广发调仓延迟-T-1起算-无转换-默认确认到账-基金净值日",
        "不模拟转换",
        "默认",
        "基金净值日",
        True,
        trade_date_offset_days=-1,
    ),
    DelayVariant(
        "gf_delay_default_nav_same_company_minus1",
        "广发调仓延迟-T-1起算-同基金公司视作可转换-默认确认到账-基金净值日",
        "同基金公司",
        "默认",
        "基金净值日",
        True,
        trade_date_offset_days=-1,
    ),
    DelayVariant(
        "gf_delay_default_nav_all_transfer_minus1",
        "广发调仓延迟-T-1起算-全部视作可转换上界-默认确认到账-基金净值日",
        "全部可转换",
        "默认",
        "基金净值日",
        True,
        trade_date_offset_days=-1,
    ),
    DelayVariant(
        "gf_delay_default_nav_none_exclusive_minus1",
        "广发调仓延迟-T-1起算-无转换-赎回确认日不含当日收益",
        "不模拟转换",
        "默认",
        "基金净值日",
        False,
        trade_date_offset_days=-1,
    ),
    DelayVariant(
        "gf_daily_clearing_fee_none_base",
        "广发日终清算日初被动再平衡-无转换-估算赎回费-尾差分摊底仓",
        "不模拟转换",
        "默认",
        "基金净值日",
        True,
        redemption_fee_mode=FEE_MODE_ESTIMATED_REDEEM,
        passive_weight_rule=WEIGHT_RULE_ROUND2_RESIDUAL_BASE,
    ),
    DelayVariant(
        "gf_daily_clearing_fee_same_base",
        "广发日终清算日初被动再平衡-同基金公司转换-估算赎回费-尾差分摊底仓",
        "同基金公司",
        "默认",
        "基金净值日",
        True,
        redemption_fee_mode=FEE_MODE_ESTIMATED_REDEEM,
        passive_weight_rule=WEIGHT_RULE_ROUND2_RESIDUAL_BASE,
    ),
    DelayVariant(
        "gf_daily_clearing_fee_all_base",
        "广发日终清算日初被动再平衡-全部转换上界-估算赎回费-尾差分摊底仓",
        "全部可转换",
        "默认",
        "基金净值日",
        True,
        redemption_fee_mode=FEE_MODE_ESTIMATED_REDEEM,
        passive_weight_rule=WEIGHT_RULE_ROUND2_RESIDUAL_BASE,
    ),
    DelayVariant(
        "gf_daily_clearing_fee_none_largest",
        "广发日终清算日初被动再平衡-无转换-估算赎回费-尾差补最大基金",
        "不模拟转换",
        "默认",
        "基金净值日",
        True,
        redemption_fee_mode=FEE_MODE_ESTIMATED_REDEEM,
        passive_weight_rule=WEIGHT_RULE_ROUND2_RESIDUAL_LARGEST,
    ),
    DelayVariant(
        "gf_daily_clearing_fee_none_trunc_base",
        "广发日终清算日初被动再平衡-无转换-估算赎回费-截断尾差分摊底仓",
        "不模拟转换",
        "默认",
        "基金净值日",
        True,
        redemption_fee_mode=FEE_MODE_ESTIMATED_REDEEM,
        passive_weight_rule=WEIGHT_RULE_TRUNC2_RESIDUAL_BASE,
    ),
    DelayVariant(
        "gf_daily_clearing_fee_none_prev_eod",
        "广发日初初始化-上一日日终净值-无转换-估算赎回费-尾差分摊底仓",
        "不模拟转换",
        "默认",
        "基金净值日",
        True,
        redemption_fee_mode=FEE_MODE_ESTIMATED_REDEEM,
        passive_weight_rule=WEIGHT_RULE_ROUND2_RESIDUAL_BASE,
        end_date_mode=END_DATE_PREVIOUS_EOD,
    ),
]

BASELINE_VARIANT_CODES = [
    "total_fund_position",
    "total_fund_position_minus1",
    "amount_round2_reinvest_navdate",
    "amount_round2_order_nav_confirm_navdate",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="评估广发调仓清算/日初被动再平衡算法对当前持仓披露的贴合度。"
    )
    parser.add_argument("--channel-id", default=CHANNEL_ID)
    parser.add_argument("--advisor-name", default=ADVISOR_NAME)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    return parser.parse_args()


def decimal_or_zero(value: Any) -> Decimal:
    parsed = tune.decimal_or_none(value)
    return parsed if parsed is not None else Decimal("0")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def add_days(date_text: str | None, days: int) -> str | None:
    parsed = base.parse_date(date_text)
    if not parsed:
        return None
    return (parsed + timedelta(days=days)).strftime("%Y-%m-%d")


def max_date(*dates: str | None) -> str | None:
    usable = [date for date in dates if date]
    return max(usable) if usable else None


def quantile(values: list[float], p: float) -> float | None:
    vals = sorted(value for value in values if math.isfinite(value))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    idx = (len(vals) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - idx) + vals[hi] * (idx - lo)


@lru_cache(maxsize=100000)
def fund_profile_cached(db_path: str, fund_code: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    profile: dict[str, Any] = {
        "基金代码": fund_code,
        "基金名称": None,
        "基金公司": None,
        "基金类型": None,
        "是否货币基金": 0,
        "是否QDII": 0,
    }
    for table in ("基金信息", "基金净值概况"):
        row = conn.execute(
            f'SELECT * FROM "{table}" WHERE "基金代码" = ? LIMIT 1',
            [fund_code],
        ).fetchone()
        if not row:
            continue
        for key in ("基金名称", "基金公司", "基金类型"):
            if key in row.keys() and row[key] and not profile.get(key):
                profile[key] = str(row[key])
        if "是否货币基金" in row.keys() and row["是否货币基金"] is not None:
            profile["是否货币基金"] = int(row["是否货币基金"] or 0)
    row = conn.execute(
        """
        SELECT "基金名称", "基金公司", "基金类型", "是否货币基金"
        FROM "基金日度净值"
        WHERE "基金代码" = ?
        ORDER BY "交易日期" DESC
        LIMIT 1
        """,
        [fund_code],
    ).fetchone()
    conn.close()
    if row:
        for key in ("基金名称", "基金公司", "基金类型"):
            if row[key] and not profile.get(key):
                profile[key] = str(row[key])
        if row["是否货币基金"] is not None:
            profile["是否货币基金"] = max(int(profile["是否货币基金"] or 0), int(row["是否货币基金"] or 0))
    joined = f"{profile.get('基金名称') or ''} {profile.get('基金类型') or ''}".upper()
    profile["是否QDII"] = int("QDII" in joined)
    return profile


def fund_share_class(profile: dict[str, Any]) -> str | None:
    name = str(profile.get("基金名称") or "").upper()
    fund_type = str(profile.get("基金类型") or "").upper()
    # 优先识别名称末尾或括号内的份额类别，避免把指数名称里的字母误判为份额。
    for text in (name, fund_type):
        for pattern in (
            r"(?:^|[\s（(])([A-Z])(?:类)?(?:份额)?[）)]?$",
            r"([A-Z])(?:类)?(?:份额)?$",
        ):
            match = re.search(pattern, text)
            if match:
                return match.group(1)
    return None


def estimated_redeem_fee_rate(db_path: str, fund_code: str) -> Decimal:
    profile = fund_profile_cached(db_path, fund_code)
    if int(profile.get("是否货币基金") or 0) == 1:
        return Decimal("0")
    if fund_share_class(profile) != "A":
        return Decimal("0")
    text = f"{profile.get('基金名称') or ''} {profile.get('基金类型') or ''}"
    if any(token in text for token in ("债", "固收", "短债", "中短债")):
        return Decimal("0.001")
    if any(token in text for token in ("股票", "混合", "权益", "指数", "ETF", "QDII", "商品", "REIT")):
        return Decimal("0.005")
    return Decimal("0.001")


def apply_redeem_fee(
    db_path: str,
    fund_code: str,
    proceeds: Decimal,
    fee_mode: str,
) -> tuple[Decimal, Decimal]:
    if fee_mode != FEE_MODE_ESTIMATED_REDEEM or proceeds <= 0:
        return proceeds, Decimal("0")
    fee_rate = estimated_redeem_fee_rate(db_path, fund_code)
    if fee_rate <= 0:
        return proceeds, Decimal("0")
    fee = tune.round_half_up(proceeds * fee_rate, 2)
    return tune.round_half_up(max(proceeds - fee, Decimal("0")), 2), fee


def lags_for_fund(db_path: str, fund_code: str, lag_mode: str) -> tuple[int, int, int]:
    profile = fund_profile_cached(db_path, fund_code)
    is_qdii = int(profile.get("是否QDII") or 0) == 1
    is_money = int(profile.get("是否货币基金") or 0) == 1
    if lag_mode == "普通确认T1到账T1":
        return (2 if is_qdii else 1, 7 if is_qdii else 1, 2 if is_qdii else 1)
    if lag_mode == "普通确认T1到账T2":
        return (2 if is_qdii else 1, 7 if is_qdii else 2, 2 if is_qdii else 1)
    if is_qdii:
        return 2, 7, 2
    if is_money:
        return 1, 1, 1
    return 1, 2, 1


def shifted_effective_date(db_path: str, fund_code: str, anchor: str, days: int, lag_basis: str) -> str:
    if days <= 0:
        return anchor
    if lag_basis == "基金净值日":
        row = tune.fund_nav_row_after_offset_cached(db_path, fund_code, anchor, days)
        return row[0] or add_days(anchor, days) or anchor
    return add_days(anchor, days) or anchor


def previous_nav_date(db_path: str, fund_code: str, anchor: str) -> str:
    parsed = base.parse_date(anchor)
    if not parsed:
        return anchor
    before = (parsed - timedelta(days=1)).strftime("%Y-%m-%d")
    row = tune.fund_nav_row_on_or_before_cached(db_path, fund_code, before)
    return row[0] or before


@lru_cache(maxsize=250000)
def amount_round2_value_cached(
    db_path: str,
    fund_code: str,
    initial_asset_text: str,
    start_anchor: str,
    end_anchor: str,
) -> tuple[float | None, dict[str, Any]]:
    initial_asset = tune.round_half_up(decimal_or_zero(initial_asset_text), 2)
    if initial_asset <= 0:
        return 0.0, {"source": "零金额", "missing_nav": 0, "start_trade_date": None, "end_trade_date": None}
    start_row = tune.fund_nav_row_on_or_before_cached(db_path, fund_code, start_anchor)
    if not start_row[0]:
        start_row = tune.fund_nav_row_first_between_cached(db_path, fund_code, start_anchor, end_anchor)
    end_row = tune.fund_nav_row_on_or_before_cached(db_path, fund_code, end_anchor)
    if not start_row[0] or not end_row[0]:
        return None, {"source": "缺少基金净值", "missing_nav": 1, "start_trade_date": start_row[0], "end_trade_date": end_row[0]}
    if start_row[0] > end_row[0]:
        return float(initial_asset), {
            "source": "终点早于起点，按在途资产不计收益",
            "missing_nav": 0,
            "start_trade_date": start_row[0],
            "end_trade_date": end_row[0],
        }

    is_money = int(start_row[2] or end_row[2] or 0) == 1
    if is_money:
        factor = base.compute_fund_factor_cached(db_path, fund_code, start_row[0], end_row[0])
        if factor.total_return_factor is None:
            return None, {
                "source": "货币基金缺少收益因子",
                "missing_nav": 1,
                "start_trade_date": factor.start_trade_date,
                "end_trade_date": factor.end_trade_date,
            }
        return float(tune.round_half_up(initial_asset * Decimal(str(factor.total_return_factor)), 2)), {
            "source": "货币基金收益因子+资产2位",
            "missing_nav": 0,
            "start_trade_date": factor.start_trade_date,
            "end_trade_date": factor.end_trade_date,
            "dividend_event_count": factor.dividend_event_count,
        }

    start_unit = tune.decimal_or_none(start_row[1])
    end_unit = tune.decimal_or_none(end_row[1])
    if start_unit in (None, Decimal("0")) or end_unit is None:
        return None, {"source": "缺少单位净值", "missing_nav": 1, "start_trade_date": start_row[0], "end_trade_date": end_row[0]}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    dividend_rows = conn.execute(
        """
        SELECT COALESCE("除息日", "权益登记日") AS "分红日期", "每份分红"
        FROM "基金分红送配"
        WHERE "基金代码" = ?
          AND COALESCE("除息日", "权益登记日") > ?
          AND COALESCE("除息日", "权益登记日") <= ?
        ORDER BY COALESCE("除息日", "权益登记日")
        """,
        [fund_code, start_row[0], end_row[0]],
    ).fetchall()
    conn.close()

    shares = tune.round_half_up(initial_asset / start_unit, 2)
    dividend_count = 0
    for row in dividend_rows:
        dividend_date = base.norm_text(row["分红日期"])
        dividend_per_share = tune.decimal_or_none(base.parse_cash_dividend(row["每份分红"]))
        if not dividend_date or dividend_per_share is None or dividend_per_share <= 0:
            continue
        _, reinvest_nav = tune.unit_nav_on_or_before_cached(db_path, fund_code, dividend_date)
        if reinvest_nav in (None, Decimal("0")):
            continue
        dividend_asset = tune.round_half_up(shares * dividend_per_share, 2)
        added_shares = tune.round_half_up(dividend_asset / reinvest_nav, 2)
        shares = tune.round_half_up(shares + added_shares, 2)
        dividend_count += 1
    return float(tune.round_half_up(shares * end_unit, 2)), {
        "source": "单位净值+红利再投+份额资产2位",
        "missing_nav": 0,
        "start_trade_date": start_row[0],
        "end_trade_date": end_row[0],
        "dividend_event_count": dividend_count,
    }


def fund_end_date(current: base.CurrentHolding, fund_code: str) -> str:
    return current.fund_nav_dates.get(fund_code) or current.disclosure_date or current.holding_date


def fund_end_date_for_variant(db_path: str, current: base.CurrentHolding, fund_code: str, variant: DelayVariant) -> str:
    if variant.end_date_mode == END_DATE_PREVIOUS_EOD:
        return previous_nav_date(db_path, fund_code, current.holding_date)
    return fund_end_date(current, fund_code)


def allocate_residual_to_codes(
    adjusted: dict[str, float],
    projected: dict[str, float],
    preferred_codes: set[str],
) -> dict[str, float]:
    if not adjusted:
        return adjusted
    residual_cents = int(round((100.0 - sum(adjusted.values())) * 100))
    if residual_cents == 0:
        return adjusted
    candidates = [code for code in preferred_codes if code in adjusted]
    if not candidates:
        candidates = list(adjusted)
    candidates.sort(key=lambda code: (projected.get(code, 0.0), code), reverse=True)
    idx = 0
    step = 0.01 if residual_cents > 0 else -0.01
    for _ in range(abs(residual_cents)):
        code = candidates[idx % len(candidates)]
        if step < 0 and adjusted[code] <= 0:
            idx += 1
            continue
        adjusted[code] = tune.round_float_half_up(adjusted[code] + step, 2)
        idx += 1
    return adjusted


def apply_passive_weight_rule(
    projected: dict[str, float],
    base_codes: set[str],
    rule: str,
) -> dict[str, float]:
    if not projected:
        return {}
    if rule == WEIGHT_RULE_ROUND2_RESIDUAL_LARGEST:
        return tune.adjust_weight_residual_to_largest(projected, tune.WEIGHT_ADJUST_RESIDUAL_TO_LARGEST)
    if rule == WEIGHT_RULE_ROUND2_RESIDUAL_BASE:
        adjusted = {code: tune.round_float_half_up(value, 2) for code, value in projected.items()}
        return allocate_residual_to_codes(adjusted, projected, base_codes)
    if rule == WEIGHT_RULE_TRUNC2_RESIDUAL_BASE:
        adjusted = {code: tune.truncate_float(value, 2) for code, value in projected.items()}
        return allocate_residual_to_codes(adjusted, projected, base_codes)
    if rule == WEIGHT_RULE_ROUND2:
        return {code: tune.round_float_half_up(value, 2) for code, value in projected.items()}
    return projected


def grow_value(
    db_path: str,
    fund_code: str,
    initial_asset: Decimal,
    start_date: str,
    end_date: str,
) -> tuple[Decimal, dict[str, Any]]:
    if start_date > end_date:
        return tune.round_half_up(initial_asset, 2), {
            "source": "在途资产不计收益",
            "missing_nav": 0,
            "start_trade_date": start_date,
            "end_trade_date": end_date,
        }
    value, meta = amount_round2_value_cached(db_path, fund_code, str(tune.round_half_up(initial_asset, 2)), start_date, end_date)
    if value is None:
        return Decimal("0.00"), meta
    return tune.round_half_up(Decimal(str(value)), 2), meta


def is_transfer_eligible(db_path: str, from_code: str, to_code: str, transfer_mode: str) -> bool:
    if transfer_mode == "不模拟转换":
        return False
    if transfer_mode == "全部可转换":
        return True
    if transfer_mode == "同基金公司":
        from_profile = fund_profile_cached(db_path, from_code)
        to_profile = fund_profile_cached(db_path, to_code)
        from_company = base.norm_text(from_profile.get("基金公司"))
        to_company = base.norm_text(to_profile.get("基金公司"))
        return bool(from_company and to_company and from_company == to_company)
    return False


def build_rebalance_snapshot(event: dict[str, Any], details: list[dict[str, Any]]) -> RebalanceSnapshot:
    before_weights: dict[str, float] = defaultdict(float)
    after_weights: dict[str, float] = defaultdict(float)
    fund_names: dict[str, str | None] = {}
    missing_code_count = 0
    positive_after_row_count = 0
    before_sum = 0.0
    after_sum = 0.0
    for detail in details:
        before_weight = base.to_float(detail.get("调前权重_百分比")) or 0.0
        after_weight = base.to_float(detail.get("调后权重_百分比")) or 0.0
        if before_weight <= 0 and after_weight <= 0:
            continue
        code = base.norm_code(detail.get("基金代码"))
        if not code:
            missing_code_count += 1
            continue
        before_sum += max(before_weight, 0.0)
        after_sum += max(after_weight, 0.0)
        if before_weight > 0:
            before_weights[code] += before_weight
        if after_weight > 0:
            after_weights[code] += after_weight
            positive_after_row_count += 1
        fund_names.setdefault(code, base.norm_text(detail.get("基金名称")))

    return RebalanceSnapshot(
        event_id=str(event["调仓事件ID"]),
        strategy_id=str(event["统一策略ID"]),
        channel_id=str(event["渠道ID"]),
        source_strategy_id=str(event["渠道策略ID"]),
        rebalance_date=base.ymd(event.get("调仓日期")),
        previous_position_date=base.ymd(event.get("上次仓位日期")),
        position_date=base.ymd(event.get("本次仓位日期")),
        disclosure_date=base.ymd(event.get("披露日期")),
        event_seq=int(event["事件序号"]) if event.get("事件序号") is not None else None,
        event_time=base.norm_text(event.get("事件时间")),
        title=base.norm_text(event.get("调仓标题")),
        before_weights_pct=dict(before_weights),
        after_weights_pct=dict(after_weights),
        fund_names=fund_names,
        raw_row_count=len(details),
        positive_after_row_count=positive_after_row_count,
        missing_code_count=missing_code_count,
        before_weight_sum_pct=before_sum,
        after_weight_sum_pct=after_sum,
    )


def snapshot_sort_key(snapshot: RebalanceSnapshot) -> tuple[int, int, int, float, str, str]:
    close_to_100 = abs(snapshot.after_weight_sum_pct - 100.0) <= 1.0
    return (
        1 if snapshot.valid_for_projection else 0,
        1 if close_to_100 else 0,
        snapshot.positive_after_row_count,
        -abs(snapshot.after_weight_sum_pct - 100.0),
        str(snapshot.event_time or ""),
        snapshot.event_id,
    )


def load_latest_rebalance_snapshots(conn: sqlite3.Connection, strategy_ids: set[str]) -> dict[str, RebalanceSnapshot]:
    if not strategy_ids:
        return {}
    params = list(strategy_ids)
    events = base.fetch_dicts(
        conn,
        f"""
        SELECT "调仓事件ID", "统一策略ID", "渠道ID", "渠道策略ID", "调仓日期", "上次仓位日期",
               "本次仓位日期", "披露日期", "事件序号", "事件时间", "调仓标题"
        FROM "策略调仓事件"
        WHERE "统一策略ID" IN ({base.sql_placeholders(params)})
          AND "调仓日期" IS NOT NULL
          AND TRIM("调仓日期") <> ''
        ORDER BY "统一策略ID", "调仓日期", COALESCE("事件序号", 0), COALESCE("事件时间", ''), "调仓事件ID"
        """,
        params,
    )
    event_ids = [str(row["调仓事件ID"]) for row in events]
    details_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk_start in range(0, len(event_ids), 900):
        chunk = event_ids[chunk_start : chunk_start + 900]
        rows = base.fetch_dicts(
            conn,
            f"""
            SELECT "调仓事件ID", "基金代码", "基金名称", "调前权重_百分比", "调后权重_百分比"
            FROM "策略调仓明细"
            WHERE "调仓事件ID" IN ({base.sql_placeholders(chunk)})
            """,
            chunk,
        )
        for row in rows:
            details_by_event[str(row["调仓事件ID"])].append(row)

    by_strategy_date: dict[tuple[str, str], list[RebalanceSnapshot]] = defaultdict(list)
    for event in events:
        strategy_id = str(event["统一策略ID"])
        rebalance_date = base.ymd(event.get("调仓日期"))
        if not rebalance_date:
            continue
        snapshot = build_rebalance_snapshot(event, details_by_event.get(str(event["调仓事件ID"]), []))
        by_strategy_date[(strategy_id, rebalance_date)].append(snapshot)

    collapsed: dict[str, list[RebalanceSnapshot]] = defaultdict(list)
    for (strategy_id, _), snapshots in by_strategy_date.items():
        collapsed[strategy_id].append(max(snapshots, key=snapshot_sort_key))

    latest: dict[str, RebalanceSnapshot] = {}
    for strategy_id, snapshots in collapsed.items():
        ordered = sorted(
            snapshots,
            key=lambda item: (
                str(item.rebalance_date or ""),
                int(item.event_seq or -1),
                str(item.event_time or ""),
                item.event_id,
            ),
        )
        if ordered:
            latest[strategy_id] = ordered[-1]
    return latest


def sort_decrease_key(db_path: str, item: dict[str, Any], variant: DelayVariant) -> tuple[int, float, str]:
    _, refund_lag, _ = lags_for_fund(db_path, item["code"], variant.lag_mode)
    return refund_lag, -float(item["amount_pct"]), item["code"]


def sort_increase_key(db_path: str, item: dict[str, Any]) -> tuple[int, float, str]:
    profile = fund_profile_cached(db_path, item["code"])
    is_money = int(profile.get("是否货币基金") or 0) == 1
    return (1 if is_money else 0), -float(item["amount_pct"]), item["code"]


def apply_delay_variant(
    db_path: str,
    snapshot: RebalanceSnapshot,
    current: base.CurrentHolding,
    variant: DelayVariant,
) -> tuple[dict[str, float], dict[str, Any]]:
    base_trade_date = snapshot.rebalance_date or snapshot.position_date or snapshot.disclosure_date or current.holding_date
    trade_date = add_days(base_trade_date, variant.trade_date_offset_days) or base_trade_date
    values: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    missing_nav_count = 0
    source_counts: Counter[str] = Counter()
    instruction_counts: Counter[str] = Counter()
    total_redeem_fee = Decimal("0.00")
    retained_codes: set[str] = set()

    before = {code: Decimal(str(weight)) for code, weight in snapshot.before_weights_pct.items()}
    after = {code: Decimal(str(weight)) for code, weight in snapshot.after_weights_pct.items()}
    all_codes = sorted(set(before) | set(after))

    decreases: list[dict[str, Any]] = []
    increases: list[dict[str, Any]] = []
    for code in all_codes:
        before_weight = before.get(code, Decimal("0"))
        after_weight = after.get(code, Decimal("0"))
        retained_weight = min(before_weight, after_weight)
        if retained_weight > 0:
            retained_codes.add(code)
            initial_asset = tune.round_half_up(PRINCIPAL * retained_weight / Decimal("100"), 2)
            end_date = fund_end_date_for_variant(db_path, current, code, variant)
            value, meta = grow_value(db_path, code, initial_asset, trade_date, end_date)
            values[code] += value
            missing_nav_count += int(meta.get("missing_nav") or 0)
            source_counts[str(meta.get("source") or "保留仓位")] += 1
            instruction_counts["保留仓位"] += 1
        delta = after_weight - before_weight
        if delta > 0:
            increases.append({"code": code, "amount_pct": delta})
        elif delta < 0:
            decreases.append({"code": code, "amount_pct": -delta})

    decreases.sort(key=lambda item: sort_decrease_key(db_path, item, variant))
    increases.sort(key=lambda item: sort_increase_key(db_path, item))

    def consume_match(source: dict[str, Any], target: dict[str, Any], amount_pct: Decimal, is_transfer: bool) -> None:
        nonlocal missing_nav_count, total_redeem_fee
        source_code = source["code"]
        target_code = target["code"]
        source_asset = tune.round_half_up(PRINCIPAL * amount_pct / Decimal("100"), 2)
        redeem_confirm_lag, refund_lag, _ = lags_for_fund(db_path, source_code, variant.lag_mode)
        _, _, purchase_confirm_lag = lags_for_fund(db_path, target_code, variant.lag_mode)

        redeem_confirm_date = shifted_effective_date(db_path, source_code, trade_date, redeem_confirm_lag, variant.lag_basis)
        if not variant.redeem_confirm_include_day:
            redeem_return_end_date = previous_nav_date(db_path, source_code, redeem_confirm_date)
        else:
            redeem_return_end_date = redeem_confirm_date

        if is_transfer:
            target_confirm_date = shifted_effective_date(db_path, target_code, trade_date, purchase_confirm_lag, variant.lag_basis)
            target_start_date = max_date(redeem_confirm_date, target_confirm_date) or trade_date
            instruction_counts["转换"] += 1
        else:
            refund_order_lag = max(refund_lag - 2, 0)
            refund_order_date = shifted_effective_date(db_path, source_code, trade_date, refund_order_lag, variant.lag_basis)
            purchase_order_date = max_date(refund_order_date, redeem_confirm_date) or trade_date
            target_start_date = shifted_effective_date(db_path, target_code, purchase_order_date, purchase_confirm_lag, variant.lag_basis)
            instruction_counts["赎回转申购"] += 1

        proceeds, source_meta = grow_value(db_path, source_code, source_asset, trade_date, redeem_return_end_date)
        proceeds, redeem_fee = apply_redeem_fee(db_path, source_code, proceeds, variant.redemption_fee_mode)
        if redeem_fee > 0:
            total_redeem_fee += redeem_fee
            source_counts["A端估算赎回费"] += 1
        missing_nav_count += int(source_meta.get("missing_nav") or 0)
        source_counts[f"A端{source_meta.get('source') or '收益'}"] += 1

        target_end_date = fund_end_date_for_variant(db_path, current, target_code, variant)
        if target_start_date > target_end_date:
            final_value = proceeds
            source_counts["B端在途不计收益"] += 1
        else:
            final_value, target_meta = grow_value(db_path, target_code, proceeds, target_start_date, target_end_date)
            missing_nav_count += int(target_meta.get("missing_nav") or 0)
            source_counts[f"B端{target_meta.get('source') or '收益'}"] += 1
        values[target_code] += final_value

    # 先撮合可转换基金。没有 invest_transfer_rule 入库时，使用候选 transfer_mode 做可解释上界/下界测试。
    for source in decreases:
        if source["amount_pct"] <= 0:
            continue
        for target in increases:
            if target["amount_pct"] <= 0:
                continue
            if not is_transfer_eligible(db_path, source["code"], target["code"], variant.transfer_mode):
                continue
            amount_pct = min(source["amount_pct"], target["amount_pct"])
            consume_match(source, target, amount_pct, True)
            source["amount_pct"] -= amount_pct
            target["amount_pct"] -= amount_pct
            if source["amount_pct"] <= 0:
                break

    # 再按广发规则撮合赎回转申购：减持按到账短到长，增持按非货币优先、金额大到小。
    remaining_decreases = [item for item in decreases if item["amount_pct"] > 0]
    remaining_increases = [item for item in increases if item["amount_pct"] > 0]
    remaining_decreases.sort(key=lambda item: sort_decrease_key(db_path, item, variant))
    remaining_increases.sort(key=lambda item: sort_increase_key(db_path, item))
    for source in remaining_decreases:
        if source["amount_pct"] <= 0:
            continue
        for target in remaining_increases:
            if target["amount_pct"] <= 0:
                continue
            amount_pct = min(source["amount_pct"], target["amount_pct"])
            consume_match(source, target, amount_pct, False)
            source["amount_pct"] -= amount_pct
            target["amount_pct"] -= amount_pct
            if source["amount_pct"] <= 0:
                break

    # 增持大于减持的部分，按现金直接申购处理；确认前作为B基金在途资产不产生收益。
    for target in remaining_increases:
        if target["amount_pct"] <= 0:
            continue
        target_code = target["code"]
        _, _, purchase_confirm_lag = lags_for_fund(db_path, target_code, variant.lag_mode)
        target_start_date = shifted_effective_date(db_path, target_code, trade_date, purchase_confirm_lag, variant.lag_basis)
        target_end_date = fund_end_date_for_variant(db_path, current, target_code, variant)
        initial_asset = tune.round_half_up(PRINCIPAL * target["amount_pct"] / Decimal("100"), 2)
        if target_start_date > target_end_date:
            values[target_code] += initial_asset
            source_counts["现金申购在途不计收益"] += 1
        else:
            value, meta = grow_value(db_path, target_code, initial_asset, target_start_date, target_end_date)
            values[target_code] += value
            missing_nav_count += int(meta.get("missing_nav") or 0)
            source_counts[f"现金申购{meta.get('source') or '收益'}"] += 1
        instruction_counts["现金申购"] += 1

    # 减持大于增持的剩余部分视作现金留存；当前基金占比按基金资产内部归一，不进入分母。
    cash_left = sum((item["amount_pct"] for item in remaining_decreases if item["amount_pct"] > 0), Decimal("0"))
    if cash_left > 0:
        instruction_counts["赎回后现金留存"] += 1

    denominator = sum(values.values())
    projected = {
        code: (float(value / denominator * Decimal("100")) if denominator > 0 else 0.0)
        for code, value in values.items()
        if value > 0
    }
    if variant.weight_round_digits is not None:
        projected = apply_passive_weight_rule(projected, retained_codes, variant.passive_weight_rule)
    meta = {
        "trade_date": trade_date,
        "base_trade_date": base_trade_date,
        "trade_date_offset_days": variant.trade_date_offset_days,
        "missing_nav_count": missing_nav_count,
        "source_counts": dict(source_counts),
        "instruction_counts": dict(instruction_counts),
        "cash_left_pct": float(cash_left),
        "total_redeem_fee": float(total_redeem_fee),
        "redemption_fee_mode": variant.redemption_fee_mode,
        "passive_weight_rule": variant.passive_weight_rule,
        "end_date_mode": variant.end_date_mode,
        "projected_weight_sum_pct": round(sum(projected.values()), 8),
    }
    return projected, meta


def status_from_metrics(metrics: dict[str, Any], missing_nav_count: int) -> str:
    max_abs = metrics.get("max_abs_diff_pct")
    total_abs = metrics.get("total_abs_diff_pct")
    if max_abs is None or total_abs is None:
        return "不可比对"
    if metrics.get("current_only_count") or metrics.get("history_only_count"):
        return "基金集合不一致"
    if missing_nav_count > 0:
        return "缺净值需复核"
    if max_abs <= tune.PASS_MAX_ABS and total_abs <= tune.PASS_TOTAL_ABS:
        return "通过"
    if max_abs <= tune.MINOR_MAX_ABS and total_abs <= tune.MINOR_TOTAL_ABS:
        return "小额差异"
    return "需复核"


def score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    max_diffs = [float(row["最大绝对差_百分点"]) for row in rows if row.get("最大绝对差_百分点") is not None]
    total_diffs = [float(row["绝对差合计_百分点"]) for row in rows if row.get("绝对差合计_百分点") is not None]
    status_counts = Counter(row["算法状态"] for row in rows)
    return {
        "样本数": len(rows),
        "通过数": status_counts.get("通过", 0),
        "小额差异数": status_counts.get("小额差异", 0),
        "需复核数": status_counts.get("需复核", 0),
        "缺净值需复核数": status_counts.get("缺净值需复核", 0),
        "基金集合不一致数": status_counts.get("基金集合不一致", 0),
        "通过或小额差异数": status_counts.get("通过", 0) + status_counts.get("小额差异", 0),
        "通过或小额差异占比": round((status_counts.get("通过", 0) + status_counts.get("小额差异", 0)) / len(rows) * 100.0, 6) if rows else None,
        "最大差中位数": round(quantile(max_diffs, 0.5), 8) if max_diffs else None,
        "最大差P90": round(quantile(max_diffs, 0.9), 8) if max_diffs else None,
        "最大差均值": round(statistics.fmean(max_diffs), 8) if max_diffs else None,
        "差异合计中位数": round(quantile(total_diffs, 0.5), 8) if total_diffs else None,
        "差异合计均值": round(statistics.fmean(total_diffs), 8) if total_diffs else None,
    }


def main() -> None:
    args = parse_args()
    generated_at = datetime.now().astimezone()
    output_dir = (
        args.output_root
        / args.channel_id
        / generated_at.strftime("%Y-%m-%d")
        / generated_at.strftime("%Y%m%dT%H%M%S%z")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    strategies: dict[str, base.Strategy] = {}
    for row in base.fetch_dicts(
        conn,
        """
        SELECT "统一策略ID", "渠道ID", "渠道策略ID", "策略名称", "投顾机构", "策略类型", "投顾费率"
        FROM "策略信息"
        WHERE "渠道ID" = ? AND "投顾机构" = ?
        """,
        [args.channel_id, args.advisor_name],
    ):
        strategies[str(row["统一策略ID"])] = base.Strategy(
            strategy_id=str(row["统一策略ID"]),
            channel_id=str(row["渠道ID"]),
            source_strategy_id=str(row["渠道策略ID"]),
            strategy_name=base.norm_text(row.get("策略名称")),
            advisor_name=base.norm_text(row.get("投顾机构")) or args.advisor_name,
            strategy_type=base.norm_text(row.get("策略类型")),
            fee_rate_text=base.norm_text(row.get("投顾费率")),
            annual_fee_rate=base.parse_fee_rate(row.get("投顾费率")),
        )

    latest_base_snapshots = base.load_latest_snapshots(conn, set(strategies))
    latest_rebalance_snapshots = load_latest_rebalance_snapshots(conn, set(strategies))
    current_holdings = base.load_current_holdings(conn, set(strategies))
    conn.close()

    db_path_str = str(DB_PATH)
    baseline_variants = {variant.code: variant for variant in tune.VARIANTS if variant.code in BASELINE_VARIANT_CODES}
    strategy_rows: list[dict[str, Any]] = []
    comparable_strategy_count = 0
    calibration_strategy_count = 0

    for strategy_id, strategy in strategies.items():
        base_snapshot = latest_base_snapshots.get(strategy_id)
        rebalance_snapshot = latest_rebalance_snapshots.get(strategy_id)
        current = current_holdings.get(strategy_id)
        if (
            not base_snapshot
            or not base_snapshot.valid_for_projection
            or not rebalance_snapshot
            or not rebalance_snapshot.valid_for_projection
            or not current
            or not current.weights_pct
            or current.weight_sum_pct <= 0
        ):
            continue
        comparable_strategy_count += 1
        baseline_projected, _, baseline_meta = tune.apply_variant(
            db_path_str,
            strategy,
            base_snapshot,
            current,
            baseline_variants["total_fund_position"],
        )
        baseline_metrics = base.compare_weights(baseline_projected, current.weights_pct)
        is_calibration_sample = (
            not baseline_metrics.get("current_only_count")
            and not baseline_metrics.get("history_only_count")
            and (baseline_metrics.get("max_abs_diff_pct") is None or baseline_metrics["max_abs_diff_pct"] <= CALIBRATION_MAX_DIFF_CUTOFF)
        )
        if is_calibration_sample:
            calibration_strategy_count += 1

        for code in BASELINE_VARIANT_CODES:
            variant = baseline_variants.get(code)
            if not variant:
                continue
            projected, _, meta = tune.apply_variant(db_path_str, strategy, base_snapshot, current, variant)
            metrics = base.compare_weights(projected, current.weights_pct)
            strategy_rows.append(
                {
                    "统一策略ID": strategy_id,
                    "渠道策略ID": strategy.source_strategy_id,
                    "策略名称": strategy.strategy_name,
                    "投顾机构": strategy.advisor_name,
                    "是否校准样本": int(is_calibration_sample),
                    "算法类别": "既有算法",
                    "算法代码": variant.code,
                    "算法名称": variant.label,
                    "转换模式": "",
                    "确认到账模式": "",
                    "日期推进口径": "",
                    "调仓起算偏移_天": "",
                    "赎回费口径": "",
                    "被动再平衡尾差规则": "",
                    "终点净值口径": "",
                    "赎回确认日是否含当日收益": "",
                    "最新调仓日期": rebalance_snapshot.rebalance_date,
                    "调前权重和_百分比": base.round_or_none(rebalance_snapshot.before_weight_sum_pct, 8),
                    "调后权重和_百分比": base.round_or_none(rebalance_snapshot.after_weight_sum_pct, 8),
                    "当前持仓日期": current.holding_date,
                    "当前持仓披露日期": current.disclosure_date,
                    "当前基金数": len(current.weights_pct),
                    "推算基金数": len(projected),
                    "共同基金数": metrics.get("common_count"),
                    "当前有历史无基金数": metrics.get("current_only_count"),
                    "历史有当前无基金数": metrics.get("history_only_count"),
                    "最大绝对差_百分点": base.round_or_none(metrics.get("max_abs_diff_pct"), 8),
                    "平均绝对差_百分点": base.round_or_none(metrics.get("avg_abs_diff_pct"), 8),
                    "绝对差合计_百分点": base.round_or_none(metrics.get("total_abs_diff_pct"), 8),
                    "均方根差_百分点": base.round_or_none(metrics.get("rmse_pct"), 8),
                    "缺净值基金数": int(meta.get("missing_nav_count") or 0),
                    "估算赎回费": "",
                    "指令统计JSON": "",
                    "收益源统计JSON": json.dumps(meta.get("source_counts") or {}, ensure_ascii=False),
                    "算法状态": status_from_metrics(metrics, int(meta.get("missing_nav_count") or 0)),
                }
            )

        for variant in DELAY_VARIANTS:
            projected, meta = apply_delay_variant(db_path_str, rebalance_snapshot, current, variant)
            metrics = base.compare_weights(projected, current.weights_pct)
            strategy_rows.append(
                {
                    "统一策略ID": strategy_id,
                    "渠道策略ID": strategy.source_strategy_id,
                    "策略名称": strategy.strategy_name,
                    "投顾机构": strategy.advisor_name,
                    "是否校准样本": int(is_calibration_sample),
                    "算法类别": "广发调仓指令延迟算法",
                    "算法代码": variant.code,
                    "算法名称": variant.label,
                    "转换模式": variant.transfer_mode,
                    "确认到账模式": variant.lag_mode,
                    "日期推进口径": variant.lag_basis,
                    "调仓起算偏移_天": variant.trade_date_offset_days,
                    "赎回费口径": variant.redemption_fee_mode,
                    "被动再平衡尾差规则": variant.passive_weight_rule,
                    "终点净值口径": variant.end_date_mode,
                    "赎回确认日是否含当日收益": "是" if variant.redeem_confirm_include_day else "否",
                    "最新调仓日期": rebalance_snapshot.rebalance_date,
                    "调前权重和_百分比": base.round_or_none(rebalance_snapshot.before_weight_sum_pct, 8),
                    "调后权重和_百分比": base.round_or_none(rebalance_snapshot.after_weight_sum_pct, 8),
                    "当前持仓日期": current.holding_date,
                    "当前持仓披露日期": current.disclosure_date,
                    "当前基金数": len(current.weights_pct),
                    "推算基金数": len(projected),
                    "共同基金数": metrics.get("common_count"),
                    "当前有历史无基金数": metrics.get("current_only_count"),
                    "历史有当前无基金数": metrics.get("history_only_count"),
                    "最大绝对差_百分点": base.round_or_none(metrics.get("max_abs_diff_pct"), 8),
                    "平均绝对差_百分点": base.round_or_none(metrics.get("avg_abs_diff_pct"), 8),
                    "绝对差合计_百分点": base.round_or_none(metrics.get("total_abs_diff_pct"), 8),
                    "均方根差_百分点": base.round_or_none(metrics.get("rmse_pct"), 8),
                    "缺净值基金数": int(meta.get("missing_nav_count") or 0),
                    "估算赎回费": base.round_or_none(meta.get("total_redeem_fee"), 2),
                    "指令统计JSON": json.dumps(meta.get("instruction_counts") or {}, ensure_ascii=False),
                    "收益源统计JSON": json.dumps(meta.get("source_counts") or {}, ensure_ascii=False),
                    "算法状态": status_from_metrics(metrics, int(meta.get("missing_nav_count") or 0)),
                }
            )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in strategy_rows:
        if int(row["是否校准样本"]) == 1:
            grouped[row["算法代码"]].append(row)

    algorithm_rows: list[dict[str, Any]] = []
    variant_lookup = {variant.code: variant for variant in DELAY_VARIANTS}
    for code, rows in grouped.items():
        first = rows[0]
        algorithm_rows.append(
            {
                "投顾机构": args.advisor_name,
                "可比策略数": comparable_strategy_count,
                "校准样本数": calibration_strategy_count,
                "算法类别": first["算法类别"],
                "算法代码": code,
                "算法名称": first["算法名称"],
                "转换模式": first["转换模式"],
                "确认到账模式": first["确认到账模式"],
                "日期推进口径": first["日期推进口径"],
                "调仓起算偏移_天": first["调仓起算偏移_天"],
                "赎回费口径": first["赎回费口径"],
                "被动再平衡尾差规则": first["被动再平衡尾差规则"],
                "终点净值口径": first["终点净值口径"],
                "赎回确认日是否含当日收益": first["赎回确认日是否含当日收益"],
                **score_rows(rows),
            }
        )
    algorithm_rows.sort(
        key=lambda row: (
            row.get("最大差中位数") if row.get("最大差中位数") is not None else 9999.0,
            row.get("最大差P90") if row.get("最大差P90") is not None else 9999.0,
            row.get("差异合计中位数") if row.get("差异合计中位数") is not None else 9999.0,
        )
    )

    best = algorithm_rows[0] if algorithm_rows else None
    known = next((row for row in algorithm_rows if row["算法代码"] == "amount_round2_reinvest_navdate"), None)
    baseline = next((row for row in algorithm_rows if row["算法代码"] == "total_fund_position"), None)
    t_minus_1 = next((row for row in algorithm_rows if row["算法代码"] == "total_fund_position_minus1"), None)

    write_csv(output_dir / "gffunds_trade_delay_strategy_scores.csv", strategy_rows)
    write_csv(output_dir / "gffunds_trade_delay_algorithm_scores.csv", algorithm_rows)

    summary = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "channel_id": args.channel_id,
        "advisor": args.advisor_name,
        "strategy_total": len(strategies),
        "comparable_strategy_count": comparable_strategy_count,
        "calibration_strategy_count": calibration_strategy_count,
        "algorithm_count": len(algorithm_rows),
        "best_algorithm": best,
        "known_amount_round2_algorithm": known,
        "baseline_total_return_algorithm": baseline,
        "t_minus_1_algorithm": t_minus_1,
        "output_dir": str(output_dir),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 广发投顾调仓指令延迟算法评估",
        "",
        f"- 渠道ID：{args.channel_id}",
        f"- 投顾机构：{args.advisor_name}",
        f"- 天天策略总数：{len(strategies)}",
        f"- 有最新基金级持仓可比对策略：{comparable_strategy_count}",
        f"- 校准样本：{calibration_strategy_count}",
        f"- 候选算法数：{len(algorithm_rows)}",
        "",
        "## 结论排序",
        "",
        "| 排名 | 算法 | 类别 | 转换模式 | 确认到账 | 日期口径 | 赎回费 | 尾差规则 | 终点净值 | 起算偏移 | 最大差中位数 | P90 | 差异合计中位数 | 通过/小额占比 |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for idx, row in enumerate(algorithm_rows, start=1):
        lines.append(
            f"| {idx} | {row['算法名称']} | {row['算法类别']} | {row.get('转换模式') or ''} | "
            f"{row.get('确认到账模式') or ''} | {row.get('日期推进口径') or ''} | "
            f"{row.get('赎回费口径') or ''} | {row.get('被动再平衡尾差规则') or ''} | {row.get('终点净值口径') or ''} | "
            f"{row.get('调仓起算偏移_天') if row.get('调仓起算偏移_天') not in (None, '') else ''} | "
            f"{row.get('最大差中位数') if row.get('最大差中位数') is not None else ''} | "
            f"{row.get('最大差P90') if row.get('最大差P90') is not None else ''} | "
            f"{row.get('差异合计中位数') if row.get('差异合计中位数') is not None else ''} | "
            f"{row.get('通过或小额差异占比') if row.get('通过或小额差异占比') is not None else ''} |"
        )
    lines.extend(
        [
            "",
            "## 口径说明",
            "",
            "- 既有算法继续保留，用于和外部已知广发算法、T-1经验算法对照。",
            "- 广发调仓指令延迟算法使用调前/调后权重拆出保留、减持、增持金额；保留部分继续持有，减持转增持部分按赎回确认、到账、申购确认拆成指令。",
            "- 可转换规则表未在当前分析库中，因此报告用“不模拟转换”“同基金公司视作可转换”“全部可转换上界”三档测试，后续拿到 invest_transfer_rule 后可替换为精确规则。",
            "- invest_fundinfo 的 confirmpace/refundpace 未在当前分析库中，因此默认确认/到账天数按本地基金类型估算：普通基金确认T+1、到账T+2，货币到账T+1，QDII确认T+2、到账T+7。",
            "- 赎回确认但申购未确认阶段作为目标基金在途资产，不产生收益；最终权重按基金资产内部归一，并按2位展示口径输出。",
            "- 广发日终清算日初被动再平衡候选额外加入基金分红再投、估算赎回费、日初被动再平衡尾差处理；赎回费规则按A类份额估算，股票/权益类0.5%，固收/债券类0.1%，货币和C类为0。",
            "- “上一日日终”终点净值口径用于测试当前持仓是否按新交易日日初初始化展示，而不是按持仓日当晚清算展示。",
        ]
    )
    (output_dir / "gffunds_trade_delay_algorithm_report.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
