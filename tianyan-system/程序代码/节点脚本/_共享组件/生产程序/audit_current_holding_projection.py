from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from rebalance_snapshot_repairs import repair_rebalance_details


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "current_holding_projection_audit"

WEIGHT_TARGET = 100.0
WEIGHT_CLOSE_TOLERANCE = 1.0
MATCH_PASS_MAX_ABS = 0.15
MATCH_PASS_TOTAL_ABS = 0.60
MATCH_MINOR_MAX_ABS = 0.50
MATCH_MINOR_TOTAL_ABS = 1.50
SIGNIFICANT_FUND_WEIGHT = 0.20
DIVIDEND_IMPACT_THRESHOLD = 0.30
FEE_IMPACT_THRESHOLD = 0.05
RETURN_SOURCE_TOLERANCE_PCT = 0.01


TABLE_STRATEGY_AUDIT = "最新持仓推算稽核策略汇总"
TABLE_FUND_AUDIT = "最新持仓推算稽核基金明细"
TABLE_INFERRED_HOLDING = "策略当前持仓推算补齐"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用最后一次调仓后的基金权重和基金净值涨跌推算最新持仓，并与披露持仓逐策略稽核。"
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--strategy-id", action="append", default=[], help="可选：只稽核指定统一策略ID。")
    parser.add_argument("--channel-id", action="append", default=[], help="可选：只稽核指定渠道ID。")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="将稽核结果写入分析库的中文结果表。",
    )
    parser.add_argument(
        "--project-stale-current-channel",
        action="append",
        default=None,
        help="对已有当前持仓但持仓日早于最新基金净值日的渠道，写入最新净值日推算持仓。默认只处理 gffunds；可传 all。",
    )
    parser.add_argument(
        "--no-project-stale-current",
        action="store_true",
        help="关闭已有当前持仓的最新净值日滚动推算。",
    )
    return parser.parse_args()


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
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def parse_date(value: str | None) -> datetime | None:
    text = norm_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def ymd(value: str | None) -> str | None:
    parsed = parse_date(value)
    return parsed.strftime("%Y-%m-%d") if parsed else None


def days_between(start: str | None, end: str | None) -> int | None:
    start_dt = parse_date(start)
    end_dt = parse_date(end)
    if not start_dt or not end_dt:
        return None
    return (end_dt.date() - start_dt.date()).days


def parse_cash_dividend(value: Any) -> float | None:
    text = norm_text(value)
    if not text:
        return None
    if "拆分" in text or "折算" in text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text.replace(",", ""))
    if not match:
        return None
    amount = float(match.group(1))
    if re.search(r"每\s*10\s*份|10\s*份", text):
        amount /= 10.0
    return amount


def parse_fee_rate(value: Any) -> float | None:
    text = norm_text(value)
    if not text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text.replace(",", ""))
    if not match:
        return None
    number = float(match.group(1))
    lowered = text.lower()
    if "bp" in lowered or "基点" in text:
        return max(number / 10000.0, 0.0)
    if "%" in text:
        return max(number / 100.0, 0.0)
    if number >= 1.0:
        return max(number / 100.0, 0.0)
    return max(number / 100.0, 0.0)


def sql_placeholders(values: list[Any] | tuple[Any, ...]) -> str:
    return ",".join("?" for _ in values)


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


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params)]


@dataclass
class Strategy:
    strategy_id: str
    channel_id: str
    source_strategy_id: str
    strategy_name: str | None
    advisor_name: str | None
    strategy_type: str | None
    fee_rate_text: str | None
    annual_fee_rate: float | None


@dataclass
class Snapshot:
    event_id: str
    strategy_id: str
    channel_id: str
    source_strategy_id: str
    rebalance_date: str | None
    position_date: str | None
    disclosure_date: str | None
    event_seq: int | None
    event_time: str | None
    title: str | None
    weights_pct: dict[str, float]
    fund_names: dict[str, str | None]
    raw_row_count: int
    positive_row_count: int
    missing_code_count: int
    duplicate_fund_count: int
    after_weight_sum_pct: float
    is_liquidation: bool = False

    @property
    def valid_for_projection(self) -> bool:
        if self.is_liquidation:
            return self.missing_code_count == 0
        return bool(self.weights_pct) and self.missing_code_count == 0 and self.positive_row_count > 0


@dataclass
class CurrentHolding:
    strategy_id: str
    holding_date: str
    disclosure_date: str | None
    weights_pct: dict[str, float]
    fund_names: dict[str, str | None]
    fund_nav_dates: dict[str, str | None]
    raw_row_count: int
    positive_row_count: int
    missing_code_count: int
    weight_sum_pct: float


@dataclass
class FundFactor:
    fund_code: str
    start_anchor: str
    end_anchor: str
    start_trade_date: str | None
    end_trade_date: str | None
    total_return_factor: float | None
    unit_only_factor: float | None
    accum_factor: float | None
    daily_pct_factor: float | None
    nav_row_count: int
    missing_return_points: int
    dividend_event_count: int
    dividend_amount_sum: float
    source_counts: dict[str, int]
    status: str


