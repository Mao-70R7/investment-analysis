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
from datetime import date, datetime
from pathlib import Path
from typing import Any

from rebalance_snapshot_repairs import repair_rebalance_details


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "strategy_nav_reconstruction"

ALGORITHM_VERSION = "rebalance_asset_fee_dual_nav_v4"
DEFAULT_INITIAL_ASSET = 100_000_000.0
WEIGHT_SUM_TARGET = 100.0
WEIGHT_SUM_TOLERANCE = 1.0
WEIGHT_NORMALIZE_EPSILON = 0.0001
ANNUAL_TRADING_DAYS = 252
FEE_ACCRUAL_DAYS = 365.0
RETURN_SOURCE_TOLERANCE_PCT = 0.01
TARGET_PROFIT_KEYWORDS = ("目标盈", "小目标", "止盈")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay advisor strategy NAV with asset simulation, dividend reinvestment, and daily advisory fee accrual."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--algorithm-version", default=ALGORITHM_VERSION)
    parser.add_argument("--initial-asset", type=float, default=DEFAULT_INITIAL_ASSET)
    parser.add_argument("--channel-id", action="append", default=[], help="Optional channel id filter.")
    parser.add_argument("--strategy-id", action="append", default=[], help="Optional strategy id filter.")
    parser.add_argument("--limit", type=int, default=None, help="Optional strategy limit.")
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


def parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def days_between(start: str, end: str) -> int:
    return (parse_ymd(end) - parse_ymd(start)).days


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
    if "%" in text or "％" in text:
        return max(number / 100.0, 0.0)
    if "bp" in lowered or "基点" in text:
        return max(number / 10000.0, 0.0)
    if number >= 1.0:
        return max(number / 100.0, 0.0)
    return max(number / 100.0, 0.0)


def sql_in_placeholders(values: list[Any]) -> str:
    return ",".join("?" for _ in values)


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params)]


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


@dataclass
class FundProfile:
    fund_code: str
    min_date: str | None
    max_date: str | None
    row_count: int


@dataclass
class Snapshot:
    event: dict[str, Any]
    weights_pct: dict[str, float]
    fund_names: dict[str, str | None]
    raw_detail_count: int
    positive_detail_count: int
    missing_code_count: int
    duplicate_weight_row_count: int
    duplicate_fund_count: int
    weight_sum_pct: float
    basic_issues: list[str]
    repairs: list[str]

    @property
    def event_id(self) -> str:
        return str(self.event["调仓事件ID"])

    @property
    def rebalance_date(self) -> str:
        return str(self.event["调仓日期"])

    @property
    def is_basic_valid(self) -> bool:
        return not self.basic_issues

    @property
    def signature(self) -> tuple[tuple[str, float], ...]:
        return tuple(sorted((code, round(weight, 8)) for code, weight in self.weights_pct.items()))


@dataclass
class Segment:
    strategy: dict[str, Any]
    snapshot: Snapshot
    seq: int
    start_date: str
    effective_start_date: str
    end_date: str
    end_type: str
    next_rebalance_date: str | None
    is_valid: bool
    simulation_relevant: bool
    exclusion_reason: str | None
    issues: list[str]
    repairs: list[str]
    missing_nav_count: int
    start_coverage_gap_count: int
    end_coverage_gap_count: int
    normalized_weights: dict[str, float]
    date_count: int = 0
    interval_return_pct: float | None = None
    interval_gross_return_pct: float | None = None
    interval_fee_amount: float = 0.0
    interval_fee_drag_pct: float | None = None
    carried_missing_return_points: int = 0

    @property
    def quality_grade(self) -> str:
        if not self.is_valid:
            return "不可回放"
        return "完整_已修复" if self.repairs else "完整"


