from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_ALGORITHM_VERSION = "standard_rebalance_asset_dual_nav_v10_all_channels_20260528"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "rebalance_quality_analysis"
DEFAULT_EXCLUDED_CHANNELS = ("qieman",)

FULL_WEIGHT_MIN_PCT = 99.0
FULL_WEIGHT_MAX_PCT = 101.0
WIN_LOSS_EPSILON_PCT = 0.05
CONTRIBUTION_LABEL_PCT = 0.20
SIGNIFICANT_LABEL_PCT = 1.00
ACTION_EPSILON_PCT = 0.0001


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild rebalance-quality facts from the current governed rebalance events and adjusted fund NAV."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--algorithm-version", default=DEFAULT_ALGORITHM_VERSION)
    parser.add_argument("--exclude-channel", action="append", default=[])
    parser.add_argument("--strategy-id", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def mean_or_none(values: Iterable[float]) -> float | None:
    rows = list(values)
    return sum(rows) / len(rows) if rows else None


def normalize_fund_code(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    digits = "".join(char for char in text if char.isdigit())
    if digits and len(digits) <= 6:
        return digits.zfill(6)
    return text


def action_label(before: float, after: float) -> str:
    change = after - before
    if before <= ACTION_EPSILON_PCT and after > ACTION_EPSILON_PCT:
        return "新买入"
    if before > ACTION_EPSILON_PCT and after <= ACTION_EPSILON_PCT:
        return "清仓卖出"
    if change > ACTION_EPSILON_PCT:
        return "加仓"
    if change < -ACTION_EPSILON_PCT:
        return "减仓"
    return "持平"


def event_result_label(level: str, excess: float | None) -> tuple[str, str]:
    if excess is None or level == "不可评估":
        return "不可评估", "不可评估"
    prefix = "全组合" if level == "全组合可评估" else "调仓子集"
    if excess > WIN_LOSS_EPSILON_PCT:
        outcome = "胜"
    elif excess < -WIN_LOSS_EPSILON_PCT:
        outcome = "负"
    else:
        outcome = "平"
    magnitude = abs(excess)
    if magnitude >= SIGNIFICANT_LABEL_PCT:
        suffix = "显著正贡献" if excess > 0 else "显著负贡献"
    elif magnitude >= CONTRIBUTION_LABEL_PCT:
        suffix = "正贡献" if excess > 0 else "负贡献"
    else:
        suffix = "中性"
    return outcome, f"{prefix}{suffix}"


def strategy_history_label(full_count: int, valid_count: int, win_rate: float | None, average: float | None) -> str:
    if valid_count <= 0 or average is None:
        return "无可评估调仓"
    if full_count <= 0:
        if average >= CONTRIBUTION_LABEL_PCT:
            return "仅子集口径可见正向调仓"
        if average <= -CONTRIBUTION_LABEL_PCT:
            return "仅子集口径可见负向调仓"
        return "仅子集口径可评估，结论中性"
    effective_win_rate = win_rate or 0.0
    if average >= CONTRIBUTION_LABEL_PCT and effective_win_rate >= 60.0:
        return "历史调仓质量较强"
    if average > 0 and effective_win_rate >= 50.0:
        return "历史调仓质量偏正"
    if average <= -CONTRIBUTION_LABEL_PCT and effective_win_rate <= 40.0:
        return "历史调仓质量偏弱"
    return "历史调仓质量中性"


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def require_source_tables(conn: sqlite3.Connection) -> None:
    required = {
        "策略信息",
        "策略调仓事件",
        "策略调仓明细",
        "策略模拟净值区间",
        "基金日度净值",
    }
    missing = sorted(required - table_names(conn))
    if missing:
        raise RuntimeError("required source tables missing: " + ", ".join(missing))


def create_quality_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS "调仓质量事件分析" (
            "调仓事件ID" TEXT PRIMARY KEY,
            "统一策略ID" TEXT NOT NULL,
            "策略名称" TEXT,
            "投顾机构" TEXT,
            "渠道ID" TEXT NOT NULL,
            "调仓日期" TEXT,
            "下次调仓日期" TEXT,
            "区间结束锚点日期" TEXT,
            "区间结束是否封闭" INTEGER NOT NULL DEFAULT 0,
            "评估层级" TEXT NOT NULL,
            "评估状态" TEXT NOT NULL,
            "评估说明" TEXT,
            "调仓明细行数" INTEGER NOT NULL DEFAULT 0,
            "已补码行数" INTEGER NOT NULL DEFAULT 0,
            "未补码行数" INTEGER NOT NULL DEFAULT 0,
            "有净值覆盖行数" INTEGER NOT NULL DEFAULT 0,
            "调前权重和_百分比" REAL,
            "调后权重和_百分比" REAL,
            "调前仓位收益率_百分比" REAL,
            "调后仓位收益率_百分比" REAL,
            "调仓超额_百分比" REAL,
            "胜负" TEXT,
            "结果评价" TEXT,
            "买入加仓收益率_百分比" REAL,
            "卖出减仓收益率_百分比" REAL,
            "方向性超额_百分比" REAL,
            "策略区间收益率_百分比" REAL,
            "最优贡献基金" TEXT,
            "最差贡献基金" TEXT,
            "调仓标题" TEXT,
            "调仓原因" TEXT
        );

        CREATE TABLE IF NOT EXISTS "调仓质量基金明细" (
            "调仓明细分析ID" TEXT PRIMARY KEY,
            "调仓事件ID" TEXT NOT NULL,
            "统一策略ID" TEXT NOT NULL,
            "策略名称" TEXT,
            "渠道ID" TEXT NOT NULL,
            "调仓日期" TEXT,
            "基金代码_原始" TEXT,
            "基金代码_分析" TEXT,
            "基金代码解析状态" TEXT NOT NULL,
            "基金名称" TEXT,
            "调仓动作_分析" TEXT,
            "调前权重_百分比" REAL,
            "调后权重_百分比" REAL,
            "基金区间收益率_百分比" REAL,
            "调前收益贡献_百分比" REAL,
            "调后收益贡献_百分比" REAL,
            "调仓贡献变化_百分比" REAL,
            "评估层级" TEXT NOT NULL,
            "基金收益起始日期" TEXT,
            "基金收益结束日期" TEXT
        );

        CREATE INDEX IF NOT EXISTS "idx_调仓质量事件分析_策略日期"
        ON "调仓质量事件分析"("统一策略ID", "调仓日期");

        CREATE INDEX IF NOT EXISTS "idx_调仓质量基金明细_事件"
        ON "调仓质量基金明细"("调仓事件ID");

        CREATE TABLE IF NOT EXISTS "调仓质量策略汇总" (
            "统一策略ID" TEXT PRIMARY KEY,
            "策略名称" TEXT,
            "投顾机构" TEXT,
            "渠道ID" TEXT NOT NULL,
            "历史调仓事件数" INTEGER NOT NULL DEFAULT 0,
            "有效调仓事件数" INTEGER NOT NULL DEFAULT 0,
            "全组合有效事件数" INTEGER NOT NULL DEFAULT 0,
            "调仓子集有效事件数" INTEGER NOT NULL DEFAULT 0,
            "不可评估事件数" INTEGER NOT NULL DEFAULT 0,
            "胜事件数" INTEGER NOT NULL DEFAULT 0,
            "负事件数" INTEGER NOT NULL DEFAULT 0,
            "平事件数" INTEGER NOT NULL DEFAULT 0,
            "胜率_有效事件_百分比" REAL,
            "胜率_全组合事件_百分比" REAL,
            "平均调仓超额_百分比" REAL,
            "中位数调仓超额_百分比" REAL,
            "累计调仓超额_百分比" REAL,
            "平均正超额_百分比" REAL,
            "平均负超额_百分比" REAL,
            "赔率" REAL,
            "最近一次调仓日期" TEXT,
            "最近一次调仓评价" TEXT,
            "完整性说明" TEXT,
            "历史评价" TEXT
        );

        CREATE TABLE IF NOT EXISTS "调仓质量完整性概览" (
            "对象类型" TEXT NOT NULL,
            "对象ID" TEXT NOT NULL,
            "渠道ID" TEXT,
            "策略名称" TEXT,
            "指标名称" TEXT NOT NULL,
            "指标值" REAL,
            "指标文本" TEXT,
            PRIMARY KEY ("对象类型", "对象ID", "指标名称")
        );

        CREATE TABLE IF NOT EXISTS "调仓质量构建状态" (
            "构建ID" TEXT PRIMARY KEY,
            "算法版本" TEXT NOT NULL,
            "生成时间" TEXT NOT NULL,
            "排除渠道JSON" TEXT NOT NULL,
            "源事件数" INTEGER NOT NULL,
            "质量事件数" INTEGER NOT NULL,
            "源最新调仓日期" TEXT,
            "质量最新调仓日期" TEXT,
            "基金净值最新日期" TEXT,
            "缺失事件数" INTEGER NOT NULL,
            "孤立事件数" INTEGER NOT NULL
        );
        '''
    )


def selected_segments_sql(excluded_channels: set[str], strategy_ids: list[str]) -> tuple[str, list[Any]]:
    filters = ['seg."算法版本"=?', 'seg."调仓事件ID" IS NOT NULL', 'TRIM(seg."调仓事件ID")<>\'\'']
    params: list[Any] = []
    if excluded_channels:
        placeholders = ",".join("?" for _ in excluded_channels)
        filters.append(f'seg."渠道ID" NOT IN ({placeholders})')
        params.extend(sorted(excluded_channels))
    if strategy_ids:
        placeholders = ",".join("?" for _ in strategy_ids)
        filters.append(f'seg."统一策略ID" IN ({placeholders})')
        params.extend(strategy_ids)
    return " AND ".join(filters), params


def load_segments(
    conn: sqlite3.Connection,
    algorithm_version: str,
    excluded_channels: set[str],
    strategy_ids: list[str],
) -> list[dict[str, Any]]:
    where_sql, filter_params = selected_segments_sql(excluded_channels, strategy_ids)
    rows = conn.execute(
        f'''
        SELECT
            seg."统一策略ID", seg."渠道ID", seg."渠道策略ID", seg."策略名称",
            s."投顾机构", seg."调仓事件ID", seg."调仓日期", seg."下一调仓日期",
            seg."区间结束日期", seg."区间结束类型", seg."区间是否有效",
            seg."是否纳入模拟", seg."质量等级", seg."问题说明", seg."修复说明",
            seg."明细行数", seg."区间收益率_百分比",
            e."调仓标题", e."调仓原因"
        FROM "策略模拟净值区间" seg
        LEFT JOIN "策略信息" s ON s."统一策略ID"=seg."统一策略ID"
        LEFT JOIN "策略调仓事件" e ON e."调仓事件ID"=seg."调仓事件ID"
        WHERE {where_sql}
        ORDER BY seg."统一策略ID", seg."调仓日期", seg."区间序号"
        ''',
        [algorithm_version, *filter_params],
    ).fetchall()
    return [dict(row) for row in rows]


def load_fund_name_mappings(conn: sqlite3.Connection) -> dict[str, str]:
    if "基金名称映射" not in table_names(conn):
        return {}
    return {
        clean_text(row[0]): normalize_fund_code(row[1])
        for row in conn.execute('SELECT "映射名称", "基金代码" FROM "基金名称映射"')
        if clean_text(row[0]) and normalize_fund_code(row[1])
    }


def load_event_details(conn: sqlite3.Connection, event_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    if not event_ids:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in conn.execute(
        '''
        SELECT "调仓明细ID", "调仓事件ID", "基金代码", "基金名称", "调前权重_百分比",
               "调后权重_百分比", "调仓动作", "基金代码匹配状态"
        FROM "策略调仓明细"
        ORDER BY "调仓事件ID", "调仓明细ID"
        '''
    ):
        event_id = clean_text(row[1])
        if event_id in event_ids:
            grouped[event_id].append(dict(row))
    return grouped


class FundNavLookup:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.cache: dict[tuple[str, str, str, bool], tuple[str | None, str | None, float | None]] = {}
        self.series_cache: dict[str, list[tuple[str, float]]] = {}
        self.date_cache: dict[str, list[str]] = {}

    def load_series(self, fund_code: str) -> list[tuple[str, float]]:
        cached = self.series_cache.get(fund_code)
        if cached is not None:
            return cached
        rows = self.conn.execute(
            '''
            SELECT "交易日期", "复权净值", "累计净值", "单位净值", "日收益率_百分比"
            FROM "基金日度净值"
            WHERE "基金代码"=?
            ORDER BY "交易日期"
            ''',
            (fund_code,),
        )
        series: list[tuple[str, float]] = []
        previous_value: float | None = None
        previous_proxy: float | None = None
        for row in rows:
            trade_date = clean_text(row["交易日期"])
            adjusted = to_float(row["复权净值"])
            proxy = to_float(row["累计净值"])
            if proxy is None:
                proxy = to_float(row["单位净值"])
            daily_pct = to_float(row["日收益率_百分比"])
            if adjusted is not None and adjusted > 0:
                value = adjusted
            elif previous_value is not None and daily_pct is not None and daily_pct > -100:
                value = previous_value * (1.0 + daily_pct / 100.0)
            elif previous_value is not None and proxy is not None and previous_proxy not in (None, 0):
                value = previous_value * proxy / previous_proxy
            elif proxy is not None and proxy > 0:
                value = proxy
            elif daily_pct is not None and daily_pct > -100:
                value = (previous_value or 1.0) * (1.0 + daily_pct / 100.0)
            else:
                value = None
            if value is not None and value > 0 and trade_date:
                previous_value = value
                series.append((trade_date, value))
            if proxy is not None and proxy > 0:
                previous_proxy = proxy
        self.series_cache[fund_code] = series
        self.date_cache[fund_code] = [row[0] for row in series]
        return series

    @staticmethod
    def value_at(
        series: list[tuple[str, float]], dates: list[str], target_date: str, inclusive: bool
    ) -> tuple[str, float] | None:
        index = bisect.bisect_right(dates, target_date) if inclusive else bisect.bisect_left(dates, target_date)
        if index <= 0:
            return None
        return series[index - 1]

    def interval_return(
        self,
        fund_code: str,
        start_date: str,
        end_date: str,
        end_closed: bool,
    ) -> tuple[str | None, str | None, float | None]:
        key = (fund_code, start_date, end_date, end_closed)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        series = self.load_series(fund_code)
        dates = self.date_cache.get(fund_code, [])
        start_row = self.value_at(series, dates, start_date, inclusive=True)
        end_row = self.value_at(series, dates, end_date, inclusive=not end_closed)
        start_nav_date, start_nav = start_row if start_row else (None, None)
        end_nav_date, end_nav = end_row if end_row else (None, None)
        interval_return = None
        if start_nav and end_nav and start_nav_date and end_nav_date and end_nav_date > start_nav_date:
            interval_return = (end_nav / start_nav - 1.0) * 100.0
        result = (start_nav_date, end_nav_date, interval_return)
        self.cache[key] = result
        return result


def weighted_action_return(rows: list[dict[str, Any]], positive: bool) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for row in rows:
        fund_return = to_float(row.get("基金区间收益率_百分比"))
        if fund_return is None:
            continue
        before = to_float(row.get("调前权重_百分比")) or 0.0
        after = to_float(row.get("调后权重_百分比")) or 0.0
        change = after - before
        weight = change if positive else -change
        if weight <= ACTION_EPSILON_PCT:
            continue
        numerator += weight * fund_return
        denominator += weight
    return numerator / denominator if denominator > 0 else None


def build_quality_payload(
    conn: sqlite3.Connection,
    algorithm_version: str,
    excluded_channels: set[str],
    strategy_ids: list[str] | None = None,
) -> dict[str, Any]:
    require_source_tables(conn)
    strategy_ids = strategy_ids or []
    segments = load_segments(conn, algorithm_version, excluded_channels, strategy_ids)
    if not segments:
        raise RuntimeError(f"no strategy simulation segments found for algorithm={algorithm_version}")
    event_ids = {clean_text(row["调仓事件ID"]) for row in segments}
    details_by_event = load_event_details(conn, event_ids)
    name_mappings = load_fund_name_mappings(conn)
    nav_lookup = FundNavLookup(conn)

    event_rows: list[tuple[Any, ...]] = []
    fund_rows: list[tuple[Any, ...]] = []
    event_records: list[dict[str, Any]] = []
    strategy_records: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for segment in segments:
        event_id = clean_text(segment["调仓事件ID"])
        strategy_id = clean_text(segment["统一策略ID"])
        start_date = clean_text(segment["调仓日期"])
        next_date = clean_text(segment["下一调仓日期"])
        end_date = next_date or clean_text(segment["区间结束日期"])
        end_closed = bool(next_date)
        details = details_by_event.get(event_id, [])
        analyzed: list[dict[str, Any]] = []
        pre_sum = 0.0
        post_sum = 0.0
        active_count = 0
        covered_count = 0
        filled_code_count = 0
        missing_code_count = 0

        for index, detail in enumerate(details, start=1):
            before = to_float(detail.get("调前权重_百分比")) or 0.0
            after = to_float(detail.get("调后权重_百分比")) or 0.0
            pre_sum += before
            post_sum += after
            is_active = before > ACTION_EPSILON_PCT or after > ACTION_EPSILON_PCT
            if is_active:
                active_count += 1
            original_code = normalize_fund_code(detail.get("基金代码"))
            mapped_code = name_mappings.get(clean_text(detail.get("基金名称")), "")
            analysis_code = original_code or mapped_code
            if original_code:
                code_status = "原始基金代码"
            elif mapped_code:
                code_status = "基金名称映射补码"
                if is_active:
                    filled_code_count += 1
            else:
                code_status = "未补码"
                if is_active:
                    missing_code_count += 1
            nav_start = nav_end = None
            fund_return = None
            if analysis_code and start_date and end_date:
                nav_start, nav_end, fund_return = nav_lookup.interval_return(
                    analysis_code, start_date, end_date, end_closed
                )
            if is_active and fund_return is not None:
                covered_count += 1
            pre_contribution = before * fund_return / 100.0 if fund_return is not None else None
            post_contribution = after * fund_return / 100.0 if fund_return is not None else None
            contribution_change = (
                post_contribution - pre_contribution
                if pre_contribution is not None and post_contribution is not None
                else None
            )
            analyzed.append(
                {
                    "analysis_id": f"{event_id}#{index:03d}",
                    "original_code": original_code or None,
                    "analysis_code": analysis_code or None,
                    "code_status": code_status,
                    "fund_name": clean_text(detail.get("基金名称")) or None,
                    "action": action_label(before, after),
                    "before": before,
                    "after": after,
                    "fund_return": fund_return,
                    "pre_contribution": pre_contribution,
                    "post_contribution": post_contribution,
                    "contribution_change": contribution_change,
                    "nav_start": nav_start,
                    "nav_end": nav_end,
                }
            )

        both_sides_positive = pre_sum > ACTION_EPSILON_PCT and post_sum > ACTION_EPSILON_PCT
        complete_coverage = active_count > 0 and covered_count == active_count and missing_code_count == 0
        full_weight = (
            FULL_WEIGHT_MIN_PCT <= pre_sum <= FULL_WEIGHT_MAX_PCT
            and FULL_WEIGHT_MIN_PCT <= post_sum <= FULL_WEIGHT_MAX_PCT
        )
        if complete_coverage and both_sides_positive and full_weight:
            level = "全组合可评估"
            status = "可评估"
            explanation = (
                "基金明细和净值区间覆盖完整；"
                f"调前/调后权重和={pre_sum:.2f}%/{post_sum:.2f}%"
            )
        elif complete_coverage and both_sides_positive:
            level = "调仓子集可评估"
            status = "可评估"
            explanation = (
                "基金明细和净值区间覆盖完整；仅能回放调仓子集，"
                f"调前/调后权重和={pre_sum:.2f}%/{post_sum:.2f}%；权重和未闭合到全组合"
            )
        else:
            level = "不可评估"
            status = "不可评估"
            reasons: list[str] = []
            if active_count <= 0:
                reasons.append("无有效调仓基金明细")
            if missing_code_count:
                reasons.append(f"{missing_code_count}/{active_count}条基金未补码")
            uncovered = max(0, active_count - covered_count)
            if uncovered:
                reasons.append(f"{uncovered}/{active_count}条基金缺少区间净值覆盖")
            if not both_sides_positive:
                reasons.append(f"调前/调后权重和={pre_sum:.2f}%/{post_sum:.2f}%")
            elif not full_weight:
                reasons.append(f"权重和未闭合，调前/调后={pre_sum:.2f}%/{post_sum:.2f}%")
            explanation = "；".join(dict.fromkeys(reasons)) or clean_text(segment.get("问题说明")) or "不可评估"

        pre_return = (
            sum(to_float(item.get("pre_contribution")) or 0.0 for item in analyzed)
            if status == "可评估"
            else None
        )
        post_return = (
            sum(to_float(item.get("post_contribution")) or 0.0 for item in analyzed)
            if status == "可评估"
            else None
        )
        excess = post_return - pre_return if pre_return is not None and post_return is not None else None
        outcome, result_label = event_result_label(level, excess)
        buy_return = weighted_action_return(analyzed, positive=True) if status == "可评估" else None
        sell_return = weighted_action_return(analyzed, positive=False) if status == "可评估" else None
        directional_excess = (
            buy_return - sell_return if buy_return is not None and sell_return is not None else None
        )
        contribution_rows = [item for item in analyzed if item.get("contribution_change") is not None]
        best = max(contribution_rows, key=lambda item: float(item["contribution_change"])) if contribution_rows else None
        worst = min(contribution_rows, key=lambda item: float(item["contribution_change"])) if contribution_rows else None

        event_record = {
            "event_id": event_id,
            "strategy_id": strategy_id,
            "strategy_name": clean_text(segment.get("策略名称")),
            "advisor": clean_text(segment.get("投顾机构")),
            "channel_id": clean_text(segment.get("渠道ID")),
            "rebalance_date": start_date,
            "next_date": next_date or None,
            "end_date": end_date or None,
            "end_closed": 1 if end_closed else 0,
            "level": level,
            "status": status,
            "explanation": explanation,
            "detail_count": len(details),
            "filled_code_count": filled_code_count,
            "missing_code_count": missing_code_count,
            "covered_count": covered_count,
            "pre_sum": pre_sum,
            "post_sum": post_sum,
            "pre_return": pre_return,
            "post_return": post_return,
            "excess": excess,
            "outcome": outcome,
            "result_label": result_label,
            "buy_return": buy_return,
            "sell_return": sell_return,
            "directional_excess": directional_excess,
            "strategy_return": to_float(segment.get("区间收益率_百分比")),
            "best_fund": best.get("fund_name") if best else None,
            "worst_fund": worst.get("fund_name") if worst else None,
            "title": clean_text(segment.get("调仓标题")) or f"{clean_text(segment.get('策略名称'))} 调仓",
            "reason": clean_text(segment.get("调仓原因")) or None,
        }
        event_records.append(event_record)
        strategy_records[strategy_id].append(event_record)
        event_rows.append(
            (
                event_id,
                strategy_id,
                event_record["strategy_name"],
                event_record["advisor"],
                event_record["channel_id"],
                start_date,
                event_record["next_date"],
                event_record["end_date"],
                event_record["end_closed"],
                level,
                status,
                explanation,
                len(details),
                filled_code_count,
                missing_code_count,
                covered_count,
                round_or_none(pre_sum),
                round_or_none(post_sum),
                round_or_none(pre_return),
                round_or_none(post_return),
                round_or_none(excess),
                outcome,
                result_label,
                round_or_none(buy_return),
                round_or_none(sell_return),
                round_or_none(directional_excess),
                round_or_none(event_record["strategy_return"]),
                event_record["best_fund"],
                event_record["worst_fund"],
                event_record["title"],
                event_record["reason"],
            )
        )
        for item in analyzed:
            fund_rows.append(
                (
                    item["analysis_id"],
                    event_id,
                    strategy_id,
                    event_record["strategy_name"],
                    event_record["channel_id"],
                    start_date,
                    item["original_code"],
                    item["analysis_code"],
                    item["code_status"],
                    item["fund_name"],
                    item["action"],
                    round_or_none(item["before"]),
                    round_or_none(item["after"]),
                    round_or_none(item["fund_return"]),
                    round_or_none(item["pre_contribution"]),
                    round_or_none(item["post_contribution"]),
                    round_or_none(item["contribution_change"]),
                    level,
                    item["nav_start"],
                    item["nav_end"],
                )
            )

    strategy_rows: list[tuple[Any, ...]] = []
    overview_rows: list[tuple[Any, ...]] = []
    for strategy_id, records in strategy_records.items():
        first = records[0]
        valid = [row for row in records if row["status"] == "可评估" and row["excess"] is not None]
        full = [row for row in valid if row["level"] == "全组合可评估"]
        subset = [row for row in valid if row["level"] == "调仓子集可评估"]
        outcomes = Counter(row["outcome"] for row in valid)
        excess_values = [float(row["excess"]) for row in valid]
        outcomes_full = Counter(row["outcome"] for row in full)
        full_win_rate = outcomes_full["胜"] / len(full) * 100.0 if full else None
        win_rate = outcomes["胜"] / len(valid) * 100.0 if valid else None
        average = mean_or_none(excess_values)
        positives = [value for value in excess_values if value > 0]
        negatives = [value for value in excess_values if value < 0]
        average_positive = mean_or_none(positives)
        average_negative = mean_or_none(negatives)
        odds = (
            average_positive / abs(average_negative)
            if average_positive is not None and average_negative not in (None, 0)
            else None
        )
        latest = max(records, key=lambda row: (row["rebalance_date"], row["event_id"]))
        completeness = (
            f"{len(full)}/{len(records)}次可做全组合回放，"
            f"{len(subset)}次仅可回放调仓子集，{len(records) - len(valid)}次暂不可评估"
        )
        history_label = strategy_history_label(len(full), len(valid), win_rate, average)
        strategy_rows.append(
            (
                strategy_id,
                first["strategy_name"],
                first["advisor"],
                first["channel_id"],
                len(records),
                len(valid),
                len(full),
                len(subset),
                len(records) - len(valid),
                outcomes["胜"],
                outcomes["负"],
                outcomes["平"],
                round_or_none(win_rate),
                round_or_none(full_win_rate),
                round_or_none(average),
                round_or_none(statistics.median(excess_values) if excess_values else None),
                round_or_none(sum(excess_values) if excess_values else None),
                round_or_none(average_positive),
                round_or_none(average_negative),
                round_or_none(odds),
                latest["rebalance_date"],
                latest["result_label"],
                completeness,
                history_label,
            )
        )
        overview_values = [
            ("历史调仓事件数", float(len(records)), None),
            ("有效调仓事件数", float(len(valid)), None),
            ("全组合有效事件数", float(len(full)), None),
            ("调仓子集有效事件数", float(len(subset)), None),
            ("不可评估事件数", float(len(records) - len(valid)), None),
            ("历史评价", None, history_label),
        ]
        overview_rows.extend(
            ("策略", strategy_id, first["channel_id"], first["strategy_name"], metric, value, text)
            for metric, value, text in overview_values
        )

    channel_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in event_records:
        channel_records[record["channel_id"]].append(record)
    for channel_id, records in channel_records.items():
        metrics = [
            ("调仓事件数", len(records)),
            ("全组合可评估事件数", sum(row["level"] == "全组合可评估" for row in records)),
            ("调仓子集可评估事件数", sum(row["level"] == "调仓子集可评估" for row in records)),
            ("不可评估事件数", sum(row["status"] != "可评估" for row in records)),
            ("已补码明细行数", sum(int(row["filled_code_count"]) for row in records)),
            ("未补码明细行数", sum(int(row["missing_code_count"]) for row in records)),
        ]
        overview_rows.extend(
            ("渠道", channel_id, channel_id, None, metric, float(value), None)
            for metric, value in metrics
        )

    source_latest = max((clean_text(row["调仓日期"]) for row in segments), default=None)
    quality_latest = max((clean_text(row["rebalance_date"]) for row in event_records), default=None)
    fund_nav_latest = conn.execute('SELECT MAX("交易日期") FROM "基金日度净值"').fetchone()[0]
    event_id_set = {row["event_id"] for row in event_records}
    segment_id_set = {clean_text(row["调仓事件ID"]) for row in segments}
    summary = {
        "algorithmVersion": algorithm_version,
        "excludedChannels": sorted(excluded_channels),
        "strategyFilter": strategy_ids,
        "sourceEventCount": len(segments),
        "qualityEventCount": len(event_rows),
        "qualityFundRowCount": len(fund_rows),
        "qualityStrategyCount": len(strategy_rows),
        "sourceLatestRebalanceDate": source_latest,
        "qualityLatestRebalanceDate": quality_latest,
        "fundNavLatestDate": fund_nav_latest,
        "missingEventCount": len(segment_id_set - event_id_set),
        "orphanedEventCount": len(event_id_set - segment_id_set),
        "evaluationCounts": dict(Counter(record["level"] for record in event_records)),
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return {
        "eventRows": event_rows,
        "fundRows": fund_rows,
        "strategyRows": strategy_rows,
        "overviewRows": overview_rows,
        "summary": summary,
        "eventRecords": event_records,
    }


def replace_quality_tables(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    create_quality_tables(conn)
    for table in (
        "调仓质量基金明细",
        "调仓质量事件分析",
        "调仓质量策略汇总",
        "调仓质量完整性概览",
        "调仓质量构建状态",
    ):
        conn.execute(f'DELETE FROM "{table}"')
    conn.executemany(
        '''
        INSERT INTO "调仓质量事件分析" VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        ''',
        payload["eventRows"],
    )
    conn.executemany(
        '''
        INSERT INTO "调仓质量基金明细" VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        ''',
        payload["fundRows"],
    )
    conn.executemany(
        '''
        INSERT INTO "调仓质量策略汇总" VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        ''',
        payload["strategyRows"],
    )
    conn.executemany(
        'INSERT INTO "调仓质量完整性概览" VALUES (?,?,?,?,?,?,?)',
        payload["overviewRows"],
    )
    conn.execute(
        'INSERT INTO "调仓质量构建状态" VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (
            "latest",
            summary["algorithmVersion"],
            summary["generatedAt"],
            json.dumps(summary["excludedChannels"], ensure_ascii=False),
            summary["sourceEventCount"],
            summary["qualityEventCount"],
            summary["sourceLatestRebalanceDate"],
            summary["qualityLatestRebalanceDate"],
            summary["fundNavLatestDate"],
            summary["missingEventCount"],
            summary["orphanedEventCount"],
        ),
    )
    validations = {
        "eventCount": conn.execute('SELECT COUNT(*) FROM "调仓质量事件分析"').fetchone()[0],
        "fundRowCount": conn.execute('SELECT COUNT(*) FROM "调仓质量基金明细"').fetchone()[0],
        "strategyCount": conn.execute('SELECT COUNT(*) FROM "调仓质量策略汇总"').fetchone()[0],
        "exactEventMatches": conn.execute(
            '''
            SELECT COUNT(*)
            FROM "调仓质量事件分析" q
            JOIN "策略调仓事件" e ON e."调仓事件ID"=q."调仓事件ID"
            '''
        ).fetchone()[0],
        "fundParentMatches": conn.execute(
            '''
            SELECT COUNT(*)
            FROM "调仓质量基金明细" f
            JOIN "调仓质量事件分析" q ON q."调仓事件ID"=f."调仓事件ID"
            '''
        ).fetchone()[0],
    }
    if validations["eventCount"] != summary["qualityEventCount"]:
        raise RuntimeError(f"quality event row count mismatch: {validations}")
    if validations["fundRowCount"] != summary["qualityFundRowCount"]:
        raise RuntimeError(f"quality fund row count mismatch: {validations}")
    if validations["strategyCount"] != summary["qualityStrategyCount"]:
        raise RuntimeError(f"quality strategy row count mismatch: {validations}")
    if validations["exactEventMatches"] != summary["qualityEventCount"]:
        raise RuntimeError(f"quality event referential integrity mismatch: {validations}")
    if validations["fundParentMatches"] != summary["qualityFundRowCount"]:
        raise RuntimeError(f"quality fund parent integrity mismatch: {validations}")
    summary["databaseValidation"] = validations


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{datetime.now().strftime('%Y%m%d%H%M%S%f')}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def main() -> None:
    args = parse_args()
    excluded_channels = set(DEFAULT_EXCLUDED_CHANNELS)
    excluded_channels.update(clean_text(value) for value in args.exclude_channel if clean_text(value))
    db_path = args.db_path.resolve()
    if not db_path.is_file():
        raise SystemExit(f"database missing: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode={'ro' if args.dry_run else 'rw'}"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=120000")
        if args.dry_run:
            conn.execute("PRAGMA query_only=ON")
        payload = build_quality_payload(
            conn,
            args.algorithm_version,
            excluded_channels,
            [clean_text(value) for value in args.strategy_id if clean_text(value)],
        )
        if not args.dry_run:
            if args.strategy_id:
                raise SystemExit("--strategy-id is diagnostic-only; omit it for the atomic full refresh")
            conn.execute("BEGIN IMMEDIATE")
            try:
                replace_quality_tables(conn, payload)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    summary = dict(payload["summary"])
    summary["dryRun"] = bool(args.dry_run)
    summary["databasePath"] = str(db_path)
    output_json = args.output_json
    if output_json is None:
        run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
        output_json = DEFAULT_OUTPUT_ROOT / datetime.now().strftime("%Y-%m-%d") / run_id / "summary.json"
    write_json_atomic(output_json.resolve(), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