def ensure_result_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS "{TABLE_STRATEGY_AUDIT}" (
            "统一策略ID" TEXT PRIMARY KEY,
            "渠道ID" TEXT NOT NULL,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "投顾机构" TEXT,
            "策略类型" TEXT,
            "投顾费率" TEXT,
            "年化投顾费率_百分比" REAL,
            "最新调仓事件ID" TEXT,
            "最新调仓日期" TEXT,
            "最新调仓仓位日期" TEXT,
            "最新调仓披露日期" TEXT,
            "当前持仓日期" TEXT,
            "当前持仓披露日期" TEXT,
            "稽核状态" TEXT NOT NULL,
            "稽核结论" TEXT,
            "归因分类" TEXT,
            "差异归因" TEXT,
            "是否已有当前持仓" INTEGER NOT NULL,
            "是否可推算补齐" INTEGER NOT NULL,
            "推算持仓日期" TEXT,
            "最佳推算口径" TEXT,
            "最佳起算日期" TEXT,
            "持有天数" INTEGER,
            "当前基金数" INTEGER,
            "推算基金数" INTEGER,
            "共同基金数" INTEGER,
            "当前有历史无基金数" INTEGER,
            "历史有当前无基金数" INTEGER,
            "当前权重和_百分比" REAL,
            "调后权重和_百分比" REAL,
            "推算权重和_百分比" REAL,
            "现金权重_调后_百分比" REAL,
            "最大绝对差_百分点" REAL,
            "平均绝对差_百分点" REAL,
            "绝对差合计_百分点" REAL,
            "均方根差_百分点" REAL,
            "费前最大绝对差_百分点" REAL,
            "不含分红最大绝对差_百分点" REAL,
            "现金扣费最大绝对差_百分点" REAL,
            "分红影响_最大改善_百分点" REAL,
            "投顾费影响_最大改善_百分点" REAL,
            "缺净值基金数" INTEGER,
            "分红事件数" INTEGER,
            "收益源统计JSON" TEXT,
            "生成时间" TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS "{TABLE_FUND_AUDIT}" (
            "明细ID" TEXT PRIMARY KEY,
            "统一策略ID" TEXT NOT NULL,
            "渠道ID" TEXT NOT NULL,
            "策略名称" TEXT,
            "基金代码" TEXT NOT NULL,
            "基金名称" TEXT,
            "最新调仓事件ID" TEXT,
            "起算日期" TEXT,
            "推算日期" TEXT,
            "当前持仓日期" TEXT,
            "调后权重_百分比" REAL,
            "推算权重_百分比" REAL,
            "当前权重_百分比" REAL,
            "差异_百分点" REAL,
            "绝对差_百分点" REAL,
            "收益因子_复权" REAL,
            "收益因子_不含分红" REAL,
            "收益因子_累计净值" REAL,
            "净值起始日" TEXT,
            "净值结束日" TEXT,
            "净值行数" INTEGER,
            "分红事件数" INTEGER,
            "分红金额合计" REAL,
            "净值状态" TEXT,
            "是否补齐推算" INTEGER NOT NULL,
            "生成时间" TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS "{TABLE_INFERRED_HOLDING}" (
            "统一策略ID" TEXT NOT NULL,
            "渠道ID" TEXT NOT NULL,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "推算持仓日期" TEXT NOT NULL,
            "基金代码" TEXT NOT NULL,
            "基金名称" TEXT,
            "推算基金权重_百分比" REAL NOT NULL,
            "最后调仓后权重_百分比" REAL,
            "收益因子_复权" REAL,
            "推算来源" TEXT NOT NULL,
            "置信度" TEXT NOT NULL,
            "稽核结论" TEXT,
            "最新调仓事件ID" TEXT,
            "最新调仓日期" TEXT,
            "生成时间" TEXT NOT NULL,
            PRIMARY KEY ("统一策略ID", "推算持仓日期", "基金代码")
        );
        """
    )


def clear_result_tables(
    conn: sqlite3.Connection,
    channel_ids: list[str] | None = None,
    strategy_ids: list[str] | None = None,
) -> None:
    for table_name in (TABLE_STRATEGY_AUDIT, TABLE_FUND_AUDIT, TABLE_INFERRED_HOLDING):
        if strategy_ids:
            conn.execute(
                f'DELETE FROM "{table_name}" WHERE "统一策略ID" IN ({sql_placeholders(strategy_ids)})',
                strategy_ids,
            )
        elif channel_ids:
            conn.execute(
                f'DELETE FROM "{table_name}" WHERE "渠道ID" IN ({sql_placeholders(channel_ids)})',
                channel_ids,
            )
        else:
            conn.execute(f'DELETE FROM "{table_name}"')


def load_strategies(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Strategy]:
    clauses: list[str] = []
    params: list[Any] = []
    if args.strategy_id:
        clauses.append(f'"统一策略ID" IN ({sql_placeholders(args.strategy_id)})')
        params.extend(args.strategy_id)
    if args.channel_id:
        clauses.append(f'"渠道ID" IN ({sql_placeholders(args.channel_id)})')
        params.extend(args.channel_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    limit = f"LIMIT {int(args.limit)}" if args.limit else ""
    rows = fetch_dicts(
        conn,
        f"""
        SELECT "统一策略ID", "渠道ID", "渠道策略ID", "策略名称", "投顾机构", "策略类型", "投顾费率"
        FROM "策略信息"
        {where}
        ORDER BY "渠道ID", "统一策略ID"
        {limit}
        """,
        params,
    )
    result: dict[str, Strategy] = {}
    for row in rows:
        fee_rate = parse_fee_rate(row.get("投顾费率"))
        strategy = Strategy(
            strategy_id=str(row["统一策略ID"]),
            channel_id=str(row["渠道ID"]),
            source_strategy_id=str(row["渠道策略ID"]),
            strategy_name=norm_text(row.get("策略名称")),
            advisor_name=norm_text(row.get("投顾机构")),
            strategy_type=norm_text(row.get("策略类型")),
            fee_rate_text=norm_text(row.get("投顾费率")),
            annual_fee_rate=fee_rate,
        )
        result[strategy.strategy_id] = strategy
    return result


def event_sort_key(row: dict[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(row.get("调仓日期") or ""),
        int(row.get("事件序号") or -1),
        str(row.get("事件时间") or ""),
        str(row.get("调仓事件ID") or ""),
    )


def snapshot_score(snapshot: Snapshot) -> tuple[int, int, int, float, str, str]:
    is_close = abs(snapshot.after_weight_sum_pct - WEIGHT_TARGET) <= WEIGHT_CLOSE_TOLERANCE
    return (
        1 if snapshot.valid_for_projection else 0,
        1 if is_close else 0,
        snapshot.positive_row_count,
        -abs(snapshot.after_weight_sum_pct - WEIGHT_TARGET),
        str(snapshot.event_time or ""),
        snapshot.event_id,
    )


def build_snapshot(
    event: dict[str, Any],
    details: list[dict[str, Any]],
    fund_profiles: dict[str, dict[str, Any]] | None = None,
) -> Snapshot:
    repair_result = repair_rebalance_details(event, details, fund_profiles)
    details = repair_result.details
    weights: dict[str, float] = defaultdict(float)
    names: dict[str, str | None] = {}
    positive_rows = 0
    missing_code_count = 0
    weight_sum = 0.0
    code_counter: Counter[str] = Counter()
    for detail in details:
        weight = to_float(detail.get("调后权重_百分比")) or 0.0
        if weight <= 0:
            continue
        positive_rows += 1
        weight_sum += weight
        code = norm_code(detail.get("基金代码"))
        if not code:
            missing_code_count += 1
            continue
        weights[code] += weight
        code_counter[code] += 1
        names.setdefault(code, norm_text(detail.get("基金名称")))

    return Snapshot(
        event_id=str(event["调仓事件ID"]),
        strategy_id=str(event["统一策略ID"]),
        channel_id=str(event["渠道ID"]),
        source_strategy_id=str(event["渠道策略ID"]),
        rebalance_date=ymd(event.get("调仓日期")),
        position_date=ymd(event.get("本次仓位日期")),
        disclosure_date=ymd(event.get("披露日期")),
        event_seq=int(event["事件序号"]) if event.get("事件序号") is not None else None,
        event_time=norm_text(event.get("事件时间")),
        title=norm_text(event.get("调仓标题")),
        weights_pct=dict(weights),
        fund_names=names,
        raw_row_count=len(details),
        positive_row_count=positive_rows,
        missing_code_count=missing_code_count,
        duplicate_fund_count=sum(1 for count in code_counter.values() if count > 1),
        after_weight_sum_pct=weight_sum,
        is_liquidation=repair_result.is_liquidation,
    )


def load_rebalance_fund_profiles(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT "基金代码", MIN("交易日期") AS min_date, MAX("交易日期") AS max_date, COUNT(*) AS row_count
        FROM "基金日度净值"
        GROUP BY "基金代码"
        """,
    )
    profiles: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = norm_code(row.get("基金代码"))
        if code:
            profiles[code] = row
    return profiles


def load_latest_snapshots(conn: sqlite3.Connection, strategy_ids: set[str]) -> dict[str, Snapshot]:
    if not strategy_ids:
        return {}
    fund_profiles = load_rebalance_fund_profiles(conn)
    params = list(strategy_ids)
    events = fetch_dicts(
        conn,
        f"""
        SELECT e."调仓事件ID", e."统一策略ID", e."渠道ID", e."渠道策略ID", e."调仓日期", e."本次仓位日期",
               e."披露日期", e."事件序号", e."事件时间", e."调仓标题", e."调仓原因",
               s."策略名称", s."策略状态"
        FROM "策略调仓事件" e
        LEFT JOIN "策略信息" s
          ON e."统一策略ID" = s."统一策略ID"
        WHERE e."统一策略ID" IN ({sql_placeholders(params)})
          AND e."调仓日期" IS NOT NULL
          AND TRIM(e."调仓日期") <> ''
        ORDER BY e."统一策略ID", e."调仓日期", COALESCE(e."事件序号", 0), COALESCE(e."事件时间", ''), e."调仓事件ID"
        """,
        params,
    )
    event_ids = [str(row["调仓事件ID"]) for row in events]
    details_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk_start in range(0, len(event_ids), 900):
        chunk = event_ids[chunk_start : chunk_start + 900]
        rows = fetch_dicts(
            conn,
            f"""
            SELECT "调仓事件ID", "基金代码", "基金名称", "调前权重_百分比",
                   "调仓动作", "分组名称", "调后权重_百分比"
            FROM "策略调仓明细"
            WHERE "调仓事件ID" IN ({sql_placeholders(chunk)})
            """,
            chunk,
        )
        for row in rows:
            details_by_event[str(row["调仓事件ID"])].append(row)

    by_strategy_date: dict[tuple[str, str], list[Snapshot]] = defaultdict(list)
    for event in events:
        strategy_id = str(event["统一策略ID"])
        rebalance_date = ymd(event.get("调仓日期"))
        if not rebalance_date:
            continue
        snapshot = build_snapshot(event, details_by_event.get(str(event["调仓事件ID"]), []), fund_profiles)
        by_strategy_date[(strategy_id, rebalance_date)].append(snapshot)

    collapsed_by_strategy: dict[str, list[Snapshot]] = defaultdict(list)
    for (strategy_id, _date), snapshots in by_strategy_date.items():
        collapsed_by_strategy[strategy_id].append(max(snapshots, key=snapshot_score))

    latest: dict[str, Snapshot] = {}
    for strategy_id, snapshots in collapsed_by_strategy.items():
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


def load_current_holdings(conn: sqlite3.Connection, strategy_ids: set[str]) -> dict[str, CurrentHolding]:
    if not strategy_ids:
        return {}
    params = list(strategy_ids)
    latest_dates = {
        str(row["统一策略ID"]): str(row["持仓日期"])
        for row in fetch_dicts(
            conn,
            f"""
            SELECT "统一策略ID", MAX("持仓日期") AS "持仓日期"
            FROM "策略当前持仓"
            WHERE "统一策略ID" IN ({sql_placeholders(params)})
            GROUP BY "统一策略ID"
            """,
            params,
        )
        if row.get("持仓日期")
    }
    result: dict[str, CurrentHolding] = {}
    items = list(latest_dates.items())
    for chunk_start in range(0, len(items), 450):
        chunk = items[chunk_start : chunk_start + 450]
        clauses = []
        chunk_params: list[Any] = []
        for strategy_id, holding_date in chunk:
            clauses.append('("统一策略ID" = ? AND "持仓日期" = ?)')
            chunk_params.extend([strategy_id, holding_date])
        rows = fetch_dicts(
            conn,
            f"""
            SELECT "统一策略ID", "渠道ID", "渠道策略ID", "持仓日期", "披露日期", "基金代码", "基金名称",
                   "基金权重_百分比", "基金净值日期"
            FROM "策略当前持仓"
            WHERE {" OR ".join(clauses)}
            ORDER BY "统一策略ID", "基金权重_百分比" DESC
            """,
            chunk_params,
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["统一策略ID"])].append(row)
        for strategy_id, group in grouped.items():
            weights: dict[str, float] = defaultdict(float)
            names: dict[str, str | None] = {}
            nav_dates: dict[str, str | None] = {}
            positive_rows = 0
            missing_code_count = 0
            weight_sum = 0.0
            disclosure_dates = [ymd(row.get("披露日期")) for row in group if ymd(row.get("披露日期"))]
            for row in group:
                weight = to_float(row.get("基金权重_百分比")) or 0.0
                if weight <= 0:
                    continue
                positive_rows += 1
                weight_sum += weight
                code = norm_code(row.get("基金代码"))
                if not code:
                    missing_code_count += 1
                    continue
                weights[code] += weight
                names.setdefault(code, norm_text(row.get("基金名称")))
                nav_dates.setdefault(code, ymd(row.get("基金净值日期")))
            result[strategy_id] = CurrentHolding(
                strategy_id=strategy_id,
                holding_date=str(group[0]["持仓日期"]),
                disclosure_date=max(disclosure_dates) if disclosure_dates else None,
                weights_pct=dict(weights),
                fund_names=names,
                fund_nav_dates=nav_dates,
                raw_row_count=len(group),
                positive_row_count=positive_rows,
                missing_code_count=missing_code_count,
                weight_sum_pct=weight_sum,
            )
    return result