def create_result_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS "策略模拟净值" (
            "统一策略ID" TEXT NOT NULL,
            "渠道ID" TEXT NOT NULL,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "交易日期" TEXT NOT NULL,
            "模拟单位净值" REAL NOT NULL,
            "日收益率_百分比" REAL NOT NULL,
            "累计收益率_百分比" REAL NOT NULL,
            "最大回撤_百分比" REAL NOT NULL,
            "费前单位净值" REAL,
            "费前日收益率_百分比" REAL,
            "费前累计收益率_百分比" REAL,
            "费前最大回撤_百分比" REAL,
            "模拟总资产_元" REAL,
            "费前总资产_元" REAL,
            "当日投顾费_元" REAL,
            "累计投顾费_元" REAL,
            "投顾费率_年化_百分比" REAL,
            "初始资产_元" REAL,
            "调仓事件ID" TEXT,
            "调仓日期" TEXT,
            "区间序号" INTEGER,
            "成分基金数" INTEGER,
            "权重和_百分比" REAL,
            "现金权重_百分比" REAL,
            "算法版本" TEXT NOT NULL,
            "质量等级" TEXT NOT NULL,
            "生成时间" TEXT NOT NULL,
            PRIMARY KEY ("统一策略ID", "交易日期", "算法版本")
        );

        CREATE INDEX IF NOT EXISTS "idx_策略模拟净值_日期"
        ON "策略模拟净值"("交易日期");

        CREATE INDEX IF NOT EXISTS "idx_策略模拟净值_渠道_日期"
        ON "策略模拟净值"("渠道ID", "交易日期");

        CREATE TABLE IF NOT EXISTS "策略模拟净值区间" (
            "统一策略ID" TEXT NOT NULL,
            "区间序号" INTEGER NOT NULL,
            "算法版本" TEXT NOT NULL,
            "渠道ID" TEXT NOT NULL,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "调仓事件ID" TEXT,
            "调仓日期" TEXT,
            "下一调仓日期" TEXT,
            "区间开始日期" TEXT,
            "区间结束日期" TEXT,
            "区间结束类型" TEXT,
            "区间是否有效" INTEGER NOT NULL DEFAULT 0,
            "是否纳入模拟" INTEGER NOT NULL,
            "质量等级" TEXT NOT NULL,
            "问题类型" TEXT,
            "问题说明" TEXT,
            "修复说明" TEXT,
            "明细行数" INTEGER,
            "正权重明细行数" INTEGER,
            "唯一基金数" INTEGER,
            "缺失代码数" INTEGER,
            "重复基金代码数" INTEGER,
            "重复权重行数" INTEGER,
            "权重和_百分比" REAL,
            "归一化倍数" REAL,
            "缺净值基金数" INTEGER,
            "起始覆盖不足基金数" INTEGER,
            "结束覆盖不足基金数" INTEGER,
            "区间交易日数" INTEGER,
            "缺失日收益填补点数" INTEGER,
            "区间收益率_百分比" REAL,
            "区间费前收益率_百分比" REAL,
            "区间投顾费_元" REAL,
            "区间费率拖累_百分点" REAL,
            "生成时间" TEXT NOT NULL,
            PRIMARY KEY ("统一策略ID", "区间序号", "算法版本")
        );

        CREATE INDEX IF NOT EXISTS "idx_策略模拟净值区间_质量"
        ON "策略模拟净值区间"("算法版本", "是否纳入模拟", "区间是否有效", "质量等级");

        CREATE TABLE IF NOT EXISTS "策略模拟净值质量" (
            "统一策略ID" TEXT NOT NULL,
            "算法版本" TEXT NOT NULL,
            "渠道ID" TEXT NOT NULL,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "投顾机构" TEXT,
            "原始调仓事件数" INTEGER NOT NULL,
            "折叠后调仓日期数" INTEGER NOT NULL,
            "同日重复事件数" INTEGER NOT NULL,
            "同日不同仓位日期数" INTEGER NOT NULL,
            "有效区间数" INTEGER NOT NULL,
            "无效区间数" INTEGER NOT NULL,
            "是否纳入模拟" INTEGER NOT NULL,
            "质量等级" TEXT NOT NULL,
            "首个问题日期" TEXT,
            "首个问题类型" TEXT,
            "问题说明" TEXT,
            "修复说明" TEXT,
            "模拟起始日期" TEXT,
            "模拟结束日期" TEXT,
            "模拟交易日数" INTEGER,
            "模拟区间年数" REAL,
            "初始资产_元" REAL,
            "投顾费率_年化_百分比" REAL,
            "缺失投顾费率按0处理" INTEGER,
            "模拟期末总资产_元" REAL,
            "模拟单位净值_期末" REAL,
            "模拟费前单位净值_期末" REAL,
            "模拟累计投顾费_元" REAL,
            "模拟投顾费拖累_百分点" REAL,
            "模拟累计收益率_百分比" REAL,
            "模拟费前累计收益率_百分比" REAL,
            "模拟年化收益率_百分比" REAL,
            "模拟最大回撤_百分比" REAL,
            "模拟波动率_年化_百分比" REAL,
            "模拟夏普_年化无风险0" REAL,
            "官方可比记录数" INTEGER,
            "官方起始日期" TEXT,
            "官方结束日期" TEXT,
            "官方区间收益率_百分比" REAL,
            "模拟同区间收益率_百分比" REAL,
            "模拟官方收益差_百分点" REAL,
            "模拟费前同区间收益率_百分比" REAL,
            "模拟费前官方收益差_百分点" REAL,
            "官方更接近口径" TEXT,
            "App展示对比口径" TEXT,
            "App展示同区间收益率_百分比" REAL,
            "App展示官方收益差_百分点" REAL,
            "官方对比区间规则" TEXT,
            "官方对比结束日期_调整后" TEXT,
            "生成时间" TEXT NOT NULL,
            PRIMARY KEY ("统一策略ID", "算法版本")
        );

        CREATE INDEX IF NOT EXISTS "idx_策略模拟净值质量_纳入"
        ON "策略模拟净值质量"("算法版本", "是否纳入模拟", "渠道ID");

        CREATE TABLE IF NOT EXISTS "策略模拟净值校验" (
            "统一策略ID" TEXT NOT NULL,
            "算法版本" TEXT NOT NULL,
            "校验项" TEXT NOT NULL,
            "校验状态" TEXT NOT NULL,
            "校验数值" REAL,
            "阈值" REAL,
            "问题说明" TEXT,
            "生成时间" TEXT NOT NULL,
            PRIMARY KEY ("统一策略ID", "算法版本", "校验项")
        );
        """
    )


def ensure_columns(conn: sqlite3.Connection, table: str, definitions: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    for column_name, column_sql in definitions.items():
        if column_name not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column_name}" {column_sql}')


def ensure_result_schema(conn: sqlite3.Connection) -> None:
    ensure_columns(
        conn,
        "策略模拟净值",
        {
            "模拟总资产_元": "REAL",
            "费前总资产_元": "REAL",
            "费前单位净值": "REAL",
            "费前日收益率_百分比": "REAL",
            "费前累计收益率_百分比": "REAL",
            "费前最大回撤_百分比": "REAL",
            "当日投顾费_元": "REAL",
            "累计投顾费_元": "REAL",
            "投顾费率_年化_百分比": "REAL",
            "初始资产_元": "REAL",
        },
    )
    ensure_columns(
        conn,
        "策略模拟净值区间",
        {
            "区间费前收益率_百分比": "REAL",
            "区间投顾费_元": "REAL",
            "区间费率拖累_百分点": "REAL",
        },
    )
    ensure_columns(
        conn,
        "策略模拟净值质量",
        {
            "初始资产_元": "REAL",
            "投顾费率_年化_百分比": "REAL",
            "缺失投顾费率按0处理": "INTEGER",
            "模拟期末总资产_元": "REAL",
            "模拟费前单位净值_期末": "REAL",
            "模拟累计投顾费_元": "REAL",
            "模拟投顾费拖累_百分点": "REAL",
            "模拟费前累计收益率_百分比": "REAL",
            "模拟费前同区间收益率_百分比": "REAL",
            "模拟费前官方收益差_百分点": "REAL",
            "官方更接近口径": "TEXT",
            "App展示对比口径": "TEXT",
            "App展示同区间收益率_百分比": "REAL",
            "App展示官方收益差_百分点": "REAL",
            "官方对比区间规则": "TEXT",
            "官方对比结束日期_调整后": "TEXT",
        },
    )


def clear_previous_results(conn: sqlite3.Connection, algorithm_version: str, channel_ids: set[str] | None = None) -> None:
    for table in ["策略模拟净值", "策略模拟净值区间", "策略模拟净值质量", "策略模拟净值校验"]:
        if channel_ids:
            placeholders = ",".join("?" for _ in channel_ids)
            if table == "策略模拟净值校验":
                conn.execute(
                    f'''
                    DELETE FROM "{table}"
                    WHERE "算法版本" = ?
                      AND "统一策略ID" IN (
                          SELECT "统一策略ID"
                          FROM "策略信息"
                          WHERE "渠道ID" IN ({placeholders})
                      )
                    ''',
                    [algorithm_version, *sorted(channel_ids)],
                )
            else:
                conn.execute(
                    f'DELETE FROM "{table}" WHERE "算法版本" = ? AND "渠道ID" IN ({placeholders})',
                    [algorithm_version, *sorted(channel_ids)],
                )
        else:
            conn.execute(f'DELETE FROM "{table}" WHERE "算法版本" = ?', [algorithm_version])


def load_fund_profiles(conn: sqlite3.Connection) -> dict[str, FundProfile]:
    rows = fetch_dicts(
        conn,
        """
        SELECT
            "基金代码" AS fund_code,
            MIN("交易日期") AS min_date,
            MAX("交易日期") AS max_date,
            COUNT(*) AS row_count
        FROM "基金日度净值"
        GROUP BY "基金代码"
        """,
    )
    profiles: dict[str, FundProfile] = {}
    for row in rows:
        code = norm_code(row["fund_code"])
        if not code:
            continue
        profiles[code] = FundProfile(
            fund_code=code,
            min_date=row["min_date"],
            max_date=row["max_date"],
            row_count=int(row["row_count"] or 0),
        )
    return profiles


def load_strategies(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT
            "统一策略ID",
            "渠道ID",
            "渠道策略ID",
            "策略名称",
            "投顾机构",
            "成立日期",
            "投顾费率"
        FROM "策略信息"
        """,
    )
    return {str(row["统一策略ID"]): row for row in rows}


def load_events(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT
            e."调仓事件ID",
            e."统一策略ID",
            e."渠道ID",
            e."渠道策略ID",
            e."调仓日期",
            e."披露日期",
            e."事件序号",
            e."事件时间",
            e."调仓标题",
            e."调仓原因",
            s."策略名称",
            s."投顾机构",
            s."策略状态"
        FROM "策略调仓事件" e
        LEFT JOIN "策略信息" s
          ON e."统一策略ID" = s."统一策略ID"
        WHERE e."调仓日期" IS NOT NULL AND TRIM(e."调仓日期") <> ''
        ORDER BY e."统一策略ID", e."调仓日期", COALESCE(e."事件序号", 0), COALESCE(e."事件时间", ''), e."调仓事件ID"
        """,
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["统一策略ID"])].append(row)
    return grouped


def load_details(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT
            "调仓事件ID",
            "基金代码",
            "基金名称",
            "调前权重_百分比",
            "调仓动作",
            "分组名称",
            "调后权重_百分比"
        FROM "策略调仓明细"
        """
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["调仓事件ID"])].append(row)
    return grouped