def load_global_latest_nav_date(conn: sqlite3.Connection) -> str:
    row = conn.execute('SELECT MAX("交易日期") FROM "基金日度净值"').fetchone()
    if not row or not row[0]:
        raise RuntimeError("基金日度净值表为空，无法执行最新持仓推算稽核。")
    return str(row[0])


@lru_cache(maxsize=10000)
def fund_max_nav_date_cached(db_path: str, fund_code: str) -> str | None:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        'SELECT MAX("交易日期") FROM "基金日度净值" WHERE "基金代码" = ?',
        [fund_code],
    ).fetchone()
    conn.close()
    return str(row[0]) if row and row[0] else None


def latest_common_nav_date(db_path: str, fund_codes: list[str], fallback: str) -> str:
    dates = [fund_max_nav_date_cached(db_path, code) for code in fund_codes]
    usable = [date for date in dates if date]
    return min(usable) if usable else fallback


@lru_cache(maxsize=250000)
def compute_fund_factor_cached(db_path: str, fund_code: str, start_anchor: str, end_anchor: str) -> FundFactor:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    start_row = conn.execute(
        """
        SELECT "交易日期", "单位净值", "累计净值", "日收益率_百分比", "每万份收益", "是否货币基金"
        FROM "基金日度净值"
        WHERE "基金代码" = ? AND "交易日期" <= ?
        ORDER BY "交易日期" DESC
        LIMIT 1
        """,
        [fund_code, start_anchor],
    ).fetchone()
    if not start_row:
        start_row = conn.execute(
            """
            SELECT "交易日期", "单位净值", "累计净值", "日收益率_百分比", "每万份收益", "是否货币基金"
            FROM "基金日度净值"
            WHERE "基金代码" = ? AND "交易日期" >= ? AND "交易日期" <= ?
            ORDER BY "交易日期" ASC
            LIMIT 1
            """,
            [fund_code, start_anchor, end_anchor],
        ).fetchone()
    if not start_row:
        conn.close()
        return FundFactor(
            fund_code=fund_code,
            start_anchor=start_anchor,
            end_anchor=end_anchor,
            start_trade_date=None,
            end_trade_date=None,
            total_return_factor=None,
            unit_only_factor=None,
            accum_factor=None,
            daily_pct_factor=None,
            nav_row_count=0,
            missing_return_points=0,
            dividend_event_count=0,
            dividend_amount_sum=0.0,
            source_counts={},
            status="缺少基金净值",
        )

    start_trade_date = str(start_row["交易日期"])
    rows = conn.execute(
        """
        SELECT "交易日期", "单位净值", "累计净值", "日收益率_百分比", "每万份收益", "是否货币基金"
        FROM "基金日度净值"
        WHERE "基金代码" = ? AND "交易日期" > ? AND "交易日期" <= ?
        ORDER BY "交易日期" ASC
        """,
        [fund_code, start_trade_date, end_anchor],
    ).fetchall()
    dividend_rows = conn.execute(
        """
        SELECT COALESCE("除息日", "权益登记日") AS "分红日期", "每份分红"
        FROM "基金分红送配"
        WHERE "基金代码" = ?
          AND COALESCE("除息日", "权益登记日") > ?
          AND COALESCE("除息日", "权益登记日") <= ?
        """,
        [fund_code, start_trade_date, end_anchor],
    ).fetchall()
    conn.close()

    dividend_by_date: dict[str, float] = defaultdict(float)
    dividend_event_count = 0
    dividend_amount_sum = 0.0
    for row in dividend_rows:
        dividend_date = norm_text(row["分红日期"])
        amount = parse_cash_dividend(row["每份分红"])
        if dividend_date and amount is not None:
            dividend_by_date[dividend_date] += amount
            dividend_event_count += 1
            dividend_amount_sum += amount

    prev_unit = to_float(start_row["单位净值"])
    prev_accum = to_float(start_row["累计净值"])
    total_factor = 1.0
    unit_factor = 1.0
    accum_factor = 1.0
    daily_pct_factor = 1.0
    has_total = False
    has_unit = False
    has_accum = False
    has_daily_pct = False
    missing_return_points = 0
    source_counts: Counter[str] = Counter()
    end_trade_date = start_trade_date

    for row in rows:
        trade_date = str(row["交易日期"])
        end_trade_date = trade_date
        unit_nav = to_float(row["单位净值"])
        accum_nav = to_float(row["累计净值"])
        daily_pct = to_float(row["日收益率_百分比"])
        per_10k = to_float(row["每万份收益"])
        is_money = int(row["是否货币基金"] or 0) == 1
        dividend = dividend_by_date.get(trade_date, 0.0)

        unit_return_with_dividend = None
        unit_return_plain = None
        accum_return = None
        daily_pct_return = None
        if unit_nav is not None and prev_unit not in (None, 0):
            unit_return_plain = unit_nav / prev_unit - 1.0
            unit_return_with_dividend = (unit_nav + dividend) / prev_unit - 1.0
        if accum_nav is not None and prev_accum not in (None, 0):
            accum_return = accum_nav / prev_accum - 1.0
        if daily_pct is not None:
            daily_pct_return = daily_pct / 100.0

        total_return = None
        if is_money and per_10k is not None:
            total_return = per_10k / 10000.0
            unit_return_plain = total_return
            daily_pct_return = total_return if daily_pct_return is None else daily_pct_return
            source_counts["每万份收益推导"] += 1
        elif daily_pct_return is not None:
            if unit_return_with_dividend is not None:
                diff_pct = abs((unit_return_with_dividend - daily_pct_return) * 100.0)
                if diff_pct > RETURN_SOURCE_TOLERANCE_PCT:
                    total_return = daily_pct_return
                    source_counts["日收益率覆盖复权异常"] += 1
                else:
                    total_return = unit_return_with_dividend
                    source_counts["单位净值加分红推导"] += 1
            else:
                total_return = daily_pct_return
                source_counts["日收益率"] += 1
        elif unit_return_with_dividend is not None:
            total_return = unit_return_with_dividend
            source_counts["单位净值加分红推导"] += 1
        elif accum_return is not None:
            total_return = accum_return
            source_counts["累计净值推导"] += 1

        if total_return is None:
            missing_return_points += 1
            source_counts["无法推导"] += 1
        else:
            total_factor *= 1.0 + total_return
            has_total = True

        if unit_return_plain is not None:
            unit_factor *= 1.0 + unit_return_plain
            has_unit = True
        if accum_return is not None:
            accum_factor *= 1.0 + accum_return
            has_accum = True
        if daily_pct_return is not None:
            daily_pct_factor *= 1.0 + daily_pct_return
            has_daily_pct = True

        if unit_nav is not None:
            prev_unit = unit_nav
        if accum_nav is not None:
            prev_accum = accum_nav

    if not rows and start_trade_date <= end_anchor:
        status = "起止日无收益变动"
    elif not has_total:
        status = "缺少可用收益"
    else:
        status = "可推算"
    if end_trade_date < end_anchor:
        status = "净值提前结束后沿用最后净值" if status == "可推算" else status
    has_any_factor = has_total or has_unit or has_accum or has_daily_pct or start_trade_date <= end_anchor

    return FundFactor(
        fund_code=fund_code,
        start_anchor=start_anchor,
        end_anchor=end_anchor,
        start_trade_date=start_trade_date,
        end_trade_date=end_trade_date,
        total_return_factor=total_factor if has_any_factor else None,
        unit_only_factor=unit_factor if has_any_factor else None,
        accum_factor=accum_factor if has_any_factor else None,
        daily_pct_factor=daily_pct_factor if has_any_factor else None,
        nav_row_count=len(rows) + 1,
        missing_return_points=missing_return_points,
        dividend_event_count=dividend_event_count,
        dividend_amount_sum=dividend_amount_sum,
        source_counts=dict(source_counts),
        status=status,
    )