def evaluate_snapshot(
    event: dict[str, Any],
    details: list[dict[str, Any]],
    fund_profiles: dict[str, FundProfile],
) -> Snapshot:
    repair_result = repair_rebalance_details(event, details, fund_profiles)
    details = repair_result.details
    weights_pct: dict[str, float] = defaultdict(float)
    names: dict[str, str | None] = {}
    row_count_by_code: Counter[str] = Counter()
    raw_detail_count = len(details)
    positive_detail_count = 0
    missing_code_count = 0
    total_positive_weight = 0.0

    for detail in details:
        weight = to_float(detail.get("调后权重_百分比")) or 0.0
        if weight <= 0:
            continue
        positive_detail_count += 1
        total_positive_weight += weight
        code = norm_code(detail.get("基金代码"))
        if not code:
            missing_code_count += 1
            continue
        weights_pct[code] += weight
        row_count_by_code[code] += 1
        names.setdefault(code, norm_text(detail.get("基金名称")))

    duplicate_weight_row_count = sum(max(count - 1, 0) for count in row_count_by_code.values())
    duplicate_fund_count = sum(1 for count in row_count_by_code.values() if count > 1)
    basic_issues: list[str] = []
    repairs: list[str] = list(repair_result.repairs)

    if positive_detail_count == 0 and not repair_result.is_liquidation:
        basic_issues.append("无调后正权重")
    if missing_code_count > 0:
        basic_issues.append(f"调后正权重缺基金代码{missing_code_count}行")
    if not weights_pct and not repair_result.is_liquidation:
        basic_issues.append("无可识别基金代码")
    if repair_result.is_liquidation:
        pass
    elif abs(total_positive_weight - WEIGHT_SUM_TARGET) > WEIGHT_SUM_TOLERANCE:
        basic_issues.append(f"调后权重和不闭合({total_positive_weight:.4f}%)")
    elif abs(total_positive_weight - WEIGHT_SUM_TARGET) > WEIGHT_NORMALIZE_EPSILON:
        repairs.append(f"权重按{total_positive_weight:.4f}%归一化")
    if duplicate_weight_row_count > 0:
        repairs.append(f"重复基金代码合并{duplicate_weight_row_count}行")

    return Snapshot(
        event=event,
        weights_pct=dict(weights_pct),
        fund_names=names,
        raw_detail_count=raw_detail_count,
        positive_detail_count=positive_detail_count,
        missing_code_count=missing_code_count,
        duplicate_weight_row_count=duplicate_weight_row_count,
        duplicate_fund_count=duplicate_fund_count,
        weight_sum_pct=total_positive_weight,
        basic_issues=basic_issues,
        repairs=repairs,
    )


def event_sort_key(event: dict[str, Any]) -> tuple[int, str, str]:
    seq = int(event["事件序号"]) if event.get("事件序号") is not None else -1
    return (seq, str(event.get("事件时间") or ""), str(event["调仓事件ID"]))


def snapshot_score(snapshot: Snapshot) -> tuple[int, int, int, float, int, str, str]:
    event = snapshot.event
    return (
        1 if snapshot.is_basic_valid else 0,
        -len(snapshot.basic_issues),
        snapshot.positive_detail_count,
        -abs(snapshot.weight_sum_pct - WEIGHT_SUM_TARGET),
        int(event["事件序号"]) if event.get("事件序号") is not None else -1,
        str(event.get("事件时间") or ""),
        str(event["调仓事件ID"]),
    )


def collapse_same_day_events(
    events: list[dict[str, Any]],
    details_by_event: dict[str, list[dict[str, Any]]],
    fund_profiles: dict[str, FundProfile],
) -> tuple[list[Snapshot], int, int]:
    grouped_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped_by_date[str(event["调仓日期"])].append(event)

    snapshots: list[Snapshot] = []
    duplicate_event_count = 0
    variant_date_count = 0

    for rebalance_date in sorted(grouped_by_date):
        same_day = sorted(grouped_by_date[rebalance_date], key=event_sort_key)
        evaluated = [
            evaluate_snapshot(event, details_by_event.get(str(event["调仓事件ID"]), []), fund_profiles)
            for event in same_day
        ]
        chosen = max(evaluated, key=snapshot_score)
        snapshots.append(chosen)

        if len(same_day) > 1:
            duplicate_event_count += len(same_day) - 1
            signatures = {snapshot.signature for snapshot in evaluated if snapshot.signature}
            if len(signatures) > 1:
                variant_date_count += 1
                chosen.repairs.append(f"{rebalance_date}同日多事件存在不同仓位，已择优使用{chosen.event_id}")
            else:
                chosen.repairs.append(f"{rebalance_date}同日重复事件已折叠为{chosen.event_id}")

    return snapshots, duplicate_event_count, variant_date_count


def snapshot_common_latest_nav_date(
    snapshot: Snapshot,
    fund_profiles: dict[str, FundProfile],
    fallback_latest_nav_date: str,
) -> str:
    """Return the latest date covered by all funds in this snapshot."""
    latest_dates: list[str] = []
    for fund_code in snapshot.weights_pct:
        profile = fund_profiles.get(fund_code)
        if profile and profile.max_date:
            latest_dates.append(profile.max_date)
    return min(latest_dates) if latest_dates else fallback_latest_nav_date


def build_segments(
    strategy: dict[str, Any],
    snapshots: list[Snapshot],
    fund_profiles: dict[str, FundProfile],
    latest_nav_date: str,
) -> list[Segment]:
    segments: list[Segment] = []
    ordered = sorted(snapshots, key=lambda item: (item.rebalance_date, event_sort_key(item.event)))
    inception_date = norm_text(strategy.get("成立日期"))

    for idx, snapshot in enumerate(ordered):
        next_snapshot = ordered[idx + 1] if idx + 1 < len(ordered) else None
        if next_snapshot:
            end_date = next_snapshot.rebalance_date
            end_type = "下一调仓日"
        else:
            end_date = latest_nav_date
            end_type = "全库最新净值日"
        effective_start_date = max(snapshot.rebalance_date, inception_date) if inception_date else snapshot.rebalance_date
        simulation_relevant = effective_start_date < end_date
        exclusion_reason = None if simulation_relevant else ("成立日前历史" if inception_date else "无有效持有区间")
        issues = list(snapshot.basic_issues)
        repairs = list(snapshot.repairs)
        missing_nav_count = 0
        start_coverage_gap_count = 0
        end_coverage_gap_count = 0

        if snapshot.rebalance_date > end_date:
            issues.append("调仓日期晚于区间结束日期")

        if simulation_relevant:
            for fund_code in snapshot.weights_pct:
                profile = fund_profiles.get(fund_code)
                if not profile or not profile.min_date or not profile.max_date:
                    missing_nav_count += 1
                    continue
                if profile.min_date > effective_start_date:
                    start_coverage_gap_count += 1
                if profile.max_date < end_date:
                    end_coverage_gap_count += 1

            if missing_nav_count:
                issues.append(f"缺基金净值{missing_nav_count}只")
            if start_coverage_gap_count:
                repairs.append(f"调仓日起始净值覆盖不足{start_coverage_gap_count}只，按起始收益因子1处理")
            if end_coverage_gap_count:
                repairs.append(f"区间结束净值提前结束{end_coverage_gap_count}只，之后沿用最后净值")

        normalized_weights: dict[str, float] = {}
        if simulation_relevant and not issues:
            weight_sum = sum(snapshot.weights_pct.values())
            if weight_sum <= 0 and snapshot.weight_sum_pct > 0:
                issues.append("可识别基金权重和为0")
            elif weight_sum > 0:
                normalized_weights = {code: weight / weight_sum for code, weight in snapshot.weights_pct.items()}

        segments.append(
            Segment(
                strategy=strategy,
                snapshot=snapshot,
                seq=idx + 1,
                start_date=snapshot.rebalance_date,
                effective_start_date=effective_start_date,
                end_date=end_date,
                end_type=end_type,
                next_rebalance_date=next_snapshot.rebalance_date if next_snapshot else None,
                is_valid=not issues,
                simulation_relevant=simulation_relevant,
                exclusion_reason=exclusion_reason,
                issues=issues,
                repairs=repairs,
                missing_nav_count=missing_nav_count,
                start_coverage_gap_count=start_coverage_gap_count,
                end_coverage_gap_count=end_coverage_gap_count,
                normalized_weights=normalized_weights,
            )
        )
    return segments


def load_dividend_amounts(
    conn: sqlite3.Connection,
    fund_codes: list[str],
    start_date: str,
    end_date: str,
) -> dict[tuple[str, str], float]:
    if not fund_codes:
        return {}
    rows = fetch_dicts(
        conn,
        f"""
        SELECT
            "基金代码",
            COALESCE("除息日", "权益登记日") AS dividend_date,
            "每份分红"
        FROM "基金分红送配"
        WHERE "基金代码" IN ({sql_in_placeholders(fund_codes)})
          AND COALESCE("除息日", "权益登记日") > ?
          AND COALESCE("除息日", "权益登记日") <= ?
        """,
        [*fund_codes, start_date, end_date],
    )
    result: dict[tuple[str, str], float] = defaultdict(float)
    for row in rows:
        code = norm_code(row["基金代码"])
        dividend_date = norm_text(row["dividend_date"])
        amount = parse_cash_dividend(row["每份分红"])
        if code and dividend_date and amount is not None:
            result[(code, dividend_date)] += amount
    return dict(result)