def apply_cash_fee(values: dict[str, float], cash_value: float, annual_fee_rate: float | None, start_date: str, end_date: str) -> tuple[dict[str, float], float]:
    days = days_between(start_date, end_date) or 0
    if not annual_fee_rate or days <= 0:
        return dict(values), cash_value
    total_before_fee = sum(values.values()) + cash_value
    fee_amount = total_before_fee * annual_fee_rate * days / 365.0
    if fee_amount <= 0:
        return dict(values), cash_value
    if cash_value >= fee_amount:
        return dict(values), cash_value - fee_amount
    remaining_fee = fee_amount - cash_value
    fund_total = sum(values.values())
    if fund_total <= 0:
        return dict(values), 0.0
    ratio = max((fund_total - remaining_fee) / fund_total, 0.0)
    return {code: value * ratio for code, value in values.items()}, 0.0


def project_weights(
    db_path: str,
    snapshot: Snapshot,
    start_date: str,
    end_date: str,
    *,
    factor_mode: str,
    annual_fee_rate: float | None = None,
    cash_fee: bool = False,
) -> tuple[dict[str, float], dict[str, FundFactor], dict[str, Any]]:
    fund_values: dict[str, float] = {}
    factors: dict[str, FundFactor] = {}
    source_counts: Counter[str] = Counter()
    missing_nav_count = 0
    dividend_event_count = 0
    dividend_amount_sum = 0.0

    for code, weight_pct in snapshot.weights_pct.items():
        factor = compute_fund_factor_cached(db_path, code, start_date, end_date)
        factors[code] = factor
        if factor_mode == "复权收益":
            value_factor = factor.total_return_factor
        elif factor_mode == "单位净值不含分红":
            value_factor = factor.unit_only_factor
        elif factor_mode == "累计净值":
            value_factor = factor.accum_factor
        elif factor_mode == "日收益率":
            value_factor = factor.daily_pct_factor
        else:
            value_factor = factor.total_return_factor
        if value_factor is None:
            missing_nav_count += 1
            value_factor = 1.0
        fund_values[code] = (weight_pct / 100.0) * value_factor
        source_counts.update(factor.source_counts)
        dividend_event_count += factor.dividend_event_count
        dividend_amount_sum += factor.dividend_amount_sum

    cash_value = max(0.0, (WEIGHT_TARGET - snapshot.after_weight_sum_pct) / 100.0)
    if cash_fee:
        fund_values, cash_value = apply_cash_fee(fund_values, cash_value, annual_fee_rate, start_date, end_date)
    denominator = sum(fund_values.values()) + cash_value
    if denominator <= 0:
        projected = {code: 0.0 for code in fund_values}
    else:
        projected = {code: value / denominator * 100.0 for code, value in fund_values.items()}

    meta = {
        "missing_nav_count": missing_nav_count,
        "dividend_event_count": dividend_event_count,
        "dividend_amount_sum": dividend_amount_sum,
        "source_counts": dict(source_counts),
        "cash_weight_pct_after_rebalance": round(max(0.0, WEIGHT_TARGET - snapshot.after_weight_sum_pct), 6),
        "projected_weight_sum_pct": round(sum(projected.values()), 6),
    }
    return projected, factors, meta


def compare_weights(projected: dict[str, float], current: dict[str, float]) -> dict[str, Any]:
    codes = sorted(set(projected) | set(current))
    diffs: list[float] = []
    abs_diffs: list[float] = []
    common = set(projected) & set(current)
    current_only = [code for code in current if code not in projected and current.get(code, 0.0) >= SIGNIFICANT_FUND_WEIGHT]
    history_only = [code for code in projected if code not in current and projected.get(code, 0.0) >= SIGNIFICANT_FUND_WEIGHT]
    for code in codes:
        diff = (current.get(code, 0.0) or 0.0) - (projected.get(code, 0.0) or 0.0)
        diffs.append(diff)
        abs_diffs.append(abs(diff))
    if not abs_diffs:
        max_abs = None
        avg_abs = None
        total_abs = None
        rmse = None
    else:
        max_abs = max(abs_diffs)
        avg_abs = statistics.fmean(abs_diffs)
        total_abs = sum(abs_diffs)
        rmse = math.sqrt(statistics.fmean([diff * diff for diff in diffs]))
    return {
        "fund_codes": codes,
        "common_count": len(common),
        "current_only_count": len(current_only),
        "history_only_count": len(history_only),
        "current_only_codes": current_only,
        "history_only_codes": history_only,
        "max_abs_diff_pct": max_abs,
        "avg_abs_diff_pct": avg_abs,
        "total_abs_diff_pct": total_abs,
        "rmse_pct": rmse,
    }


def verdict_from_metrics(metrics: dict[str, Any], missing_nav_count: int, current: CurrentHolding | None) -> tuple[str, str]:
    if current is None:
        return "无当前披露持仓", "当前持仓接口未披露或未展示，需使用推算补齐表。"
    max_abs = metrics.get("max_abs_diff_pct")
    total_abs = metrics.get("total_abs_diff_pct")
    if max_abs is None or total_abs is None:
        return "不可比对", "当前持仓和推算持仓均无可比基金权重。"
    if missing_nav_count > 0:
        return "需复核", f"{missing_nav_count}只基金缺少完整净值，推算差异不能直接判定。"
    if metrics["current_only_count"] or metrics["history_only_count"]:
        if max_abs <= MATCH_MINOR_MAX_ABS and total_abs <= MATCH_MINOR_TOTAL_ABS:
            return "小额差异", "基金集合存在小额不一致，主要在低权重基金或四舍五入边界。"
        return "结构不一致", "当前披露基金集合与最后一次调仓后基金集合不一致，疑似存在未披露交易、隐藏调仓或接口口径变化。"
    if max_abs <= MATCH_PASS_MAX_ABS and total_abs <= MATCH_PASS_TOTAL_ABS:
        return "通过", "最后一次调仓仓位结合基金复权收益可以解释当前持仓。"
    if max_abs <= MATCH_MINOR_MAX_ABS and total_abs <= MATCH_MINOR_TOTAL_ABS:
        return "小额差异", "差异处于净值日期、持仓展示四舍五入或极小现金变化可解释范围。"
    return "需复核", "差异超过常规净值涨跌和四舍五入解释范围。"


def choose_best_projection(
    db_path: str,
    strategy: Strategy,
    snapshot: Snapshot,
    current: CurrentHolding,
) -> dict[str, Any]:
    candidate_starts: list[str] = []
    for value in (snapshot.position_date, snapshot.rebalance_date, snapshot.disclosure_date):
        date_value = ymd(value)
        if date_value and date_value <= current.holding_date and date_value not in candidate_starts:
            candidate_starts.append(date_value)
    if not candidate_starts and snapshot.rebalance_date:
        candidate_starts.append(snapshot.rebalance_date)

    candidates: list[dict[str, Any]] = []
    for start_date in candidate_starts:
        projected, factors, meta = project_weights(
            db_path,
            snapshot,
            start_date,
            current.holding_date,
            factor_mode="复权收益",
        )
        metrics = compare_weights(projected, current.weights_pct)
        candidates.append(
            {
                "start_date": start_date,
                "end_date": current.holding_date,
                "口径": "复权收益_不计费",
                "projected": projected,
                "factors": factors,
                "meta": meta,
                "metrics": metrics,
            }
        )

    if not candidates:
        projected, factors, meta = project_weights(
            db_path,
            snapshot,
            snapshot.rebalance_date or current.holding_date,
            current.holding_date,
            factor_mode="复权收益",
        )
        metrics = compare_weights(projected, current.weights_pct)
        candidates.append(
            {
                "start_date": snapshot.rebalance_date or current.holding_date,
                "end_date": current.holding_date,
                "口径": "复权收益_不计费",
                "projected": projected,
                "factors": factors,
                "meta": meta,
                "metrics": metrics,
            }
        )

    best = min(
        candidates,
        key=lambda item: (
            item["metrics"].get("max_abs_diff_pct") if item["metrics"].get("max_abs_diff_pct") is not None else 9999.0,
            item["metrics"].get("total_abs_diff_pct") if item["metrics"].get("total_abs_diff_pct") is not None else 9999.0,
        ),
    )

    start_date = best["start_date"]
    end_date = best["end_date"]
    model_results: dict[str, dict[str, Any]] = {"复权收益_不计费": best}
    for model_name, factor_mode, cash_fee in [
        ("单位净值_不含分红", "单位净值不含分红", False),
        ("累计净值", "累计净值", False),
        ("日收益率", "日收益率", False),
        ("复权收益_现金扣费", "复权收益", True),
    ]:
        projected, factors, meta = project_weights(
            db_path,
            snapshot,
            start_date,
            end_date,
            factor_mode=factor_mode,
            annual_fee_rate=strategy.annual_fee_rate,
            cash_fee=cash_fee,
        )
        metrics = compare_weights(projected, current.weights_pct)
        model_results[model_name] = {
            "start_date": start_date,
            "end_date": end_date,
            "口径": model_name,
            "projected": projected,
            "factors": factors,
            "meta": meta,
            "metrics": metrics,
        }

    best_model = min(
        model_results.values(),
        key=lambda item: (
            item["metrics"].get("max_abs_diff_pct") if item["metrics"].get("max_abs_diff_pct") is not None else 9999.0,
            item["metrics"].get("total_abs_diff_pct") if item["metrics"].get("total_abs_diff_pct") is not None else 9999.0,
        ),
    )
    return {"best": best_model, "model_results": model_results, "date_candidates": candidates}


def infer_missing_current(
    db_path: str,
    strategy: Strategy,
    snapshot: Snapshot,
    global_latest_nav_date: str,
) -> dict[str, Any]:
    start_date = snapshot.position_date or snapshot.rebalance_date or snapshot.disclosure_date
    if not start_date:
        start_date = global_latest_nav_date
    end_date = global_latest_nav_date
    projected, factors, meta = project_weights(
        db_path,
        snapshot,
        start_date,
        end_date,
        factor_mode="复权收益",
        annual_fee_rate=strategy.annual_fee_rate,
        cash_fee=False,
    )
    return {
        "start_date": start_date,
        "end_date": end_date,
        "projected": projected,
        "factors": factors,
        "meta": meta,
    }


def infer_current_to_latest_nav(
    db_path: str,
    strategy: Strategy,
    snapshot: Snapshot,
    start_date: str | None,
    latest_nav_date: str,
) -> dict[str, Any]:
    effective_start = start_date or snapshot.position_date or snapshot.rebalance_date or snapshot.disclosure_date
    if not effective_start:
        effective_start = latest_nav_date
    projected, factors, meta = project_weights(
        db_path,
        snapshot,
        effective_start,
        latest_nav_date,
        factor_mode="复权收益",
        annual_fee_rate=strategy.annual_fee_rate,
        cash_fee=False,
    )
    return {
        "start_date": effective_start,
        "end_date": latest_nav_date,
        "projected": projected,
        "factors": factors,
        "meta": meta,
    }


def should_project_stale_current(
    args: argparse.Namespace,
    strategy: Strategy,
    current: CurrentHolding,
    global_latest_nav_date: str,
) -> bool:
    if args.no_project_stale_current:
        return False
    current_date = ymd(current.holding_date) or current.holding_date
    latest_date = ymd(global_latest_nav_date) or global_latest_nav_date
    if not current_date or not latest_date or current_date >= latest_date:
        return False
    channels = set(args.project_stale_current_channel or ["gffunds"])
    return "all" in channels or strategy.channel_id in channels


def classify_reason(
    *,
    verdict: str,
    current: CurrentHolding | None,
    snapshot: Snapshot | None,
    best_result: dict[str, Any] | None,
    model_results: dict[str, dict[str, Any]] | None,
) -> str:
    reasons: list[str] = []
    if snapshot is None:
        return "无历史调仓事件，无法从调仓仓位推算当前持仓。"
    if snapshot.is_liquidation:
        return "最后一次调仓后正基金权重为0，按目标盈/止盈到期清盘空仓处理。"
    if not snapshot.valid_for_projection:
        if not snapshot.weights_pct:
            reasons.append("最后一次调仓缺少可识别的调后基金权重。")
        if snapshot.missing_code_count:
            reasons.append(f"最后一次调仓有{snapshot.missing_code_count}行正权重缺基金代码，无法准确取净值。")
    if abs(snapshot.after_weight_sum_pct - WEIGHT_TARGET) > WEIGHT_CLOSE_TOLERANCE:
        reasons.append(f"最后一次调仓后基金权重和为{snapshot.after_weight_sum_pct:.2f}%，存在现金或披露不闭合。")
    if current is None:
        reasons.append("当前持仓未采集到披露明细，已单独输出可推算补齐结果。")
        return "；".join(reasons) if reasons else "当前持仓缺失，按最后调仓仓位和基金复权收益推算补齐。"
    if current.missing_code_count:
        reasons.append(f"当前持仓有{current.missing_code_count}行正权重缺基金代码。")
    if abs(current.weight_sum_pct - WEIGHT_TARGET) > WEIGHT_CLOSE_TOLERANCE:
        reasons.append(f"当前披露基金权重和为{current.weight_sum_pct:.2f}%，存在现金、非基金资产或展示不闭合。")
    if not best_result or not model_results:
        return "；".join(reasons) if reasons else verdict

    metrics = best_result["metrics"]
    if metrics.get("current_only_count"):
        reasons.append(f"当前有{metrics['current_only_count']}只显著基金不在最后调仓后仓位中。")
    if metrics.get("history_only_count"):
        reasons.append(f"最后调仓后有{metrics['history_only_count']}只显著基金当前未披露。")

    fee_free = model_results.get("复权收益_不计费", {}).get("metrics", {})
    unit_only = model_results.get("单位净值_不含分红", {}).get("metrics", {})
    cash_fee = model_results.get("复权收益_现金扣费", {}).get("metrics", {})
    fee_free_max = fee_free.get("max_abs_diff_pct")
    unit_only_max = unit_only.get("max_abs_diff_pct")
    cash_fee_max = cash_fee.get("max_abs_diff_pct")
    if unit_only_max is not None and fee_free_max is not None and unit_only_max - fee_free_max >= DIVIDEND_IMPACT_THRESHOLD:
        reasons.append("分红/复权收益口径能明显改善匹配，不能用单位净值涨跌直接推算。")
    if cash_fee_max is not None and fee_free_max is not None and fee_free_max - cash_fee_max >= FEE_IMPACT_THRESHOLD:
        reasons.append("现金扣投顾费口径较不计费口径更接近，投顾费结算可能影响基金权重和。")
    if verdict == "通过" and not reasons:
        reasons.append("最后一次调仓后仓位经基金复权收益滚动后与当前披露持仓一致。")
    elif verdict == "小额差异" and not reasons:
        reasons.append("差异较小，主要可能来自持仓展示四舍五入、净值日期错位或极小现金变化。")
    elif verdict in ("需复核", "结构不一致") and not reasons:
        reasons.append("复权收益、分红和投顾费候选口径均不能充分解释差异，需复核是否存在缺失调仓或接口披露口径变化。")
    return "；".join(reasons)