def fetch_fund_return_map(
    conn: sqlite3.Connection,
    fund_codes: list[str],
    start_date: str,
    end_date: str,
) -> tuple[dict[str, dict[str, float]], Counter[str]]:
    if not fund_codes or start_date >= end_date:
        return {}, Counter()

    rows = fetch_dicts(
        conn,
        f"""
        SELECT
            "基金代码",
            "交易日期",
            "单位净值",
            "累计净值",
            "日收益率_百分比",
            "每万份收益",
            "是否货币基金"
        FROM "基金日度净值"
        WHERE "基金代码" IN ({sql_in_placeholders(fund_codes)})
          AND "交易日期" >= ?
          AND "交易日期" <= ?
        ORDER BY "基金代码", "交易日期"
        """,
        [*fund_codes, start_date, end_date],
    )
    dividend_amounts = load_dividend_amounts(conn, fund_codes, start_date, end_date)

    rows_by_fund: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        code = norm_code(row["基金代码"])
        if code:
            rows_by_fund[code].append(row)

    returns_by_date: dict[str, dict[str, float]] = defaultdict(dict)
    source_counts: Counter[str] = Counter()

    for code, fund_rows in rows_by_fund.items():
        prev_accum: float | None = None
        prev_unit: float | None = None
        for row in fund_rows:
            trade_date = str(row["交易日期"])
            unit_nav = to_float(row["单位净值"])
            accum_nav = to_float(row["累计净值"])
            daily_pct = to_float(row["日收益率_百分比"])
            per_10k = to_float(row["每万份收益"])
            is_money = int(row["是否货币基金"] or 0) == 1
            dividend = dividend_amounts.get((code, trade_date), 0.0)

            daily_return: float | None = None
            if is_money and per_10k is not None:
                daily_return = per_10k / 10000.0
                source_counts["每万份收益推导"] += 1
            elif trade_date > start_date and prev_unit is None and prev_accum is None:
                source_counts["起始净值缺失按1"] += 1
            elif daily_pct is not None:
                provided_return = daily_pct / 100.0
                unit_return = None
                if unit_nav is not None and prev_unit not in (None, 0):
                    unit_return = (unit_nav + dividend) / prev_unit - 1.0
                if unit_return is not None:
                    diff_pct = abs((unit_return - provided_return) * 100.0)
                    if diff_pct > RETURN_SOURCE_TOLERANCE_PCT:
                        daily_return = provided_return
                        source_counts["日收益率_覆盖复权异常"] += 1
                    else:
                        daily_return = unit_return
                        source_counts["单位净值加分红推导"] += 1
                else:
                    daily_return = provided_return
                    source_counts["日收益率"] += 1
            elif unit_nav is not None and prev_unit not in (None, 0):
                daily_return = (unit_nav + dividend) / prev_unit - 1.0
                source_counts["单位净值加分红推导"] += 1
            elif accum_nav is not None and prev_accum not in (None, 0):
                daily_return = accum_nav / prev_accum - 1.0
                source_counts["累计净值推导"] += 1
            else:
                source_counts["无法推导"] += 1

            if trade_date > start_date and daily_return is not None:
                returns_by_date[trade_date][code] = daily_return

            if accum_nav is not None:
                prev_accum = accum_nav
            if unit_nav is not None:
                prev_unit = unit_nav

    return dict(returns_by_date), source_counts


def insert_nav_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO "策略模拟净值" (
            "统一策略ID", "渠道ID", "渠道策略ID", "策略名称", "交易日期",
            "模拟单位净值", "日收益率_百分比", "累计收益率_百分比", "最大回撤_百分比",
            "费前单位净值", "费前日收益率_百分比", "费前累计收益率_百分比", "费前最大回撤_百分比",
            "模拟总资产_元", "费前总资产_元", "当日投顾费_元", "累计投顾费_元", "投顾费率_年化_百分比", "初始资产_元",
            "调仓事件ID", "调仓日期", "区间序号", "成分基金数", "权重和_百分比",
            "现金权重_百分比", "算法版本", "质量等级", "生成时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def insert_segment_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO "策略模拟净值区间" (
            "统一策略ID", "区间序号", "算法版本", "渠道ID", "渠道策略ID", "策略名称",
            "调仓事件ID", "调仓日期", "下一调仓日期", "区间开始日期", "区间结束日期",
            "区间结束类型", "区间是否有效", "是否纳入模拟", "质量等级", "问题类型", "问题说明", "修复说明",
            "明细行数", "正权重明细行数", "唯一基金数", "缺失代码数", "重复基金代码数", "重复权重行数",
            "权重和_百分比", "归一化倍数", "缺净值基金数", "起始覆盖不足基金数", "结束覆盖不足基金数",
            "区间交易日数", "缺失日收益填补点数", "区间收益率_百分比", "区间费前收益率_百分比", "区间投顾费_元", "区间费率拖累_百分点", "生成时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def insert_quality_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO "策略模拟净值质量" (
            "统一策略ID", "算法版本", "渠道ID", "渠道策略ID", "策略名称", "投顾机构",
            "原始调仓事件数", "折叠后调仓日期数", "同日重复事件数", "同日不同仓位日期数",
            "有效区间数", "无效区间数", "是否纳入模拟", "质量等级", "首个问题日期",
            "首个问题类型", "问题说明", "修复说明", "模拟起始日期", "模拟结束日期",
            "模拟交易日数", "模拟区间年数", "初始资产_元", "投顾费率_年化_百分比", "缺失投顾费率按0处理",
            "模拟期末总资产_元", "模拟单位净值_期末", "模拟费前单位净值_期末", "模拟累计投顾费_元", "模拟投顾费拖累_百分点",
            "模拟累计收益率_百分比", "模拟费前累计收益率_百分比", "模拟年化收益率_百分比", "模拟最大回撤_百分比",
            "模拟波动率_年化_百分比", "模拟夏普_年化无风险0", "官方可比记录数", "官方起始日期", "官方结束日期",
            "官方区间收益率_百分比", "模拟同区间收益率_百分比", "模拟官方收益差_百分点",
            "模拟费前同区间收益率_百分比", "模拟费前官方收益差_百分点", "官方更接近口径",
            "App展示对比口径", "App展示同区间收益率_百分比", "App展示官方收益差_百分点",
            "官方对比区间规则", "官方对比结束日期_调整后", "生成时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def insert_check_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO "策略模拟净值校验" (
            "统一策略ID", "算法版本", "校验项", "校验状态", "校验数值", "阈值", "问题说明", "生成时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def compare_with_official(
    conn: sqlite3.Connection,
    strategy_id: str,
    simulated_nav_by_date: dict[str, float],
    simulated_gross_nav_by_date: dict[str, float],
    sim_start_date: str | None,
    sim_end_date: str | None,
    comparison_rule: str = "全模拟区间",
    app_compare_end_date: str | None = None,
) -> dict[str, Any]:
    effective_end_date = min(sim_end_date, app_compare_end_date) if sim_end_date and app_compare_end_date else sim_end_date

    def empty_result(count: int = 0, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        return {
            "official_count": count,
            "official_start_date": start_date,
            "official_end_date": end_date,
            "official_return_pct": None,
            "sim_same_interval_return_pct": None,
            "diff_pct": None,
            "gross_sim_same_interval_return_pct": None,
            "gross_diff_pct": None,
            "closest_basis": None,
            "app_display_basis": "费前",
            "app_display_return_pct": None,
            "app_display_diff_pct": None,
            "comparison_rule": comparison_rule,
            "adjusted_end_date": effective_end_date,
        }

    if not sim_start_date or not sim_end_date or not simulated_nav_by_date:
        return empty_result()

    rows = fetch_dicts(
        conn,
        """
        SELECT "交易日期", "单位净值", "累计收益率_百分比"
        FROM "策略日度业绩"
        WHERE "统一策略ID" = ?
          AND "交易日期" >= ?
          AND "交易日期" <= ?
          AND COALESCE("业绩区段类型", '') <> 'public_quote'
        ORDER BY "交易日期"
        """,
        [strategy_id, sim_start_date, effective_end_date],
    )
    common = [row for row in rows if str(row["交易日期"]) in simulated_nav_by_date]
    if len(common) < 2:
        return empty_result(
            len(common),
            str(common[0]["交易日期"]) if common else None,
            str(common[-1]["交易日期"]) if common else None,
        )

    start = common[0]
    end = common[-1]
    start_date = str(start["交易日期"])
    end_date = str(end["交易日期"])
    start_nav = to_float(start["单位净值"])
    end_nav = to_float(end["单位净值"])

    if start_nav not in (None, 0) and end_nav is not None:
        official_return_pct = (end_nav / start_nav - 1.0) * 100.0
    else:
        start_cum = to_float(start["累计收益率_百分比"])
        end_cum = to_float(end["累计收益率_百分比"])
        if start_cum is None or end_cum is None or (1.0 + start_cum / 100.0) == 0:
            official_return_pct = None
        else:
            official_return_pct = ((1.0 + end_cum / 100.0) / (1.0 + start_cum / 100.0) - 1.0) * 100.0

    sim_start_nav = simulated_nav_by_date[start_date]
    sim_end_nav = simulated_nav_by_date[end_date]
    sim_return_pct = (sim_end_nav / sim_start_nav - 1.0) * 100.0 if sim_start_nav else None
    diff_pct = sim_return_pct - official_return_pct if official_return_pct is not None and sim_return_pct is not None else None
    gross_sim_return_pct = None
    gross_diff_pct = None
    if start_date in simulated_gross_nav_by_date and end_date in simulated_gross_nav_by_date:
        gross_start_nav = simulated_gross_nav_by_date[start_date]
        gross_end_nav = simulated_gross_nav_by_date[end_date]
        gross_sim_return_pct = (gross_end_nav / gross_start_nav - 1.0) * 100.0 if gross_start_nav else None
        if official_return_pct is not None and gross_sim_return_pct is not None:
            gross_diff_pct = gross_sim_return_pct - official_return_pct

    closest_basis = None
    if diff_pct is not None and gross_diff_pct is not None:
        net_abs = abs(diff_pct)
        gross_abs = abs(gross_diff_pct)
        if abs(net_abs - gross_abs) <= 0.05:
            closest_basis = "费前费后接近"
        elif net_abs < gross_abs:
            closest_basis = "费后"
        else:
            closest_basis = "费前"

    return {
        "official_count": len(common),
        "official_start_date": start_date,
        "official_end_date": end_date,
        "official_return_pct": official_return_pct,
        "sim_same_interval_return_pct": sim_return_pct,
        "diff_pct": diff_pct,
        "gross_sim_same_interval_return_pct": gross_sim_return_pct,
        "gross_diff_pct": gross_diff_pct,
        "closest_basis": closest_basis,
        "app_display_basis": "费前",
        "app_display_return_pct": gross_sim_return_pct,
        "app_display_diff_pct": gross_diff_pct,
        "comparison_rule": comparison_rule,
        "adjusted_end_date": effective_end_date,
    }


def has_current_fund_holding(conn: sqlite3.Connection, strategy_id: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM "策略当前持仓"
        WHERE "统一策略ID" = ?
          AND COALESCE("基金权重_百分比", 0) > 0
        """,
        [strategy_id],
    ).fetchone()
    return bool(row and int(row[0] or 0) > 0)


def app_official_comparison_window(
    conn: sqlite3.Connection,
    strategy: dict[str, Any],
    segments: list[Segment],
    default_end_date: str | None,
) -> tuple[str, str | None]:
    """Use the app-comparable operation period for target-profit/liquidation products."""
    liquidation_dates = [
        segment.effective_start_date
        for segment in segments
        if segment.simulation_relevant
        and segment.is_valid
        and not segment.normalized_weights
        and any("清盘/止盈空仓" in note for note in segment.repairs)
    ]
    if not liquidation_dates:
        strategy_name = str(strategy.get("策略名称") or "")
        strategy_id = str(strategy.get("统一策略ID") or "")
        target_like = any(keyword in strategy_name for keyword in TARGET_PROFIT_KEYWORDS)
        if not target_like or has_current_fund_holding(conn, strategy_id):
            return "全模拟区间", default_end_date
        last_weighted_start = max(
            (
                segment.effective_start_date
                for segment in segments
                if segment.simulation_relevant and segment.is_valid and segment.normalized_weights
            ),
            default=None,
        )
        if last_weighted_start and default_end_date and last_weighted_start < default_end_date:
            return "目标盈/小目标无当前持仓，仅比较实际运作期", last_weighted_start
        return "全模拟区间", default_end_date
    liquidation_date = min(liquidation_dates)
    if default_end_date and liquidation_date > default_end_date:
        return "全模拟区间", default_end_date
    return "清盘/止盈策略仅比较实际运作期", liquidation_date


def simulate_strategy(
    conn: sqlite3.Connection,
    strategy: dict[str, Any],
    segments: list[Segment],
    algorithm_version: str,
    generated_at: str,
    initial_asset: float,
) -> tuple[dict[str, Any], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    strategy_id = str(strategy["统一策略ID"])
    channel_id = str(strategy["渠道ID"])
    channel_strategy_id = strategy.get("渠道策略ID")
    strategy_name = strategy.get("策略名称")
    relevant_segments = [segment for segment in segments if segment.simulation_relevant]

    fee_rate = parse_fee_rate(strategy.get("投顾费率"))
    fee_missing_assumed_zero = fee_rate is None
    if fee_rate is None:
        fee_rate = 0.0

    if fee_missing_assumed_zero:
        for segment in relevant_segments:
            if segment.is_valid:
                segment.repairs.append("缺投顾费率按0处理")

    if not relevant_segments or any(not segment.is_valid for segment in relevant_segments):
        first_bad = next((segment for segment in relevant_segments if not segment.is_valid), None)
        repair_notes = sorted({note for segment in relevant_segments for note in segment.repairs})
        return (
            {
                "included": False,
                "quality_grade": "不可回放",
                "first_issue_date": first_bad.effective_start_date if first_bad else None,
                "first_issue_type": first_bad.issues[0] if first_bad and first_bad.issues else "成立日后无可用调仓区间",
                "issue_desc": "；".join(first_bad.issues) if first_bad else "成立日后无可用调仓区间",
                "repair_desc": "；".join(repair_notes) if repair_notes else None,
                "nav_stats": None,
                "official": None,
                "fee_rate_pct": fee_rate * 100.0,
                "fee_missing_assumed_zero": int(fee_missing_assumed_zero),
            },
            [],
            [],
        )

    nav_rows: list[tuple[Any, ...]] = []
    check_rows: list[tuple[Any, ...]] = []
    validation_errors: list[str] = []
    simulated_nav_by_date: dict[str, float] = {}
    simulated_gross_nav_by_date: dict[str, float] = {}
    daily_returns: list[float] = []
    gross_daily_returns: list[float] = []

    current_asset = initial_asset
    current_gross_asset = initial_asset
    cumulative_fee = 0.0
    current_nav = 1.0
    current_gross_nav = 1.0
    peak_nav = 1.0
    gross_peak_nav = 1.0
    running_max_drawdown_pct = 0.0
    gross_running_max_drawdown_pct = 0.0
    last_nav = 1.0
    last_gross_nav = 1.0
    first_segment = relevant_segments[0]
    sim_start_date = first_segment.effective_start_date
    sim_end_date = first_segment.end_date
    previous_date: str | None = None
    max_return_consistency_error = 0.0

    nav_rows.append(
        (
            strategy_id,
            channel_id,
            channel_strategy_id,
            strategy_name,
            sim_start_date,
            round(current_nav, 8),
            0.0,
            0.0,
            0.0,
            round(current_gross_nav, 8),
            0.0,
            0.0,
            0.0,
            round(current_asset, 4),
            round(current_gross_asset, 4),
            0.0,
            0.0,
            round(fee_rate * 100.0, 8),
            round(initial_asset, 4),
            first_segment.snapshot.event_id,
            first_segment.effective_start_date,
            first_segment.seq,
            len(first_segment.normalized_weights),
            round(first_segment.snapshot.weight_sum_pct, 6),
            round(max(0.0, WEIGHT_SUM_TARGET - first_segment.snapshot.weight_sum_pct), 6),
            algorithm_version,
            first_segment.quality_grade,
            generated_at,
        )
    )
    simulated_nav_by_date[sim_start_date] = current_nav
    simulated_gross_nav_by_date[sim_start_date] = current_gross_nav
    previous_date = sim_start_date

    for segment in relevant_segments:
        segment_start_nav = current_nav
        segment_start_gross_nav = current_gross_nav
        segment_fee_start = cumulative_fee

        cash_only_segment = not segment.normalized_weights
        gross_component_values = {code: current_gross_asset * weight for code, weight in segment.normalized_weights.items()}
        net_component_values = {code: current_asset * weight for code, weight in segment.normalized_weights.items()}
        returns_by_date: dict[str, dict[str, float]] = {}
        if not cash_only_segment:
            returns_by_date, _source_counts = fetch_fund_return_map(
                conn,
                list(segment.normalized_weights),
                segment.effective_start_date,
                segment.end_date,
            )
        all_dates = sorted(date_value for date_value in returns_by_date if segment.effective_start_date < date_value <= segment.end_date)
        if segment.end_date not in all_dates and segment.effective_start_date < segment.end_date:
            all_dates.append(segment.end_date)
            all_dates.sort()

        segment.date_count = len(all_dates)
        missing_return_points = 0
        for trade_date in all_dates:
            if cash_only_segment:
                current_gross_asset = current_gross_asset
                net_before_fee = current_asset
            else:
                date_returns = returns_by_date.get(trade_date, {})
                for fund_code in gross_component_values:
                    fund_return = date_returns.get(fund_code)
                    if fund_return is None:
                        missing_return_points += 1
                        fund_return = 0.0
                    gross_component_values[fund_code] *= 1.0 + fund_return
                    net_component_values[fund_code] *= 1.0 + fund_return

                current_gross_asset = sum(gross_component_values.values())
                net_before_fee = sum(net_component_values.values())
            gap_days = days_between(previous_date, trade_date) if previous_date else 0
            day_fee = 0.0
            if not cash_only_segment and net_before_fee > 0 and fee_rate > 0 and gap_days > 0:
                fee_multiplier = max(0.0, 1.0 - fee_rate / FEE_ACCRUAL_DAYS) ** gap_days
                current_asset = net_before_fee * fee_multiplier
                day_fee = net_before_fee - current_asset
                scale = current_asset / net_before_fee
                for fund_code in net_component_values:
                    net_component_values[fund_code] *= scale
            else:
                current_asset = net_before_fee

            cumulative_fee += day_fee
            current_nav = current_asset / initial_asset if initial_asset else 0.0
            current_gross_nav = current_gross_asset / initial_asset if initial_asset else 0.0
            if current_nav <= 0:
                validation_errors.append(f"{trade_date}模拟净值非正")
            daily_return = current_nav / last_nav - 1.0 if last_nav else 0.0
            gross_daily_return = current_gross_nav / last_gross_nav - 1.0 if last_gross_nav else 0.0
            daily_returns.append(daily_return)
            gross_daily_returns.append(gross_daily_return)
            peak_nav = max(peak_nav, current_nav)
            gross_peak_nav = max(gross_peak_nav, current_gross_nav)
            drawdown_pct = (1.0 - current_nav / peak_nav) * 100.0 if peak_nav else 0.0
            gross_drawdown_pct = (1.0 - current_gross_nav / gross_peak_nav) * 100.0 if gross_peak_nav else 0.0
            running_max_drawdown_pct = max(running_max_drawdown_pct, drawdown_pct)
            gross_running_max_drawdown_pct = max(gross_running_max_drawdown_pct, gross_drawdown_pct)
            cumulative_pct = (current_nav - 1.0) * 100.0
            gross_cumulative_pct = (current_gross_nav - 1.0) * 100.0

            if previous_date is not None and trade_date <= previous_date:
                validation_errors.append(f"{trade_date}交易日期未递增")
            expected_return = current_nav / last_nav - 1.0 if last_nav else 0.0
            max_return_consistency_error = max(max_return_consistency_error, abs(expected_return - daily_return))

            nav_rows.append(
                (
                    strategy_id,
                    channel_id,
                    channel_strategy_id,
                    strategy_name,
                    trade_date,
                    round(current_nav, 8),
                    round(daily_return * 100.0, 8),
                    round(cumulative_pct, 8),
                    round(running_max_drawdown_pct, 8),
                    round(current_gross_nav, 8),
                    round(gross_daily_return * 100.0, 8),
                    round(gross_cumulative_pct, 8),
                    round(gross_running_max_drawdown_pct, 8),
                    round(current_asset, 4),
                    round(current_gross_asset, 4),
                    round(day_fee, 8),
                    round(cumulative_fee, 8),
                    round(fee_rate * 100.0, 8),
                    round(initial_asset, 4),
                    segment.snapshot.event_id,
                    segment.effective_start_date,
                    segment.seq,
                    len(segment.normalized_weights),
                    round(segment.snapshot.weight_sum_pct, 6),
                    round(max(0.0, WEIGHT_SUM_TARGET - segment.snapshot.weight_sum_pct), 6),
                    algorithm_version,
                    segment.quality_grade,
                    generated_at,
                )
            )
            simulated_nav_by_date[trade_date] = current_nav
            simulated_gross_nav_by_date[trade_date] = current_gross_nav
            previous_date = trade_date
            last_nav = current_nav
            last_gross_nav = current_gross_nav

        segment.carried_missing_return_points = missing_return_points
        segment.interval_return_pct = (current_nav / segment_start_nav - 1.0) * 100.0 if segment_start_nav else None
        segment.interval_gross_return_pct = (current_gross_nav / segment_start_gross_nav - 1.0) * 100.0 if segment_start_gross_nav else None
        segment.interval_fee_amount = cumulative_fee - segment_fee_start
        if segment.interval_return_pct is not None and segment.interval_gross_return_pct is not None:
            segment.interval_fee_drag_pct = segment.interval_gross_return_pct - segment.interval_return_pct
        sim_end_date = max(sim_end_date, segment.end_date)

    elapsed_days = max(days_between(sim_start_date, sim_end_date), 0)
    elapsed_years = elapsed_days / 365.25 if elapsed_days else None
    cumulative_return = current_nav - 1.0
    gross_cumulative_return = current_gross_nav - 1.0
    annualized_return = (current_nav ** (1.0 / elapsed_years) - 1.0) if elapsed_years and elapsed_years > 0 and current_nav > 0 else None
    volatility = statistics.pstdev(daily_returns) * math.sqrt(ANNUAL_TRADING_DAYS) if len(daily_returns) >= 2 else None
    sharpe = annualized_return / volatility if annualized_return is not None and volatility not in (None, 0) else None
    max_drawdown = max((to_float(row[8]) or 0.0) for row in nav_rows)
    fee_drag_pct = (current_gross_nav - current_nav) * 100.0

    comparison_rule, app_compare_end_date = app_official_comparison_window(conn, strategy, relevant_segments, sim_end_date)
    official = compare_with_official(
        conn,
        strategy_id,
        simulated_nav_by_date,
        simulated_gross_nav_by_date,
        sim_start_date,
        sim_end_date,
        comparison_rule=comparison_rule,
        app_compare_end_date=app_compare_end_date,
    )

    first_nav_ok = abs((simulated_nav_by_date.get(sim_start_date) or 0.0) - 1.0) <= 1e-10
    repair_notes = sorted({note for segment in relevant_segments for note in segment.repairs})
    quality_grade = "完整_已修复" if repair_notes else "完整"

    check_rows.extend(
        [
            (
                strategy_id,
                algorithm_version,
                "首日净值为1",
                "通过" if first_nav_ok else "失败",
                round_or_none(simulated_nav_by_date.get(sim_start_date), 10),
                1.0,
                None if first_nav_ok else "首日模拟净值不等于1",
                generated_at,
            ),
            (
                strategy_id,
                algorithm_version,
                "日收益与净值变化一致",
                "通过" if max_return_consistency_error <= 1e-10 else "失败",
                round(max_return_consistency_error, 12),
                1e-10,
                None if max_return_consistency_error <= 1e-10 else "日收益率与单位净值相邻变化不一致",
                generated_at,
            ),
            (
                strategy_id,
                algorithm_version,
                "单位净值均为正",
                "通过" if not validation_errors else "失败",
                float(len(validation_errors)),
                0.0,
                "；".join(validation_errors[:5]) if validation_errors else None,
                generated_at,
            ),
            (
                strategy_id,
                algorithm_version,
                "交易日期无重复",
                "通过" if len(simulated_nav_by_date) == len(nav_rows) else "失败",
                float(len(nav_rows) - len(simulated_nav_by_date)),
                0.0,
                None if len(simulated_nav_by_date) == len(nav_rows) else "存在重复交易日期",
                generated_at,
            ),
            (
                strategy_id,
                algorithm_version,
                "累计投顾费非负",
                "通过" if cumulative_fee >= -1e-8 else "失败",
                round(cumulative_fee, 8),
                0.0,
                None if cumulative_fee >= -1e-8 else "累计投顾费为负",
                generated_at,
            ),
            (
                strategy_id,
                algorithm_version,
                "费前净值不低于费后净值",
                "通过" if current_gross_nav + 1e-10 >= current_nav else "失败",
                round((current_gross_nav - current_nav) * 100.0, 8),
                0.0,
                None if current_gross_nav + 1e-10 >= current_nav else "费后净值高于费前净值",
                generated_at,
            ),
        ]
    )

    nav_stats = {
        "start_date": sim_start_date,
        "end_date": sim_end_date,
        "row_count": len(nav_rows),
        "elapsed_years": elapsed_years,
        "ending_asset": current_asset,
        "ending_nav": current_nav,
        "gross_ending_nav": current_gross_nav,
        "cumulative_fee": cumulative_fee,
        "fee_drag_pct": fee_drag_pct,
        "cumulative_return_pct": cumulative_return * 100.0,
        "gross_cumulative_return_pct": gross_cumulative_return * 100.0,
        "annualized_return_pct": annualized_return * 100.0 if annualized_return is not None else None,
        "max_drawdown_pct": max_drawdown,
        "volatility_pct": volatility * 100.0 if volatility is not None else None,
        "sharpe": sharpe,
    }
    return (
        {
            "included": True,
            "quality_grade": quality_grade,
            "first_issue_date": None,
            "first_issue_type": None,
            "issue_desc": None,
            "repair_desc": "；".join(repair_notes) if repair_notes else None,
            "nav_stats": nav_stats,
            "official": official,
            "fee_rate_pct": fee_rate * 100.0,
            "fee_missing_assumed_zero": int(fee_missing_assumed_zero),
        },
        nav_rows,
        check_rows,
    )


def segment_to_row(
    segment: Segment,
    strategy_included: bool,
    algorithm_version: str,
    generated_at: str,
) -> tuple[Any, ...]:
    snapshot = segment.snapshot
    strategy = segment.strategy
    weight_sum = snapshot.weight_sum_pct
    normalize_factor = WEIGHT_SUM_TARGET / weight_sum if weight_sum else None
    return (
        strategy["统一策略ID"],
        segment.seq,
        algorithm_version,
        strategy["渠道ID"],
        strategy.get("渠道策略ID"),
        strategy.get("策略名称"),
        snapshot.event_id,
        segment.start_date,
        segment.next_rebalance_date,
        segment.effective_start_date,
        segment.end_date,
        segment.end_type,
        1 if segment.is_valid else 0,
        1 if strategy_included and segment.simulation_relevant and segment.is_valid else 0,
        segment.quality_grade,
        segment.issues[0] if segment.issues else segment.exclusion_reason,
        "；".join(segment.issues) if segment.issues else segment.exclusion_reason,
        "；".join(segment.repairs) if segment.repairs else None,
        snapshot.raw_detail_count,
        snapshot.positive_detail_count,
        len(snapshot.weights_pct),
        snapshot.missing_code_count,
        snapshot.duplicate_fund_count,
        snapshot.duplicate_weight_row_count,
        round(weight_sum, 6),
        round_or_none(normalize_factor, 10),
        segment.missing_nav_count,
        segment.start_coverage_gap_count,
        segment.end_coverage_gap_count,
        segment.date_count,
        segment.carried_missing_return_points,
        round_or_none(segment.interval_return_pct, 8),
        round_or_none(segment.interval_gross_return_pct, 8),
        round_or_none(segment.interval_fee_amount, 8),
        round_or_none(segment.interval_fee_drag_pct, 8),
        generated_at,
    )


def quality_to_row(
    strategy: dict[str, Any],
    algorithm_version: str,
    generated_at: str,
    initial_asset: float,
    original_event_count: int,
    collapsed_event_count: int,
    duplicate_event_count: int,
    variant_date_count: int,
    segments: list[Segment],
    simulation_summary: dict[str, Any],
) -> tuple[Any, ...]:
    valid_count = sum(1 for segment in segments if segment.simulation_relevant and segment.is_valid)
    invalid_count = sum(1 for segment in segments if segment.simulation_relevant and not segment.is_valid)
    nav_stats = simulation_summary.get("nav_stats") or {}
    official = simulation_summary.get("official") or {}
    return (
        strategy["统一策略ID"],
        algorithm_version,
        strategy["渠道ID"],
        strategy.get("渠道策略ID"),
        strategy.get("策略名称"),
        strategy.get("投顾机构"),
        original_event_count,
        collapsed_event_count,
        duplicate_event_count,
        variant_date_count,
        valid_count,
        invalid_count,
        1 if simulation_summary["included"] else 0,
        simulation_summary["quality_grade"],
        simulation_summary.get("first_issue_date"),
        simulation_summary.get("first_issue_type"),
        simulation_summary.get("issue_desc"),
        simulation_summary.get("repair_desc"),
        nav_stats.get("start_date"),
        nav_stats.get("end_date"),
        nav_stats.get("row_count"),
        round_or_none(nav_stats.get("elapsed_years"), 6),
        round_or_none(initial_asset, 4),
        round_or_none(simulation_summary.get("fee_rate_pct"), 8),
        int(simulation_summary.get("fee_missing_assumed_zero") or 0),
        round_or_none(nav_stats.get("ending_asset"), 4),
        round_or_none(nav_stats.get("ending_nav"), 8),
        round_or_none(nav_stats.get("gross_ending_nav"), 8),
        round_or_none(nav_stats.get("cumulative_fee"), 8),
        round_or_none(nav_stats.get("fee_drag_pct"), 6),
        round_or_none(nav_stats.get("cumulative_return_pct"), 6),
        round_or_none(nav_stats.get("gross_cumulative_return_pct"), 6),
        round_or_none(nav_stats.get("annualized_return_pct"), 6),
        round_or_none(nav_stats.get("max_drawdown_pct"), 6),
        round_or_none(nav_stats.get("volatility_pct"), 6),
        round_or_none(nav_stats.get("sharpe"), 6),
        official.get("official_count"),
        official.get("official_start_date"),
        official.get("official_end_date"),
        round_or_none(official.get("official_return_pct"), 6),
        round_or_none(official.get("sim_same_interval_return_pct"), 6),
        round_or_none(official.get("diff_pct"), 6),
        round_or_none(official.get("gross_sim_same_interval_return_pct"), 6),
        round_or_none(official.get("gross_diff_pct"), 6),
        official.get("closest_basis"),
        official.get("app_display_basis"),
        round_or_none(official.get("app_display_return_pct"), 6),
        round_or_none(official.get("app_display_diff_pct"), 6),
        official.get("comparison_rule"),
        official.get("adjusted_end_date"),
        generated_at,
    )


def table_count(conn: sqlite3.Connection, table: str, algorithm_version: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "算法版本" = ?', [algorithm_version]).fetchone()[0])


def build_report(
    output_dir: Path,
    conn: sqlite3.Connection,
    algorithm_version: str,
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    quality_rows = fetch_dicts(
        conn,
        """
        SELECT *
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ?
        ORDER BY "是否纳入模拟" DESC, "渠道ID", "统一策略ID"
        """,
        [algorithm_version],
    )
    issue_rows = fetch_dicts(
        conn,
        """
        SELECT "首个问题类型" AS issue_type, COUNT(*) AS count
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ? AND "是否纳入模拟" = 0
        GROUP BY "首个问题类型"
        ORDER BY count DESC
        """,
        [algorithm_version],
    )
    channel_rows = fetch_dicts(
        conn,
        """
        SELECT
            "渠道ID",
            COUNT(*) AS strategy_count,
            SUM("是否纳入模拟") AS included_count,
            ROUND(SUM(COALESCE("模拟累计投顾费_元", 0)), 2) AS total_fee_amount,
            ROUND(AVG(COALESCE("投顾费率_年化_百分比", 0)), 6) AS avg_fee_rate_pct
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ?
        GROUP BY "渠道ID"
        ORDER BY "渠道ID"
        """,
        [algorithm_version],
    )
    check_rows = fetch_dicts(
        conn,
        """
        SELECT "校验项", "校验状态", COUNT(*) AS count
        FROM "策略模拟净值校验"
        WHERE "算法版本" = ?
        GROUP BY "校验项", "校验状态"
        ORDER BY "校验项", "校验状态"
        """,
        [algorithm_version],
    )

    write_csv(output_dir / "strategy_nav_quality.csv", quality_rows)
    write_csv(output_dir / "strategy_nav_issue_summary.csv", issue_rows)
    write_csv(output_dir / "strategy_nav_channel_summary.csv", channel_rows)
    write_csv(output_dir / "strategy_nav_validation_summary.csv", check_rows)

    summary_json = {
        **summary,
        "table_counts": {
            "策略模拟净值": table_count(conn, "策略模拟净值", algorithm_version),
            "策略模拟净值区间": table_count(conn, "策略模拟净值区间", algorithm_version),
            "策略模拟净值质量": table_count(conn, "策略模拟净值质量", algorithm_version),
            "策略模拟净值校验": table_count(conn, "策略模拟净值校验", algorithm_version),
        },
        "issue_summary": issue_rows,
        "channel_summary": channel_rows,
        "validation_summary": check_rows,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary_json, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 策略模拟净值重构报告",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 算法版本：`{algorithm_version}`",
        f"- 初始资产：{summary['initial_asset']:,.2f} 元",
        f"- 基金净值最新日期：{summary['latest_nav_date']}",
        f"- 参与评估策略数：{summary['strategy_count']}，纳入模拟策略数：{summary['included_count']}",
        f"- 写入日度模拟净值：{summary_json['table_counts']['策略模拟净值']} 行；写入区间质量：{summary_json['table_counts']['策略模拟净值区间']} 行。",
        "",
        "## 标准算法口径",
        "",
        "1. 每个策略从成立日开始，以 1 亿元初始资产建仓，按每次调仓后的基金权重进行资产再配置。",
        "2. 调仓持有区间定义为 `(调仓日, 下一调仓日]`，最后一段截至基金净值表最新日期。",
        "3. 基金收益按“分红复投后的总收益”口径回放：货币基金优先用每万份收益，非货币基金优先用单位净值 + 除息日分红推导；缺失时再退回累计净值或日收益率字段。",
        "4. 投顾费按年化费率日化扣减，使用 `年费率 / 365`，按自然日逐日从策略总资产中计提，周末和节假日同样扣费。",
        "5. 同时保留费前、费后两套口径：费前净值只反映调仓、基金涨跌和分红复投；费后净值在费前资产基础上逐日扣减投顾管理费。",
        "6. 调仓、分红、涨跌、投顾费都落实到资产层，费后日净值定义为 `当日扣费后总资产 / 初始资产`。",
        "",
        "## 输出文件",
        "",
        "- `strategy_nav_quality.csv`：策略级纳入情况、费率、累计投顾费、回放收益指标和官方可比差异。",
        "- `strategy_nav_issue_summary.csv`：未纳入策略首个问题分布。",
        "- `strategy_nav_channel_summary.csv`：渠道级覆盖、费率与累计投顾费统计。",
        "- `strategy_nav_validation_summary.csv`：写库后的校验统计。",
        "",
    ]
    (output_dir / "strategy_nav_reconstruction_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    generated_at = datetime.now().isoformat(timespec="seconds")
    db_path = args.db_path
    output_dir = args.output_dir
    algorithm_version = args.algorithm_version
    initial_asset = float(args.initial_asset)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    create_result_tables(conn)
    ensure_result_schema(conn)
    clear_previous_results(conn, algorithm_version, set(args.channel_id) if args.channel_id else None)

    strategies = load_strategies(conn)
    events_by_strategy = load_events(conn)
    details_by_event = load_details(conn)
    fund_profiles = load_fund_profiles(conn)
    latest_nav_date = conn.execute('SELECT MAX("交易日期") FROM "基金日度净值"').fetchone()[0]
    if not latest_nav_date:
        raise RuntimeError("基金日度净值表为空，无法回放策略净值。")

    selected_ids = list(strategies)
    if args.channel_id:
        allow_channels = set(args.channel_id)
        selected_ids = [
            strategy_id
            for strategy_id in selected_ids
            if str(strategies[strategy_id].get("渠道ID") or "") in allow_channels
        ]
    if args.strategy_id:
        allow = set(args.strategy_id)
        selected_ids = [strategy_id for strategy_id in selected_ids if strategy_id in allow]
    selected_ids.sort(key=lambda strategy_id: (str(strategies[strategy_id].get("渠道ID") or ""), strategy_id))
    if args.limit is not None:
        selected_ids = selected_ids[: args.limit]

    nav_rows_all: list[tuple[Any, ...]] = []
    segment_rows_all: list[tuple[Any, ...]] = []
    quality_rows_all: list[tuple[Any, ...]] = []
    check_rows_all: list[tuple[Any, ...]] = []
    included_count = 0

    for strategy_id in selected_ids:
        strategy = strategies[strategy_id]
        events = events_by_strategy.get(strategy_id, [])
        snapshots, duplicate_event_count, variant_date_count = collapse_same_day_events(
            events,
            details_by_event,
            fund_profiles,
        )
        segments = build_segments(strategy, snapshots, fund_profiles, latest_nav_date)
        simulation_summary, nav_rows, check_rows = simulate_strategy(
            conn=conn,
            strategy=strategy,
            segments=segments,
            algorithm_version=algorithm_version,
            generated_at=generated_at,
            initial_asset=initial_asset,
        )

        nav_rows_all.extend(nav_rows)
        check_rows_all.extend(check_rows)
        if simulation_summary["included"]:
            included_count += 1

        for segment in segments:
            segment_rows_all.append(
                segment_to_row(
                    segment=segment,
                    strategy_included=simulation_summary["included"],
                    algorithm_version=algorithm_version,
                    generated_at=generated_at,
                )
            )

        quality_rows_all.append(
            quality_to_row(
                strategy=strategy,
                algorithm_version=algorithm_version,
                generated_at=generated_at,
                initial_asset=initial_asset,
                original_event_count=len(events),
                collapsed_event_count=len(snapshots),
                duplicate_event_count=duplicate_event_count,
                variant_date_count=variant_date_count,
                segments=segments,
                simulation_summary=simulation_summary,
            )
        )

    insert_nav_rows(conn, nav_rows_all)
    insert_segment_rows(conn, segment_rows_all)
    insert_quality_rows(conn, quality_rows_all)
    insert_check_rows(conn, check_rows_all)
    conn.commit()

    summary = {
        "generated_at": generated_at,
        "algorithm_version": algorithm_version,
        "initial_asset": initial_asset,
        "latest_nav_date": latest_nav_date,
        "strategy_count": len(selected_ids),
        "included_count": included_count,
    }
    build_report(output_dir, conn, algorithm_version, summary)
    conn.close()

    print(
        json.dumps(
            {
                "dbPath": str(db_path),
                "outputDir": str(output_dir),
                "generatedAt": generated_at,
                "algorithmVersion": algorithm_version,
                "strategyCount": len(selected_ids),
                "includedCount": included_count,
                "initialAsset": initial_asset,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