def build_fund_rows(
    strategy: Strategy,
    snapshot: Snapshot,
    current: CurrentHolding | None,
    projected: dict[str, float],
    factors: dict[str, FundFactor],
    *,
    start_date: str,
    end_date: str,
    is_inferred: bool,
    generated_at: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    codes = sorted(set(projected) | (set(current.weights_pct) if current else set()))
    for code in codes:
        factor = factors.get(code)
        projected_weight = projected.get(code)
        current_weight = current.weights_pct.get(code) if current else None
        diff = (current_weight - projected_weight) if current_weight is not None and projected_weight is not None else None
        fund_name = (
            snapshot.fund_names.get(code)
            or (current.fund_names.get(code) if current else None)
            or None
        )
        rows.append(
            {
                "明细ID": f"{strategy.strategy_id}#{end_date}#{code}",
                "统一策略ID": strategy.strategy_id,
                "渠道ID": strategy.channel_id,
                "策略名称": strategy.strategy_name,
                "基金代码": code,
                "基金名称": fund_name,
                "最新调仓事件ID": snapshot.event_id,
                "起算日期": start_date,
                "推算日期": end_date,
                "当前持仓日期": current.holding_date if current else None,
                "调后权重_百分比": round_or_none(snapshot.weights_pct.get(code), 8),
                "推算权重_百分比": round_or_none(projected_weight, 8),
                "当前权重_百分比": round_or_none(current_weight, 8),
                "差异_百分点": round_or_none(diff, 8),
                "绝对差_百分点": round_or_none(abs(diff), 8) if diff is not None else None,
                "收益因子_复权": round_or_none(factor.total_return_factor if factor else None, 10),
                "收益因子_不含分红": round_or_none(factor.unit_only_factor if factor else None, 10),
                "收益因子_累计净值": round_or_none(factor.accum_factor if factor else None, 10),
                "净值起始日": factor.start_trade_date if factor else None,
                "净值结束日": factor.end_trade_date if factor else None,
                "净值行数": factor.nav_row_count if factor else 0,
                "分红事件数": factor.dividend_event_count if factor else 0,
                "分红金额合计": round_or_none(factor.dividend_amount_sum if factor else 0.0, 8),
                "净值状态": factor.status if factor else "未推算",
                "是否补齐推算": 1 if is_inferred else 0,
                "生成时间": generated_at,
            }
        )
    return rows


def build_strategy_row(
    strategy: Strategy,
    snapshot: Snapshot | None,
    current: CurrentHolding | None,
    *,
    audit_status: str,
    conclusion: str,
    reason_category: str,
    reason: str,
    projected: dict[str, float] | None,
    meta: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
    start_date: str | None,
    end_date: str | None,
    best_model_name: str | None,
    model_results: dict[str, dict[str, Any]] | None,
    can_infer: bool,
    generated_at: str,
) -> dict[str, Any]:
    fee_free_metrics = (model_results or {}).get("复权收益_不计费", {}).get("metrics", {})
    unit_metrics = (model_results or {}).get("单位净值_不含分红", {}).get("metrics", {})
    cash_fee_metrics = (model_results or {}).get("复权收益_现金扣费", {}).get("metrics", {})
    fee_free_max = fee_free_metrics.get("max_abs_diff_pct")
    unit_max = unit_metrics.get("max_abs_diff_pct")
    cash_fee_max = cash_fee_metrics.get("max_abs_diff_pct")
    dividend_improvement = None
    if fee_free_max is not None and unit_max is not None:
        dividend_improvement = unit_max - fee_free_max
    fee_improvement = None
    if fee_free_max is not None and cash_fee_max is not None:
        fee_improvement = fee_free_max - cash_fee_max
    return {
        "统一策略ID": strategy.strategy_id,
        "渠道ID": strategy.channel_id,
        "渠道策略ID": strategy.source_strategy_id,
        "策略名称": strategy.strategy_name,
        "投顾机构": strategy.advisor_name,
        "策略类型": strategy.strategy_type,
        "投顾费率": strategy.fee_rate_text,
        "年化投顾费率_百分比": round_or_none(strategy.annual_fee_rate * 100.0 if strategy.annual_fee_rate is not None else None, 6),
        "最新调仓事件ID": snapshot.event_id if snapshot else None,
        "最新调仓日期": snapshot.rebalance_date if snapshot else None,
        "最新调仓仓位日期": snapshot.position_date if snapshot else None,
        "最新调仓披露日期": snapshot.disclosure_date if snapshot else None,
        "当前持仓日期": current.holding_date if current else None,
        "当前持仓披露日期": current.disclosure_date if current else None,
        "稽核状态": audit_status,
        "稽核结论": conclusion,
        "归因分类": reason_category,
        "差异归因": reason,
        "是否已有当前持仓": 1 if current else 0,
        "是否可推算补齐": 1 if can_infer else 0,
        "推算持仓日期": end_date,
        "最佳推算口径": best_model_name,
        "最佳起算日期": start_date,
        "持有天数": days_between(start_date, end_date) if start_date and end_date else None,
        "当前基金数": len(current.weights_pct) if current else 0,
        "推算基金数": len(projected or {}),
        "共同基金数": metrics.get("common_count") if metrics else None,
        "当前有历史无基金数": metrics.get("current_only_count") if metrics else None,
        "历史有当前无基金数": metrics.get("history_only_count") if metrics else None,
        "当前权重和_百分比": round_or_none(current.weight_sum_pct if current else None, 6),
        "调后权重和_百分比": round_or_none(snapshot.after_weight_sum_pct if snapshot else None, 6),
        "推算权重和_百分比": round_or_none(sum((projected or {}).values()), 6),
        "现金权重_调后_百分比": round_or_none(meta.get("cash_weight_pct_after_rebalance") if meta else None, 6),
        "最大绝对差_百分点": round_or_none(metrics.get("max_abs_diff_pct") if metrics else None, 8),
        "平均绝对差_百分点": round_or_none(metrics.get("avg_abs_diff_pct") if metrics else None, 8),
        "绝对差合计_百分点": round_or_none(metrics.get("total_abs_diff_pct") if metrics else None, 8),
        "均方根差_百分点": round_or_none(metrics.get("rmse_pct") if metrics else None, 8),
        "费前最大绝对差_百分点": round_or_none(fee_free_max, 8),
        "不含分红最大绝对差_百分点": round_or_none(unit_max, 8),
        "现金扣费最大绝对差_百分点": round_or_none(cash_fee_max, 8),
        "分红影响_最大改善_百分点": round_or_none(dividend_improvement, 8),
        "投顾费影响_最大改善_百分点": round_or_none(fee_improvement, 8),
        "缺净值基金数": int(meta.get("missing_nav_count") or 0) if meta else None,
        "分红事件数": int(meta.get("dividend_event_count") or 0) if meta else None,
        "收益源统计JSON": json.dumps(meta.get("source_counts") if meta else {}, ensure_ascii=False, sort_keys=True),
        "生成时间": generated_at,
    }


def write_db_results(
    conn: sqlite3.Connection,
    strategy_rows: list[dict[str, Any]],
    fund_rows: list[dict[str, Any]],
    inferred_rows: list[dict[str, Any]],
    channel_ids: list[str] | None = None,
    strategy_ids: list[str] | None = None,
) -> None:
    ensure_result_tables(conn)
    clear_result_tables(conn, channel_ids, strategy_ids)
    if strategy_rows:
        headers = list(strategy_rows[0].keys())
        conn.executemany(
            f'INSERT OR REPLACE INTO "{TABLE_STRATEGY_AUDIT}" ({",".join(f"""\"{h}\"""" for h in headers)}) VALUES ({sql_placeholders(headers)})',
            [[row.get(header) for header in headers] for row in strategy_rows],
        )
    if fund_rows:
        headers = list(fund_rows[0].keys())
        conn.executemany(
            f'INSERT OR REPLACE INTO "{TABLE_FUND_AUDIT}" ({",".join(f"""\"{h}\"""" for h in headers)}) VALUES ({sql_placeholders(headers)})',
            [[row.get(header) for header in headers] for row in fund_rows],
        )
    if inferred_rows:
        headers = list(inferred_rows[0].keys())
        conn.executemany(
            f'INSERT OR REPLACE INTO "{TABLE_INFERRED_HOLDING}" ({",".join(f"""\"{h}\"""" for h in headers)}) VALUES ({sql_placeholders(headers)})',
            [[row.get(header) for header in headers] for row in inferred_rows],
        )
    conn.commit()


def build_inferred_rows(
    strategy: Strategy,
    snapshot: Snapshot,
    inferred: dict[str, Any],
    conclusion: str,
    generated_at: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code, weight in sorted(inferred["projected"].items(), key=lambda item: item[1], reverse=True):
        factor = inferred["factors"].get(code)
        confidence = "高" if factor and factor.status in ("可推算", "起止日无收益变动") else "中"
        rows.append(
            {
                "统一策略ID": strategy.strategy_id,
                "渠道ID": strategy.channel_id,
                "渠道策略ID": strategy.source_strategy_id,
                "策略名称": strategy.strategy_name,
                "推算持仓日期": inferred["end_date"],
                "基金代码": code,
                "基金名称": snapshot.fund_names.get(code),
                "推算基金权重_百分比": round(weight, 8),
                "最后调仓后权重_百分比": round_or_none(snapshot.weights_pct.get(code), 8),
                "收益因子_复权": round_or_none(factor.total_return_factor if factor else None, 10),
                "推算来源": "最后一次调仓调后权重 + 基金复权收益滚动",
                "置信度": confidence,
                "稽核结论": conclusion,
                "最新调仓事件ID": snapshot.event_id,
                "最新调仓日期": snapshot.rebalance_date,
                "生成时间": generated_at,
            }
        )
    return rows


def should_write_incomplete_current_projection(
    status: str,
    current: CurrentHolding,
    snapshot: Snapshot,
    best: dict[str, Any],
) -> bool:
    if status != "结构不一致":
        return False
    if current.weight_sum_pct >= 50:
        return False
    if abs(snapshot.after_weight_sum_pct - WEIGHT_TARGET) > WEIGHT_CLOSE_TOLERANCE:
        return False
    projected = best.get("projected") or {}
    if not projected:
        return False
    if abs(sum(projected.values()) - WEIGHT_TARGET) > WEIGHT_CLOSE_TOLERANCE:
        return False
    if int((best.get("meta") or {}).get("missing_nav_count") or 0) > 0:
        return False
    return True


def summarize(strategy_rows: list[dict[str, Any]], fund_rows: list[dict[str, Any]], inferred_rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counter = Counter(row["稽核状态"] for row in strategy_rows)
    reason_counter = Counter(row.get("归因分类") or "未分类" for row in strategy_rows)
    inferred_missing_by_channel: dict[str, set[str]] = defaultdict(set)
    stale_projected_by_channel: dict[str, set[str]] = defaultdict(set)
    for row in inferred_rows:
        target = stale_projected_by_channel if "滚动到最新净值日" in str(row.get("稽核结论") or "") else inferred_missing_by_channel
        target[str(row["渠道ID"])].add(str(row["统一策略ID"]))
    channel_summary: dict[str, dict[str, Any]] = {}
    for row in strategy_rows:
        channel = str(row["渠道ID"])
        item = channel_summary.setdefault(
            channel,
            {
                "策略数": 0,
                "有当前持仓明细策略数": 0,
                "有当前可比基金权重策略数": 0,
                "当前明细无基金权重策略数": 0,
                "完全无当前持仓明细策略数": 0,
                "可推算策略数": 0,
                "缺当前且已推算补齐策略数": 0,
                "持仓滞后滚动推算策略数": 0,
                "通过": 0,
                "小额差异": 0,
                "需复核": 0,
                "结构不一致": 0,
                "无当前披露持仓": 0,
                "不可推算": 0,
            },
        )
        item["策略数"] += 1
        has_current_rows = int(row.get("是否已有当前持仓") or 0)
        has_comparable_weight = 1 if row.get("最大绝对差_百分点") is not None else 0
        item["有当前持仓明细策略数"] += has_current_rows
        item["有当前可比基金权重策略数"] += has_comparable_weight
        item["可推算策略数"] += int(row.get("是否可推算补齐") or 0)
        if row.get("归因分类") == "当前持仓有明细但无基金权重":
            item["当前明细无基金权重策略数"] += 1
        if row.get("归因分类") == "当前持仓未披露":
            item["完全无当前持仓明细策略数"] += 1
        status = row["稽核状态"]
        if status in item:
            item[status] += 1
    for channel in set(inferred_missing_by_channel) | set(stale_projected_by_channel):
        channel_summary.setdefault(
            channel,
            {
                "策略数": 0,
                "有当前持仓明细策略数": 0,
                "有当前可比基金权重策略数": 0,
                "当前明细无基金权重策略数": 0,
                "完全无当前持仓明细策略数": 0,
                "可推算策略数": 0,
                "缺当前且已推算补齐策略数": 0,
                "持仓滞后滚动推算策略数": 0,
                "通过": 0,
                "小额差异": 0,
                "需复核": 0,
                "结构不一致": 0,
                "无当前披露持仓": 0,
                "不可推算": 0,
            },
        )
        channel_summary[channel]["缺当前且已推算补齐策略数"] = len(inferred_missing_by_channel.get(channel, set()))
        channel_summary[channel]["持仓滞后滚动推算策略数"] = len(stale_projected_by_channel.get(channel, set()))
    comparable = [row for row in strategy_rows if row.get("最大绝对差_百分点") is not None]
    max_abs_values = [float(row["最大绝对差_百分点"]) for row in comparable]
    inferred_missing_ids = {strategy_id for ids in inferred_missing_by_channel.values() for strategy_id in ids}
    stale_projected_ids = {strategy_id for ids in stale_projected_by_channel.values() for strategy_id in ids}
    return {
        "strategy_total": len(strategy_rows),
        "fund_detail_total": len(fund_rows),
        "inferred_holding_row_total": len(inferred_rows),
        "inferred_strategy_total": len({row["统一策略ID"] for row in inferred_rows}),
        "inferred_missing_strategy_total": len(inferred_missing_ids),
        "stale_projected_strategy_total": len(stale_projected_ids),
        "status_counts": dict(status_counter),
        "reason_category_counts": dict(reason_counter),
        "channel_summary": channel_summary,
        "comparable_strategy_total": len(comparable),
        "max_abs_diff_pct_median": round(statistics.median(max_abs_values), 6) if max_abs_values else None,
        "max_abs_diff_pct_p90": round(statistics.quantiles(max_abs_values, n=10)[8], 6) if len(max_abs_values) >= 10 else None,
        "max_abs_diff_pct_max": round(max(max_abs_values), 6) if max_abs_values else None,
    }


def write_report(output_dir: Path, summary: dict[str, Any], strategy_rows: list[dict[str, Any]]) -> None:
    status_counts = summary["status_counts"]
    lines = [
        "# 最新持仓推算稽核报告",
        "",
        f"- 策略总数：{summary['strategy_total']}",
        f"- 可比对策略数：{summary['comparable_strategy_total']}",
        f"- 当前持仓缺失但可推算补齐策略数：{summary['inferred_missing_strategy_total']}",
        f"- 当前持仓滞后但已滚动到最新净值日策略数：{summary['stale_projected_strategy_total']}",
        f"- 明细行数：{summary['fund_detail_total']}",
        f"- 状态分布：{json.dumps(status_counts, ensure_ascii=False, sort_keys=True)}",
        "",
        "## 归因分类",
        "",
        "| 归因分类 | 策略数 |",
        "| --- | ---: |",
    ]
    for category, count in sorted(summary["reason_category_counts"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {category} | {count} |")
    lines.extend(
        [
            "",
        "## 稽核口径",
        "",
        "1. 取每个策略最后一个调仓日；同日多事件时选择调后权重更完整、权重和更接近100%的事件。",
        "2. 起算日优先使用本次仓位日期，其次调仓日期、披露日期；有当前持仓时用当前持仓日期作为推算终点。",
        "3. 基金收益优先用复权收益口径：货币基金用每万份收益，非货币基金用单位净值加除息日分红，并在日收益率与复权推导差异过大时采用日收益率兜底。",
        "4. 同时比较单位净值不含分红、累计净值、日收益率、现金扣投顾费等候选口径，用于差异归因。",
        "5. 当前持仓缺失的策略不写回原始持仓表，只写入“策略当前持仓推算补齐”表和 CSV，保留来源与置信度。",
        "",
        "## 渠道概览",
        "",
        "| 渠道 | 策略数 | 有当前明细 | 可比基金权重 | 明细无权重 | 完全无当前明细 | 缺当前已补齐 | 滞后已滚动 | 通过 | 小额差异 | 需复核 | 结构不一致 | 不可推算 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for channel, row in sorted(summary["channel_summary"].items()):
        lines.append(
            f"| {channel} | {row['策略数']} | {row['有当前持仓明细策略数']} | "
            f"{row['有当前可比基金权重策略数']} | {row['当前明细无基金权重策略数']} | "
            f"{row['完全无当前持仓明细策略数']} | {row['缺当前且已推算补齐策略数']} | "
            f"{row['持仓滞后滚动推算策略数']} | "
            f"{row['通过']} | {row['小额差异']} | {row['需复核']} | {row['结构不一致']} | {row['不可推算']} |"
        )
    top_diff = sorted(
        [row for row in strategy_rows if row.get("最大绝对差_百分点") is not None],
        key=lambda item: float(item["最大绝对差_百分点"]),
        reverse=True,
    )[:30]
    lines.extend(
        [
            "",
            "## 最大差异策略",
            "",
            "| 渠道 | 策略ID | 策略名称 | 状态 | 最大差异 | 差异合计 | 归因 |",
            "| --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in top_diff:
        reason = str(row.get("差异归因") or "").replace("|", "/")
        name = str(row.get("策略名称") or "").replace("|", "/")
        lines.append(
            f"| {row['渠道ID']} | {row['统一策略ID']} | {name} | {row['稽核状态']} | "
            f"{row.get('最大绝对差_百分点')} | {row.get('绝对差合计_百分点')} | {reason[:120]} |"
        )
    (output_dir / "latest_holding_projection_audit_report.md").write_text("\n".join(lines), encoding="utf-8")


def comparison_reason_category(
    status: str,
    best_result: dict[str, Any],
    model_results: dict[str, dict[str, Any]],
) -> str:
    metrics = best_result["metrics"]
    meta = best_result["meta"]
    if status == "通过":
        return "推算与当前一致"
    if status == "小额差异":
        return "展示四舍五入或净值日差异"
    if int(meta.get("missing_nav_count") or 0) > 0:
        return "基金净值缺失影响推算"
    if metrics.get("current_only_count") or metrics.get("history_only_count"):
        return "历史调仓与当前基金集合不一致"
    fee_free = model_results.get("复权收益_不计费", {}).get("metrics", {})
    unit_only = model_results.get("单位净值_不含分红", {}).get("metrics", {})
    cash_fee = model_results.get("复权收益_现金扣费", {}).get("metrics", {})
    fee_free_max = fee_free.get("max_abs_diff_pct")
    unit_only_max = unit_only.get("max_abs_diff_pct")
    cash_fee_max = cash_fee.get("max_abs_diff_pct")
    if unit_only_max is not None and fee_free_max is not None and unit_only_max - fee_free_max >= DIVIDEND_IMPACT_THRESHOLD:
        return "分红复权口径影响"
    if cash_fee_max is not None and fee_free_max is not None and fee_free_max - cash_fee_max >= FEE_IMPACT_THRESHOLD:
        return "投顾费结算可能影响"
    return "权重差异超阈值"


def main() -> None:
    args = parse_args()
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    output_dir = args.output_root / datetime.now().strftime("%Y-%m-%d") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    strategies = load_strategies(conn, args)
    strategy_ids = set(strategies)
    latest_snapshots = load_latest_snapshots(conn, strategy_ids)
    current_holdings = load_current_holdings(conn, strategy_ids)
    global_latest_nav_date = load_global_latest_nav_date(conn)

    strategy_rows: list[dict[str, Any]] = []
    fund_rows: list[dict[str, Any]] = []
    inferred_rows: list[dict[str, Any]] = []

    db_path_str = str(args.db_path)
    for index, strategy in enumerate(strategies.values(), start=1):
        if index % 100 == 0:
            print(f"[progress] {index}/{len(strategies)}", flush=True)
        snapshot = latest_snapshots.get(strategy.strategy_id)
        raw_current = current_holdings.get(strategy.strategy_id)
        current = raw_current if raw_current and raw_current.weights_pct and raw_current.weight_sum_pct > 0 else None
        if snapshot is None:
            status = "不可推算"
            conclusion = "缺少历史调仓事件"
            reason = classify_reason(verdict=status, current=current, snapshot=None, best_result=None, model_results=None)
            strategy_rows.append(
                build_strategy_row(
                    strategy,
                    snapshot,
                    current,
                    audit_status=status,
                    conclusion=conclusion,
                    reason_category="无历史调仓事件",
                    reason=reason,
                    projected=None,
                    meta=None,
                    metrics=None,
                    start_date=None,
                    end_date=current.holding_date if current else None,
                    best_model_name=None,
                    model_results=None,
                    can_infer=False,
                    generated_at=generated_at,
                )
            )
            continue
        if not snapshot.valid_for_projection:
            status = "不可推算"
            conclusion = "最后一次调仓明细不完整"
            reason = classify_reason(verdict=status, current=current, snapshot=snapshot, best_result=None, model_results=None)
            strategy_rows.append(
                build_strategy_row(
                    strategy,
                    snapshot,
                    current,
                    audit_status=status,
                    conclusion=conclusion,
                    reason_category="历史调仓明细无可用调后权重",
                    reason=reason,
                    projected=None,
                    meta=None,
                    metrics=None,
                    start_date=snapshot.position_date or snapshot.rebalance_date,
                    end_date=current.holding_date if current else None,
                    best_model_name=None,
                    model_results=None,
                    can_infer=False,
                    generated_at=generated_at,
                )
            )
            continue

        if snapshot.is_liquidation and not current:
            inferred = infer_missing_current(db_path_str, strategy, snapshot, global_latest_nav_date)
            status = "已清盘/空仓"
            conclusion = "最后一次调仓后正基金权重为0，按清盘空仓处理"
            reason = classify_reason(
                verdict=status,
                current=None,
                snapshot=snapshot,
                best_result=None,
                model_results=None,
            )
            strategy_rows.append(
                build_strategy_row(
                    strategy,
                    snapshot,
                    None,
                    audit_status=status,
                    conclusion=conclusion,
                    reason_category="调后空仓/清盘事件",
                    reason=reason,
                    projected=inferred["projected"],
                    meta=inferred["meta"],
                    metrics=None,
                    start_date=inferred["start_date"],
                    end_date=inferred["end_date"],
                    best_model_name="清盘空仓",
                    model_results=None,
                    can_infer=True,
                    generated_at=generated_at,
                )
            )
            continue

        if current:
            chosen = choose_best_projection(db_path_str, strategy, snapshot, current)
            best = chosen["best"]
            metrics = best["metrics"]
            status, conclusion = verdict_from_metrics(metrics, int(best["meta"].get("missing_nav_count") or 0), current)
            reason = classify_reason(
                verdict=status,
                current=current,
                snapshot=snapshot,
                best_result=best,
                model_results=chosen["model_results"],
            )
            reason_category = comparison_reason_category(status, best, chosen["model_results"])
            strategy_rows.append(
                build_strategy_row(
                    strategy,
                    snapshot,
                    current,
                    audit_status=status,
                    conclusion=conclusion,
                    reason_category=reason_category,
                    reason=reason,
                    projected=best["projected"],
                    meta=best["meta"],
                    metrics=metrics,
                    start_date=best["start_date"],
                    end_date=best["end_date"],
                    best_model_name=best["口径"],
                    model_results=chosen["model_results"],
                    can_infer=True,
                    generated_at=generated_at,
                )
            )
            fund_rows.extend(
                build_fund_rows(
                    strategy,
                    snapshot,
                    current,
                    best["projected"],
                    best["factors"],
                    start_date=best["start_date"],
                    end_date=best["end_date"],
                    is_inferred=False,
                    generated_at=generated_at,
                )
            )
            if should_write_incomplete_current_projection(status, current, snapshot, best):
                incomplete_conclusion = (
                    f"当前披露持仓权重和仅{current.weight_sum_pct:.2f}%，明显不闭合；"
                    "已按最后一次调仓后权重和基金复权收益推算补齐，不覆盖原始当前持仓"
                )
                inferred_rows.extend(build_inferred_rows(strategy, snapshot, best, incomplete_conclusion, generated_at))
            if should_project_stale_current(args, strategy, current, global_latest_nav_date):
                latest_inferred = infer_current_to_latest_nav(
                    db_path_str,
                    strategy,
                    snapshot,
                    best.get("start_date"),
                    global_latest_nav_date,
                )
                latest_missing_nav_count = int(latest_inferred["meta"].get("missing_nav_count") or 0)
                latest_conclusion = (
                    "当前披露持仓日早于最新基金净值日，已按最后调仓仓位和基金复权收益滚动到最新净值日"
                )
                if latest_missing_nav_count:
                    latest_conclusion += f"；{latest_missing_nav_count}只基金缺少完整净值，使用最近可用净值兜底"
                inferred_rows.extend(build_inferred_rows(strategy, snapshot, latest_inferred, latest_conclusion, generated_at))
                fund_rows.extend(
                    build_fund_rows(
                        strategy,
                        snapshot,
                        current,
                        latest_inferred["projected"],
                        latest_inferred["factors"],
                        start_date=latest_inferred["start_date"],
                        end_date=latest_inferred["end_date"],
                        is_inferred=True,
                        generated_at=generated_at,
                    )
                )
        else:
            inferred = infer_missing_current(db_path_str, strategy, snapshot, global_latest_nav_date)
            missing_nav_count = int(inferred["meta"].get("missing_nav_count") or 0)
            if raw_current and raw_current.raw_row_count > 0 and not current:
                status = "无当前披露持仓"
                conclusion = "当前持仓明细存在但缺少正基金占比，已按最后调仓后仓位推算补齐"
                reason_category = "当前持仓有明细但无基金权重"
            elif missing_nav_count:
                status = "无当前披露持仓"
                conclusion = f"当前持仓缺失，可推算但{missing_nav_count}只基金缺净值"
                reason_category = "当前持仓未披露"
            else:
                status = "无当前披露持仓"
                conclusion = "当前持仓缺失，已按最后调仓后仓位推算补齐"
                reason_category = "当前持仓未披露"
            reason = classify_reason(
                verdict=status,
                current=None,
                snapshot=snapshot,
                best_result=None,
                model_results=None,
            )
            if raw_current and raw_current.raw_row_count > 0 and not current:
                reason = (
                    f"当前持仓接口有{raw_current.raw_row_count}行明细，但正基金权重和为"
                    f"{raw_current.weight_sum_pct:.2f}%，不能作为最新基金占比；" + reason
                )
            strategy_rows.append(
                build_strategy_row(
                    strategy,
                    snapshot,
                    raw_current if raw_current and raw_current.raw_row_count > 0 else None,
                    audit_status=status,
                    conclusion=conclusion,
                    reason_category=reason_category,
                    reason=reason,
                    projected=inferred["projected"],
                    meta=inferred["meta"],
                    metrics=None,
                    start_date=inferred["start_date"],
                    end_date=inferred["end_date"],
                    best_model_name="复权收益_不计费",
                    model_results=None,
                    can_infer=missing_nav_count == 0,
                    generated_at=generated_at,
                )
            )
            inferred_rows.extend(build_inferred_rows(strategy, snapshot, inferred, conclusion, generated_at))
            fund_rows.extend(
                build_fund_rows(
                    strategy,
                    snapshot,
                    None,
                    inferred["projected"],
                    inferred["factors"],
                    start_date=inferred["start_date"],
                    end_date=inferred["end_date"],
                    is_inferred=True,
                    generated_at=generated_at,
                )
            )

    summary = summarize(strategy_rows, fund_rows, inferred_rows)
    summary["generated_at"] = generated_at
    summary["db_path"] = str(args.db_path)
    summary["global_latest_nav_date"] = global_latest_nav_date
    summary["output_dir"] = str(output_dir)

    write_csv(output_dir / "strategy_projection_audit.csv", strategy_rows)
    write_csv(output_dir / "fund_projection_audit_detail.csv", fund_rows)
    write_csv(output_dir / "inferred_current_holdings.csv", inferred_rows)
    write_csv(
        output_dir / "top_strategy_differences.csv",
        sorted(
            [row for row in strategy_rows if row.get("最大绝对差_百分点") is not None],
            key=lambda item: float(item["最大绝对差_百分点"]),
            reverse=True,
        )[:200],
    )
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(output_dir, summary, strategy_rows)

    if args.write_db:
        write_db_results(conn, strategy_rows, fund_rows, inferred_rows, args.channel_id or None, args.strategy_id or None)

    conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
