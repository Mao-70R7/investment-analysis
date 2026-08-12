# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from statistics import median
from typing import Any

from basic_data_navigation import SIDEBAR_CSS, render_system_topbar


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "site"
DEFAULT_SITE_DIR = DEFAULT_REPORT_ROOT / "basic_data"
DEFAULT_OUTPUT = DEFAULT_SITE_DIR / "gf-rebalance-monitor.html"

WINDOWS = [
    {"key": "7d", "label": "近一周", "days": 7},
    {"key": "30d", "label": "近一月", "days": 30},
    {"key": "90d", "label": "近三月", "days": 90},
    {"key": "180d", "label": "近半年", "days": 180},
]
MIN_ABS_CHANGE = 0.5
HORIZONS = [5, 20, 60]
MAX_TABLE_ROWS = 80
QUALITY_EFFECTIVE_FLOOR = 20
HIGH_WIN_RATE_FLOOR = 55.0
MARKET_SIGNAL_MIN_GROSS = 5.0
INVALID_LABELS = {"", "-", "--", "未识别", "未分类", "???", "其他"}


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def as_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_json(value: Any, fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return fallback


def round2(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, 2)


def pct_text(value: float | None) -> str:
    if value is None:
        return "--"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def is_cash_group(value: Any) -> bool:
    return str(value or "") == "现金承接"


def normalize_series_name(name: str) -> str:
    text = re.sub(r"\s+", "", name or "")
    text = re.sub(r"第[零一二三四五六七八九十百千万\d]+期", "", text)
    text = re.sub(r"\d{6,8}$", "", text)
    text = re.sub(r"目标盈$", "", text)
    return text or (name or "未命名目标盈系列")


def is_gf_strategy(row: dict[str, Any]) -> bool:
    text = (
        f"{row.get('投顾机构') or row.get('advisor') or ''} "
        f"{row.get('渠道ID') or row.get('channel_id') or ''} "
        f"{row.get('策略名称') or row.get('strategy_name') or ''}"
    )
    return "广发" in text or "gffunds" in text.lower()


def direction_of(change: float, before: float | None, after: float | None) -> tuple[str, str]:
    if change > 0:
        action = "新买入" if (before is None or abs(before) < 0.0001) and (after or 0) > 0 else "加仓"
        return "add", action
    if change < 0:
        action = "清仓" if (after is None or abs(after) < 0.0001) and (before or 0) > 0 else "减仓"
        return "cut", action
    return "flat", "无变化"


def compact_reason(text: str, limit: int = 72) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return "未披露明确原因"
    return text[:limit] + ("..." if len(text) > limit else "")


def top_items(counter: dict[str, float], limit: int = 3) -> list[str]:
    return [k for k, _ in sorted(counter.items(), key=lambda item: (-abs(item[1]), item[0]))[:limit] if k]


def join_items(items: list[str], fallback: str = "未形成集中方向", limit: int | None = None) -> str:
    selected = items[:limit] if limit else items
    return "、".join(selected) if selected else fallback


def clean_reason_for_text(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value.rstrip("。；;、,.， ")


def clean_theme_labels(value: Any, limit: int = 3) -> list[str]:
    themes = parse_json(value, [])
    labels: list[str] = []
    if isinstance(themes, dict):
        items = themes.values() if any(isinstance(v, (dict, list)) for v in themes.values()) else themes.keys()
    elif isinstance(themes, list):
        items = themes
    else:
        items = []
    for item in items:
        if isinstance(item, dict):
            label = (
                item.get("主题名称")
                or item.get("名称")
                or item.get("name")
                or item.get("label")
                or item.get("主题")
                or item.get("entity")
                or ""
            )
        else:
            label = str(item or "")
        label = re.sub(r"\s+", "", str(label)).strip("{}[]'\" ")
        if not label or label in {"-", "--", "未识别", "未分类"}:
            continue
        if label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def is_gf_fund(row: dict[str, Any]) -> bool:
    text = f"{row.get('fund_name') or row.get('基金名称') or ''} {row.get('fund_company') or row.get('基金公司') or ''}"
    return "广发" in text


def product_bucket(asset_label: str, fund_type: str, fund_name: str = "", theme_label: str = "") -> str:
    text = f"{asset_label} {fund_type} {fund_name} {theme_label}"
    if any(token in text for token in ("货币", "现金", "同业存单", "存款")):
        return "现金承接"
    if any(token in text for token in ("债", "固收", "可转债", "收益凭证")):
        return "固收配置"
    if any(token in text for token in ("黄金", "商品", "原油", "贵金属")):
        return "商品配置"
    if any(token in text for token in ("股票", "权益", "混合", "ETF", "指数", "港股", "海外", "QDII", "主题")):
        return "权益/主题"
    return "其他"


def load_latest_date(conn: sqlite3.Connection) -> date:
    row = conn.execute(f"SELECT MAX({q('调仓日期')}) AS latest_date FROM {q('策略调仓明细')}").fetchone()
    latest = as_date(row["latest_date"] if row else None)
    if latest is None:
        raise SystemExit("策略调仓明细缺少调仓日期，无法生成调仓监控页。")
    return latest


def load_rows(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    *,
    event_ids: set[str] | None = None,
    only_gf_funds: bool = True,
) -> list[dict[str, Any]]:
    params: list[Any] = [start.isoformat(), end.isoformat()]
    extra_filters = []
    if event_ids:
        placeholders = ",".join("?" for _ in event_ids)
        extra_filters.append(f"d.{q('调仓事件ID')} IN ({placeholders})")
        params.extend(sorted(event_ids))
    if only_gf_funds:
        extra_filters.append(
            f"""(
        d.{q('基金名称')} LIKE '广发%'
        OR f.{q('基金名称')} LIKE '广发%'
        OR f.{q('基金公司')} LIKE '%广发%'
      )"""
        )
    extra_where = ""
    if extra_filters:
        extra_where = " AND " + " AND ".join(f"({item})" for item in extra_filters)
    sql = f"""
    WITH latest_exposure AS (
      SELECT e.*
      FROM {q('基金经济暴露快照')} e
      JOIN (
        SELECT {q('基金代码')} AS code, MAX({q('报告期')}) AS max_report
        FROM {q('基金经济暴露快照')}
        GROUP BY {q('基金代码')}
      ) x
      ON x.code = e.{q('基金代码')} AND x.max_report = e.{q('报告期')}
    )
    SELECT
      d.{q('调仓明细ID')} AS detail_id,
      d.{q('调仓事件ID')} AS event_id,
      d.{q('统一策略ID')} AS strategy_id,
      d.{q('渠道ID')} AS channel_id,
      d.{q('渠道策略ID')} AS channel_strategy_id,
      d.{q('调仓日期')} AS rebalance_date,
      d.{q('基金代码')} AS fund_code,
      d.{q('基金名称')} AS fund_name,
      d.{q('分组名称')} AS fund_group,
      d.{q('调前权重_百分比')} AS before_weight,
      d.{q('调后权重_百分比')} AS after_weight,
      d.{q('权重变化_百分比')} AS weight_change,
      d.{q('调仓动作')} AS raw_action,
      e.{q('调仓标题')} AS event_title,
      e.{q('调仓原因')} AS event_reason,
      e.{q('事件序号')} AS event_seq,
      s.{q('策略名称')} AS strategy_name,
      s.{q('投顾机构')} AS advisor,
      s.{q('策略类型')} AS strategy_type,
      s.{q('风险等级')} AS risk_level,
      s.{q('策略状态')} AS strategy_status,
      g.{q('治理状态')} AS governance_status,
      g.{q('分析分组')} AS analysis_group,
      g.{q('是否测试组合')} AS is_test,
      g.{q('是否信号类组合')} AS is_signal,
      g.{q('是否目标盈期次')} AS is_target,
      g.{q('是否已停止')} AS is_stopped,
      g.{q('是否纳入常规排名')} AS include_regular_rank,
      g.{q('规则说明')} AS governance_rule,
      f.{q('基金公司')} AS fund_company,
      f.{q('基金类型')} AS fund_type,
      f.{q('跟踪指数')} AS tracked_index,
      x.{q('标准资产大类')} AS standard_asset,
      x.{q('标准资产细类')} AS standard_sub_asset,
      x.{q('经济资产暴露JSON')} AS economic_asset_json,
      x.{q('经济行业暴露JSON')} AS economic_industry_json,
      x.{q('主题标签JSON')} AS theme_json,
      x.{q('质量状态')} AS exposure_quality,
      x.{q('证据说明')} AS exposure_evidence
    FROM {q('策略调仓明细')} d
    LEFT JOIN {q('策略调仓事件')} e ON e.{q('调仓事件ID')} = d.{q('调仓事件ID')}
    LEFT JOIN {q('策略信息')} s ON s.{q('统一策略ID')} = d.{q('统一策略ID')}
    LEFT JOIN {q('策略治理标签')} g ON g.{q('统一策略ID')} = d.{q('统一策略ID')}
    LEFT JOIN {q('基金信息')} f ON f.{q('基金代码')} = d.{q('基金代码')}
    LEFT JOIN latest_exposure x ON x.{q('基金代码')} = d.{q('基金代码')}
    WHERE d.{q('调仓日期')} >= ? AND d.{q('调仓日期')} <= ?
      {extra_where}
    """
    return [dict(row) for row in conn.execute(sql, params)]


def load_nav_return(
    conn: sqlite3.Connection,
    table: str,
    key_col: str,
    key: str,
    start_date: str,
    horizon: int,
    value_cols: list[str],
    alg: str | None = None,
) -> float | None:
    if not key or not start_date:
        return None
    start = as_date(start_date)
    if start is None:
        return None
    target = start + timedelta(days=horizon)
    value_expr = "COALESCE(" + ", ".join(q(col) for col in value_cols) + ")"
    where_alg = f" AND {q('算法版本')} = ?" if alg else ""
    params1: list[Any] = [key, start.isoformat()]
    params2: list[Any] = [key, target.isoformat()]
    if alg:
        params1.append(alg)
        params2.append(alg)
    start_row = conn.execute(
        f"""
        SELECT {q('交易日期')} AS d, {value_expr} AS v
        FROM {q(table)}
        WHERE {q(key_col)} = ? AND {q('交易日期')} >= ? {where_alg}
        ORDER BY {q('交易日期')} ASC
        LIMIT 1
        """,
        params1,
    ).fetchone()
    end_row = conn.execute(
        f"""
        SELECT {q('交易日期')} AS d, {value_expr} AS v
        FROM {q(table)}
        WHERE {q(key_col)} = ? AND {q('交易日期')} >= ? {where_alg}
        ORDER BY {q('交易日期')} ASC
        LIMIT 1
        """,
        params2,
    ).fetchone()
    if not start_row or not end_row:
        return None
    s = as_float(start_row["v"])
    e = as_float(end_row["v"])
    if s is None or e is None or s <= 0:
        return None
    return (e / s - 1.0) * 100.0


class ReturnCache:
    def __init__(self, conn: sqlite3.Connection, algorithm: str):
        self.conn = conn
        self.algorithm = algorithm
        self.cache: dict[tuple[str, str, str, int], float | None] = {}

    def strategy_return(self, strategy_id: str, start_date: str, horizon: int) -> float | None:
        key = ("strategy", strategy_id, start_date, horizon)
        if key not in self.cache:
            self.cache[key] = load_nav_return(
                self.conn,
                "策略标准业绩净值",
                "统一策略ID",
                strategy_id,
                start_date,
                horizon,
                ["标准费前单位净值", "标准费后单位净值"],
                self.algorithm,
            )
        return self.cache[key]

    def fund_return(self, fund_code: str, start_date: str, horizon: int) -> float | None:
        key = ("fund", fund_code, start_date, horizon)
        if key not in self.cache:
            self.cache[key] = load_nav_return(
                self.conn,
                "基金日度净值",
                "基金代码",
                fund_code,
                start_date,
                horizon,
                ["累计净值", "单位净值"],
            )
        return self.cache[key]


def latest_algorithm(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        f"""
        SELECT {q('算法版本')} AS alg, COUNT(*) AS n
        FROM {q('策略标准业绩净值')}
        GROUP BY {q('算法版本')}
        ORDER BY n DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["alg"]) if row else ""


def load_market_context(conn: sqlite3.Connection, start: date, latest: date) -> list[dict[str, Any]]:
    targets = [
        ("000300.SH", "沪深300", "A股核心"),
        ("000852.SH", "中证1000", "小盘成长"),
        ("399006.SZ", "创业板指", "成长风格"),
        ("000922.CSI", "中证红利", "红利风格"),
        ("H11001.CSI", "中证全债", "债券"),
        ("AU9999.SGE", "上海黄金", "黄金"),
        ("HSI.HI", "恒生指数", "港股"),
        ("NDX.GI", "纳斯达克100", "美股科技"),
    ]
    table = "指数日度行情"
    rows: list[dict[str, Any]] = []
    for code, label, group in targets:
        start_row = conn.execute(
            f"""
            SELECT {q('交易日期')} AS trade_date, {q('收盘点位')} AS close_price
            FROM {q(table)}
            WHERE {q('指数代码')} = ? AND {q('交易日期')} >= ? AND {q('收盘点位')} IS NOT NULL
            ORDER BY {q('交易日期')} ASC
            LIMIT 1
            """,
            (code, start.isoformat()),
        ).fetchone()
        end_row = conn.execute(
            f"""
            SELECT {q('交易日期')} AS trade_date, {q('收盘点位')} AS close_price
            FROM {q(table)}
            WHERE {q('指数代码')} = ? AND {q('交易日期')} <= ? AND {q('收盘点位')} IS NOT NULL
            ORDER BY {q('交易日期')} DESC
            LIMIT 1
            """,
            (code, latest.isoformat()),
        ).fetchone()
        if not start_row or not end_row:
            continue
        start_close = as_float(start_row["close_price"])
        end_close = as_float(end_row["close_price"])
        if not start_close or end_close is None:
            continue
        rows.append(
            {
                "code": code,
                "name": label,
                "group": group,
                "startDate": start_row["trade_date"],
                "endDate": end_row["trade_date"],
                "returnPct": round2((end_close / start_close - 1) * 100),
            }
        )
    return rows


def load_strategy_quality(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT
          {q('统一策略ID')} AS strategy_id,
          {q('有效调仓事件数')} AS valid_events,
          {q('胜事件数')} AS win_events,
          {q('胜率_有效事件_百分比')} AS win_rate,
          {q('平均调仓超额_百分比')} AS avg_excess,
          {q('平均正超额_百分比')} AS avg_positive_excess,
          {q('平均负超额_百分比')} AS avg_negative_excess,
          {q('赔率')} AS odds,
          {q('最近一次调仓评价')} AS latest_eval
        FROM {q('调仓质量策略汇总')}
        """
    ).fetchall()
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        strategy_id = str(row["strategy_id"] or "")
        if not strategy_id:
            continue
        valid = as_int(row["valid_events"])
        wins = as_int(row["win_events"])
        win_rate = as_float(row["win_rate"])
        avg_excess = as_float(row["avg_excess"])
        avg_positive_excess = as_float(row["avg_positive_excess"])
        avg_negative_excess = as_float(row["avg_negative_excess"])
        odds = as_float(row["odds"])
        output[strategy_id] = {
            "validEvents": valid,
            "winEvents": wins,
            "winRate": round2(win_rate),
            "avgExcess": round2(avg_excess),
            "avgPositiveExcess": round2(avg_positive_excess),
            "avgNegativeExcess": round2(avg_negative_excess),
            "odds": round2(odds),
            "latestEval": row["latest_eval"] or "",
            "qualityBand": quality_band(valid, win_rate, avg_excess),
        }
    return output


def quality_band(valid_events: int, win_rate: float | None, avg_excess: float | None) -> str:
    if valid_events < QUALITY_EFFECTIVE_FLOOR:
        return "样本较少"
    if win_rate is not None and win_rate >= HIGH_WIN_RATE_FLOOR:
        return "高胜率"
    if avg_excess is not None and avg_excess > 0:
        return "均值正"
    return "质量一般"


def quality_text(valid_events: int, win_rate: float | None, avg_excess: float | None) -> str:
    band = quality_band(valid_events, win_rate, avg_excess)
    if not valid_events:
        return "暂无历史评价"
    win = f"胜率 {win_rate:.1f}%" if win_rate is not None else "胜率 --"
    excess = f"均超 {pct_text(avg_excess)}" if avg_excess is not None else "均超 --"
    return f"{band}：{valid_events} 次有效，{win}，{excess}"


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    before = as_float(row.get("before_weight"))
    after = as_float(row.get("after_weight"))
    change = as_float(row.get("weight_change"))
    if change is None and before is not None and after is not None:
        change = after - before
    change = change or 0.0
    direction, action = direction_of(change, before, after)
    strategy_name = row.get("strategy_name") or row.get("strategy_id") or "未命名策略"
    advisor = row.get("advisor") or "未披露机构"
    is_target = as_int(row.get("is_target")) == 1 or "目标盈" in f"{row.get('governance_status') or ''} {row.get('analysis_group') or ''}"
    series_name = normalize_series_name(strategy_name) if is_target else strategy_name
    unit_id = f"target::{advisor}::{series_name}" if is_target else str(row.get("strategy_id") or "")
    unit_name = f"{series_name}（目标盈系列合并）" if is_target else strategy_name
    asset_json = parse_json(row.get("economic_asset_json"), {})
    industry_json = parse_json(row.get("economic_industry_json"), {})
    themes = clean_theme_labels(row.get("theme_json"), 3)
    top_asset = row.get("standard_sub_asset") or row.get("standard_asset") or row.get("fund_group") or row.get("fund_type") or "未分类"
    if isinstance(asset_json, dict) and asset_json:
        top_asset = max(asset_json.items(), key=lambda item: as_float(item[1]) or 0)[0] or top_asset
    top_industry = ""
    if isinstance(industry_json, dict) and industry_json:
        top_industry = "、".join(top_items({str(k): as_float(v) or 0 for k, v in industry_json.items()}, 2))
    initial_text = f"{row.get('event_title') or ''} {row.get('raw_action') or ''}"
    is_explicit_initial = "建仓" in initial_text
    is_first_observed = as_int(row.get("event_seq")) == 1
    return {
        **row,
        "before": before,
        "after": after,
        "change": change,
        "direction": direction,
        "action": action,
        "strategy_name": strategy_name,
        "advisor": advisor,
        "fund_name": row.get("fund_name") or row.get("fund_code") or "未命名基金",
        "fund_company": row.get("fund_company") or ("广发基金" if str(row.get("fund_name") or "").startswith("广发") else ""),
        "is_gf_fund": is_gf_fund(row),
        "is_target": is_target,
        "is_signal": as_int(row.get("is_signal")) == 1,
        "is_test": as_int(row.get("is_test")) == 1 or "测试" in strategy_name,
        "is_stopped": as_int(row.get("is_stopped")) == 1 or "停止" in str(row.get("governance_status") or ""),
        "is_initial": is_explicit_initial,
        "is_first_observed": is_first_observed,
        "is_first_observed_not_initial": is_first_observed and not is_explicit_initial,
        "is_gf_strategy": is_gf_strategy(row),
        "unit_id": unit_id,
        "unit_name": unit_name,
        "series_name": series_name,
        "asset_label": top_asset,
        "industry_label": top_industry,
        "theme_label": "、".join(themes) if themes else "",
        "reason": compact_reason(row.get("event_reason") or row.get("event_title") or ""),
    }


def usable_row(row: dict[str, Any]) -> bool:
    if row["direction"] == "flat":
        return False
    if abs(row["change"]) < MIN_ABS_CHANGE:
        return False
    if row["is_test"] or row["is_signal"] or row["is_initial"] or row["is_stopped"]:
        return False
    return True


def exclusion_stats(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "rawRows": len(rows),
        "belowThreshold": sum(1 for row in rows if abs(row["change"]) < MIN_ABS_CHANGE),
        "testRows": sum(1 for row in rows if row["is_test"]),
        "signalRows": sum(1 for row in rows if row["is_signal"]),
        "initialRows": sum(1 for row in rows if row["is_initial"]),
        "firstObservedRows": sum(1 for row in rows if row.get("is_first_observed")),
        "firstObservedNotInitialRows": sum(1 for row in rows if row.get("is_first_observed_not_initial")),
        "stoppedRows": sum(1 for row in rows if row["is_stopped"]),
    }


def sample_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "rows": len(rows),
        "eventCount": len({row.get("event_id") for row in rows if row.get("event_id")}),
        "strategyUnitCount": len({row.get("unit_id") for row in rows if row.get("unit_id")}),
    }


def build_filter_flow(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []
    current = list(rows)

    def add_stage(name: str, note: str, items: list[dict[str, Any]]) -> None:
        stages.append({"name": name, "note": note, **sample_counts(items)})

    add_stage("原始披露调仓", "按调仓明细去重统计，尽量与近期调仓明细口径一致。", current)
    current = [row for row in current if row["direction"] != "flat"]
    add_stage("去除无权重变化", "只保留基金权重发生增减的明细。", current)
    current = [row for row in current if abs(row["change"]) >= MIN_ABS_CHANGE]
    add_stage("去除小幅变化", f"单只基金权重变化低于 {MIN_ABS_CHANGE:.1f}pct 的明细不用于形成方向观点。", current)
    current = [row for row in current if not row["is_test"] and not row["is_signal"]]
    add_stage("去除测试和信号服务", "测试组合、信号服务类组合不参与全市场主动调仓观点。", current)
    current = [row for row in current if not row["is_initial"]]
    add_stage("去除明确建仓", "只剔除标题或动作明确写有“建仓”的事件；首次观察到的普通调仓不再默认剔除。", current)
    current = [row for row in current if not row["is_stopped"]]
    add_stage("去除已停止策略", "已停止运营或治理标签标记停止的策略不参与当前主动分析。", current)
    return stages


def aggregate_unit_records(
    rows: list[dict[str, Any]],
    returns: ReturnCache,
    quality_by_strategy: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["unit_id"], row.get("fund_code") or row["fund_name"], row["direction"])].append(row)

    records = []
    for (_, _, _), group in grouped.items():
        latest = max(group, key=lambda item: str(item.get("rebalance_date") or ""))
        changes = [row["change"] for row in group if as_float(row["change"]) is not None]
        befores = [row["before"] for row in group if row["before"] is not None]
        afters = [row["after"] for row in group if row["after"] is not None]
        if latest["is_target"] and len(changes) > 1:
            change = median(changes)
            before = median(befores) if befores else None
            after = median(afters) if afters else None
        else:
            change = sum(changes)
            before = sum(befores) if befores else None
            after = sum(afters) if afters else None
        quality = (quality_by_strategy or {}).get(str(latest.get("strategy_id") or ""), {})
        fund_returns = {f"fundT{h}": round2(returns.fund_return(str(latest.get("fund_code") or ""), str(latest.get("rebalance_date") or ""), h)) for h in HORIZONS}
        strategy_returns = {f"strategyT{h}": round2(returns.strategy_return(str(latest.get("strategy_id") or ""), str(latest.get("rebalance_date") or ""), h)) for h in HORIZONS}
        records.append(
            {
                "unitId": latest["unit_id"],
                "unitName": latest["unit_name"],
                "strategyId": latest.get("strategy_id") or "",
                "strategyName": latest["strategy_name"],
                "eventId": latest.get("event_id") or "",
                "advisor": latest["advisor"],
                "channelId": latest.get("channel_id") or "",
                "fundCode": latest.get("fund_code") or "",
                "fundName": latest["fund_name"],
                "fundCompany": latest.get("fund_company") or "",
                "fundType": latest.get("fund_type") or "",
                "strategyType": latest.get("strategy_type") or "",
                "assetLabel": latest.get("asset_label") or "未分类",
                "industryLabel": latest.get("industry_label") or "",
                "themeLabel": latest.get("theme_label") or "",
                "businessGroup": product_bucket(
                    latest.get("asset_label") or "",
                    latest.get("fund_type") or "",
                    latest["fund_name"],
                    latest.get("theme_label") or "",
                ),
                "exposureQuality": latest.get("exposure_quality") or "",
                "rebalanceDate": latest.get("rebalance_date") or "",
                "direction": latest["direction"],
                "action": latest["action"],
                "change": round2(change),
                "before": round2(before),
                "after": round2(after),
                "isTargetSeries": bool(latest["is_target"]),
                "isGfStrategy": bool(latest["is_gf_strategy"]),
                "qualityValidEvents": quality.get("validEvents", 0),
                "qualityWinEvents": quality.get("winEvents", 0),
                "qualityWinRate": quality.get("winRate"),
                "qualityAvgExcess": quality.get("avgExcess"),
                "qualityOdds": quality.get("odds"),
                "qualityBand": quality.get("qualityBand", "暂无历史评价"),
                "qualityText": quality_text(
                    as_int(quality.get("validEvents")),
                    as_float(quality.get("winRate")),
                    as_float(quality.get("avgExcess")),
                ),
                "rawRows": len(group),
                "eventCount": len({row.get("event_id") for row in group if row.get("event_id")}),
                "reason": latest["reason"],
                "reasonFull": latest.get("event_reason") or latest.get("event_title") or "",
                **fund_returns,
                **strategy_returns,
            }
        )
    return records


def clean_signal_label(value: Any) -> str:
    label = re.sub(r"\s+", "", str(value or "")).strip()
    return "" if label in INVALID_LABELS else label


def aggregate_market_records(
    rows: list[dict[str, Any]],
    quality_by_strategy: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["unit_id"], row.get("fund_code") or row["fund_name"], row["direction"])].append(row)

    records: list[dict[str, Any]] = []
    for group in grouped.values():
        latest = max(group, key=lambda item: str(item.get("rebalance_date") or ""))
        changes = [row["change"] for row in group if as_float(row["change"]) is not None]
        befores = [row["before"] for row in group if row["before"] is not None]
        afters = [row["after"] for row in group if row["after"] is not None]
        if latest["is_target"] and len(changes) > 1:
            change = median(changes)
            before = median(befores) if befores else None
            after = median(afters) if afters else None
        else:
            change = sum(changes)
            before = sum(befores) if befores else None
            after = sum(afters) if afters else None
        quality = quality_by_strategy.get(str(latest.get("strategy_id") or ""), {})
        records.append(
            {
                "unitId": latest["unit_id"],
                "unitName": latest["unit_name"],
                "strategyId": latest.get("strategy_id") or "",
                "strategyName": latest["strategy_name"],
                "eventId": latest.get("event_id") or "",
                "advisor": latest["advisor"],
                "fundCode": latest.get("fund_code") or "",
                "fundName": latest["fund_name"],
                "fundCompany": latest.get("fund_company") or "",
                "strategyType": latest.get("strategy_type") or "",
                "businessGroup": product_bucket(
                    latest.get("asset_label") or "",
                    latest.get("fund_type") or "",
                    latest["fund_name"],
                    latest.get("theme_label") or "",
                ),
                "assetLabel": clean_signal_label(latest.get("asset_label") or "") or "未分类",
                "industryLabel": clean_signal_label(latest.get("industry_label") or ""),
                "themeLabel": clean_signal_label(latest.get("theme_label") or ""),
                "rebalanceDate": latest.get("rebalance_date") or "",
                "direction": latest["direction"],
                "action": latest["action"],
                "change": round2(change) or 0,
                "before": round2(before),
                "after": round2(after),
                "isTargetSeries": bool(latest["is_target"]),
                "isGfStrategy": bool(latest["is_gf_strategy"]),
                "isGfFund": bool(latest["is_gf_fund"]),
                "qualityValidEvents": quality.get("validEvents", 0),
                "qualityWinEvents": quality.get("winEvents", 0),
                "qualityWinRate": quality.get("winRate"),
                "qualityAvgExcess": quality.get("avgExcess"),
                "qualityOdds": quality.get("odds"),
                "qualityBand": quality.get("qualityBand", "暂无历史评价"),
                "qualityText": quality_text(
                    as_int(quality.get("validEvents")),
                    as_float(quality.get("winRate")),
                    as_float(quality.get("avgExcess")),
                ),
                "reason": latest["reason"],
            }
        )
    return records


def summarize_signal_groups(
    records: list[dict[str, Any]],
    previous_records: list[dict[str, Any]],
    key: str,
    *,
    limit: int = 8,
    min_gross: float = MARKET_SIGNAL_MIN_GROSS,
) -> list[dict[str, Any]]:
    current: dict[str, dict[str, Any]] = {}
    previous_net: dict[str, float] = defaultdict(float)

    def labels(row: dict[str, Any]) -> list[str]:
        value = clean_signal_label(row.get(key))
        if not value:
            return []
        if key == "themeLabel":
            return [item for item in value.split("、") if clean_signal_label(item)]
        return [value]

    for row in previous_records:
        for label in labels(row):
            previous_net[label] += row["change"] or 0
    for row in records:
        for label in labels(row):
            item = current.setdefault(
                label,
                {
                    "name": label,
                    "netChange": 0.0,
                    "grossChange": 0.0,
                    "addGross": 0.0,
                    "cutGross": 0.0,
                    "records": 0,
                    "advisors": set(),
                    "strategies": set(),
                    "highWinAdvisors": set(),
                    "positiveAdvisors": set(),
                    "reasons": defaultdict(float),
                },
            )
            change = row["change"] or 0
            item["netChange"] += change
            item["grossChange"] += abs(change)
            item["addGross"] += max(change, 0)
            item["cutGross"] += abs(min(change, 0))
            item["records"] += 1
            if row.get("advisor"):
                item["advisors"].add(row["advisor"])
            if row.get("unitId"):
                item["strategies"].add(row["unitId"])
            if row.get("qualityBand") == "高胜率":
                item["highWinAdvisors"].add(row["advisor"])
            if row.get("qualityBand") in {"高胜率", "均值正"}:
                item["positiveAdvisors"].add(row["advisor"])
            item["reasons"][row.get("reason") or "未披露明确原因"] += abs(change)

    output: list[dict[str, Any]] = []
    for label, item in current.items():
        gross = item["grossChange"]
        if gross < min_gross:
            continue
        net = item["netChange"]
        prev_net = previous_net.get(label, 0.0)
        consensus = abs(net) / gross * 100 if gross else 0
        if gross >= 20 and consensus < 20:
            strength = "高换手轮动"
        elif abs(net) >= 8 and len(item["advisors"]) >= 2:
            strength = "方向信号"
        elif abs(net - prev_net) >= 10:
            strength = "趋势变化"
        else:
            strength = "观察"
        output.append(
            {
                "name": label,
                "netChange": round2(net) or 0,
                "grossChange": round2(gross) or 0,
                "addGross": round2(item["addGross"]) or 0,
                "cutGross": round2(item["cutGross"]) or 0,
                "previousNetChange": round2(prev_net) or 0,
                "netDelta": round2(net - prev_net) or 0,
                "advisorCount": len(item["advisors"]),
                "strategyUnitCount": len(item["strategies"]),
                "recordCount": item["records"],
                "consensus": round2(consensus),
                "direction": "净加仓" if net > 0 else ("净减仓" if net < 0 else "方向均衡"),
                "strength": strength,
                "highWinAdvisorCount": len(item["highWinAdvisors"]),
                "positiveAdvisorCount": len(item["positiveAdvisors"]),
                "topReasons": top_items(item["reasons"], 2),
            }
        )
    return sorted(output, key=lambda row: (-abs(row["netDelta"]), -abs(row["netChange"]), -(row["grossChange"] or 0), row["name"]))[:limit]


def summarize_market_signals(records: list[dict[str, Any]], previous_records: list[dict[str, Any]]) -> dict[str, Any]:
    asset_buckets = summarize_signal_groups(records, previous_records, "businessGroup", limit=6, min_gross=3)
    asset_details = summarize_signal_groups(records, previous_records, "assetLabel", limit=8, min_gross=5)
    industry_signals = summarize_signal_groups(records, previous_records, "industryLabel", limit=40, min_gross=2)
    theme_signals = summarize_signal_groups(records, previous_records, "themeLabel", limit=6, min_gross=3)
    gross = sum(abs(row["change"] or 0) for row in records)
    net = sum(row["change"] or 0 for row in records)
    advisors = {row["advisor"] for row in records if row.get("advisor")}
    units = {row["unitId"] for row in records if row.get("unitId")}
    top_gross = max(asset_buckets, key=lambda row: row["grossChange"], default=None)
    top_delta = max(asset_buckets, key=lambda row: abs(row["netDelta"]), default=None)
    if not records:
        narrative = [
            {
                "type": "warn",
                "title": "本窗口全市场无有效主动调仓样本",
                "text": "剔除建仓、测试、信号服务和小幅调整后，没有足够样本支撑资产配置结论。",
            }
        ]
    elif top_gross and (top_gross.get("consensus") or 0) < 20 and (top_gross.get("grossChange") or 0) >= 20:
        narrative = [
            {
                "type": "focus",
                "title": f"{top_gross['name']}是轮动，不是单边加仓",
                "text": f"调整绝对值 {pct_text(top_gross['grossChange'])}，净变化仅 {pct_text(top_gross['netChange'])}，一致性 {top_gross['consensus']:.1f}%；更像结构切换而非统一抬升风险预算。",
            }
        ]
    elif top_delta:
        narrative = [
            {
                "type": "focus",
                "title": f"{top_delta['name']}较上期变化最大",
                "text": f"本期净变化 {pct_text(top_delta['netChange'])}，上期 {pct_text(top_delta['previousNetChange'])}，变化 {pct_text(top_delta['netDelta'])}。",
            }
        ]
    else:
        narrative = [
            {
                "type": "neutral",
                "title": "资产配置方向偏均衡",
                "text": f"全市场净变化 {pct_text(net)}，调整绝对值 {pct_text(gross)}，未形成清晰单边共识。",
            }
        ]
    return {
        "kpis": {
            "recordCount": len(records),
            "advisorCount": len(advisors),
            "strategyUnitCount": len(units),
            "netChange": round2(net) or 0,
            "grossChange": round2(gross) or 0,
            "consensus": round2(abs(net) / gross * 100) if gross else 0,
        },
        "narrative": narrative,
        "assetBuckets": asset_buckets,
        "assetDetails": asset_details,
        "industrySignals": industry_signals,
        "industryAdds": sorted([row for row in industry_signals if (row.get("addGross") or 0) > 0], key=lambda row: (-(row.get("addGross") or 0), row["name"]))[:3],
        "industryCuts": sorted([row for row in industry_signals if (row.get("cutGross") or 0) > 0], key=lambda row: (-(row.get("cutGross") or 0), row["name"]))[:3],
        "themeSignals": theme_signals,
    }


def summarize_quality_advisors(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"records": [], "strategies": {}})
    for row in records:
        advisor = row.get("advisor") or "未披露机构"
        grouped[advisor]["records"].append(row)
        strategy_id = row.get("strategyId") or row.get("unitId")
        if strategy_id and strategy_id not in grouped[advisor]["strategies"]:
            grouped[advisor]["strategies"][strategy_id] = row

    rows: list[dict[str, Any]] = []
    for advisor, item in grouped.items():
        current = item["records"]
        strategies = list(item["strategies"].values())
        core_current = [row for row in current if not is_cash_group(row.get("businessGroup"))]
        focus_current = core_current or current
        valid = sum(as_int(row.get("qualityValidEvents")) for row in strategies)
        wins = sum(as_int(row.get("qualityWinEvents")) for row in strategies)
        win_rate = wins / valid * 100 if valid else None
        avg_excess = (
            sum((as_float(row.get("qualityAvgExcess")) or 0) * as_int(row.get("qualityValidEvents")) for row in strategies) / valid
            if valid
            else None
        )
        asset_counter: dict[str, float] = defaultdict(float)
        fund_counter: dict[str, float] = defaultdict(float)
        type_counter: dict[str, float] = defaultdict(float)
        reason_counter: dict[str, float] = defaultdict(float)
        for row in focus_current:
            asset_counter[row.get("businessGroup") or "未分类"] += row["change"] or 0
            fund_counter[row.get("fundName") or "未命名基金"] += row["change"] or 0
            type_counter[row.get("strategyType") or "未披露策略类型"] += abs(row["change"] or 0)
            reason_counter[row.get("reason") or "未披露明确原因"] += abs(row["change"] or 0)
        band = quality_band(valid, win_rate, avg_excess)
        strategy_count = len({row.get("unitId") for row in current if row.get("unitId")})
        core_gross = sum(abs(row["change"] or 0) for row in core_current)
        current_gross = sum(abs(row["change"] or 0) for row in current)
        rows.append(
            {
                "advisor": advisor,
                "qualityBand": band,
                "validEvents": valid,
                "winRate": round2(win_rate),
                "avgExcess": round2(avg_excess),
                "currentNetChange": round2(sum(row["change"] or 0 for row in current)) or 0,
                "currentGrossChange": round2(current_gross) or 0,
                "coreNetChange": round2(sum(row["change"] or 0 for row in core_current)) or 0,
                "coreGrossChange": round2(core_gross) or 0,
                "cashGrossChange": round2(sum(abs(row["change"] or 0) for row in current if is_cash_group(row.get("businessGroup")))) or 0,
                "strategyUnitCount": strategy_count,
                "avgTurnover": round2((core_gross or current_gross) / strategy_count) if strategy_count else None,
                "fundCount": len({row.get("fundCode") or row.get("fundName") for row in current}),
                "topAssets": top_items(asset_counter, 3),
                "topFunds": top_items(fund_counter, 4),
                "topStrategyTypes": top_items(type_counter, 2),
                "topReasons": top_items(reason_counter, 2),
                "qualityText": quality_text(valid, win_rate, avg_excess),
            }
        )
    priority = {"高胜率": 0, "均值正": 1, "质量一般": 2, "样本较少": 3, "暂无历史评价": 4}
    rows = sorted(rows, key=lambda row: (priority.get(row["qualityBand"], 9), -(row["coreGrossChange"] or row["currentGrossChange"] or 0), -(row["validEvents"] or 0), row["advisor"]))
    high_win = [row for row in rows if row["qualityBand"] == "高胜率"]
    positive = [row for row in rows if row["qualityBand"] in {"高胜率", "均值正"}]
    if high_win:
        top = high_win[0]
        narrative = [
            {
                "type": "up" if (top["currentNetChange"] or 0) > 0 else "focus",
                "title": f"高胜率机构动作：{top['advisor']}",
                "text": f"{top['qualityText']}；本期核心资产调整 {pct_text(top['coreGrossChange'] or top['currentGrossChange'])}，主要指向 {'、'.join(top['topAssets']) or '未分类'}。",
            }
        ]
    elif positive:
        top = positive[0]
        narrative = [
            {
                "type": "focus",
                "title": "质量样本给出观察级主题切换",
                "text": f"{top['advisor']} 历史 {top['validEvents']} 次有效、均超 {pct_text(top['avgExcess'])}；本期核心动作以 {'、'.join(top['topAssets']) or '未分类'} 为主，信号等级按观察处理。",
            }
        ]
    else:
        narrative = [
            {
                "type": "warn",
                "title": "历史高胜率样本未形成当期信号",
                "text": "本窗口有效调仓主要来自历史样本偏少或胜率未达阈值的策略，暂不把机构动作解释为强共识。",
            }
        ]
    return {
        "kpis": {
            "advisorCount": len(rows),
            "highWinAdvisorCount": len(high_win),
            "positiveAdvisorCount": len(positive),
        },
        "narrative": narrative,
        "advisors": (high_win or positive or rows)[:12],
    }


def summarize_quality_strategies(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if is_cash_group(row.get("businessGroup")):
            continue
        grouped[row.get("unitId") or row.get("strategyId") or row.get("strategyName") or "未命名策略"].append(row)
    rows: list[dict[str, Any]] = []
    for _, group in grouped.items():
        latest = max(group, key=lambda row: row.get("rebalanceDate") or "")
        valid = as_int(latest.get("qualityValidEvents"))
        win_rate = as_float(latest.get("qualityWinRate"))
        avg_excess = as_float(latest.get("qualityAvgExcess"))
        band = quality_band(valid, win_rate, avg_excess)
        if band not in {"高胜率", "均值正"}:
            continue
        fund_counter: dict[str, float] = defaultdict(float)
        asset_counter: dict[str, float] = defaultdict(float)
        industry_counter: dict[str, float] = defaultdict(float)
        reason_counter: dict[str, float] = defaultdict(float)
        for row in group:
            fund_counter[row.get("fundName") or "未命名基金"] += row.get("change") or 0
            asset_counter[row.get("businessGroup") or "未分类"] += row.get("change") or 0
            for label in str(row.get("industryLabel") or "").split("、"):
                label = clean_signal_label(label)
                if label:
                    industry_counter[label] += row.get("change") or 0
            reason_counter[row.get("reason") or "未披露明确原因"] += abs(row.get("change") or 0)
        gross = sum(abs(row.get("change") or 0) for row in group)
        rows.append(
            {
                "strategyId": latest.get("strategyId") or "",
                "unitName": latest.get("unitName") or latest.get("strategyName") or "",
                "advisor": latest.get("advisor") or "",
                "strategyType": latest.get("strategyType") or "未披露策略类型",
                "qualityBand": band,
                "validEvents": valid,
                "winRate": round2(win_rate),
                "avgExcess": round2(avg_excess),
                "odds": round2(as_float(latest.get("qualityOdds"))),
                "rebalanceDate": latest.get("rebalanceDate") or "",
                "netChange": round2(sum(row.get("change") or 0 for row in group)) or 0,
                "grossChange": round2(gross) or 0,
                "turnoverRate": round2(gross),
                "topFunds": top_items(fund_counter, 3),
                "topAssets": top_items(asset_counter, 2),
                "topIndustries": top_items(industry_counter, 3),
                "topReasons": top_items(reason_counter, 2),
                "qualityText": quality_text(valid, win_rate, avg_excess),
            }
        )
    return sorted(rows, key=lambda row: (0 if row["qualityBand"] == "高胜率" else 1, -(row["grossChange"] or 0), -(row["validEvents"] or 0), row["unitName"]))[:10]


def product_anomaly_text(
    *,
    fund_name: str,
    direction: str,
    net: float,
    gross: float,
    consensus: float,
    advisor_count: int,
    strategy_count: int,
    high_win_count: int,
    positive_quality_count: int,
    top_reasons: list[str],
) -> str:
    if gross <= 0:
        return "仅有零散持仓变化，暂不构成明确异动。"
    if consensus >= 70:
        consensus_text = "方向一致性高，机构动作较集中"
    elif consensus >= 35:
        consensus_text = "方向一致性中等，存在部分反向调整"
    else:
        consensus_text = "方向一致性低，更像组合内部轮动或机构分歧"
    quality_bits = []
    if high_win_count:
        quality_bits.append(f"{high_win_count}家高胜率机构参与")
    elif positive_quality_count:
        quality_bits.append(f"{positive_quality_count}家历史均超为正机构参与")
    reason_items = [clean_reason_for_text(item) for item in top_reasons if clean_reason_for_text(item)]
    reason = f"主要原因：{'；'.join(reason_items[:2]) if reason_items else '未披露明确原因'}"
    quality_text = f"；{join_items(quality_bits, '历史质量支持不强')}"
    return (
        f"{fund_name}{direction}{pct_text(net)}，调整强度{pct_text(gross)}，"
        f"覆盖{advisor_count}家机构/{strategy_count}个策略；{consensus_text}{quality_text}；{reason}。"
    )


def summarize_products(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[row["fundCode"] or row["fundName"]].append(row)
    products = []
    for _, group in grouped.items():
        fund = group[0]
        net = sum(row["change"] or 0 for row in group)
        gross = sum(abs(row["change"] or 0) for row in group)
        add_units = [row for row in group if row["direction"] == "add"]
        cut_units = [row for row in group if row["direction"] == "cut"]
        advisors = sorted({row["advisor"] for row in group if row["advisor"]})
        units = sorted({row["unitId"] for row in group if row["unitId"]})
        external_units = sorted({row["unitId"] for row in group if row["unitId"] and not row["isGfStrategy"]})
        high_win_advisors = sorted({row["advisor"] for row in group if row.get("qualityBand") == "高胜率" and row.get("advisor")})
        positive_quality_advisors = sorted({row["advisor"] for row in group if row.get("qualityBand") in {"高胜率", "均值正"} and row.get("advisor")})
        mature = [row["fundT20"] for row in group if row.get("fundT20") is not None]
        avg_t20 = sum(mature) / len(mature) if mature else None
        consensus = abs(net) / gross if gross > 0 else 0
        direction = "净调入" if net > 0 else ("净调出" if net < 0 else "分歧")
        score = min(
            100.0,
            len(advisors) * 9
            + len(units) * 3
            + min(gross, 35) * 1.2
            + consensus * 18
            + len(external_units) * 2
            + len(high_win_advisors) * 8
            + len(positive_quality_advisors) * 4,
        )
        advisor_counter: dict[str, float] = defaultdict(float)
        reason_counter: dict[str, float] = defaultdict(float)
        for row in group:
            advisor_counter[row["advisor"]] += row["change"] or 0
            reason_counter[row["reason"]] += abs(row["change"] or 0)
        top_reasons = top_items(reason_counter, 2)
        products.append(
            {
                "fundCode": fund["fundCode"],
                "fundName": fund["fundName"],
                "fundCompany": fund["fundCompany"] or ("广发基金" if str(fund.get("fundName") or "").startswith("广发") else ""),
                "fundType": fund["fundType"] or "",
                "assetLabel": fund["assetLabel"],
                "industryLabel": fund["industryLabel"],
                "themeLabel": fund["themeLabel"],
                "businessGroup": fund.get("businessGroup") or product_bucket(
                    fund.get("assetLabel") or "",
                    fund.get("fundType") or "",
                    fund.get("fundName") or "",
                    fund.get("themeLabel") or "",
                ),
                "exposureQuality": fund["exposureQuality"],
                "direction": direction,
                "netChange": round2(net),
                "grossChange": round2(gross),
                "beforeSum": round2(sum(row["before"] or 0 for row in group)),
                "afterSum": round2(sum(row["after"] or 0 for row in group)),
                "consensus": round2(consensus * 100),
                "advisorCount": len(advisors),
                "strategyUnitCount": len(units),
                "externalUnitCount": len(external_units),
                "addCount": len(add_units),
                "cutCount": len(cut_units),
                "highWinAdvisorCount": len(high_win_advisors),
                "positiveQualityAdvisorCount": len(positive_quality_advisors),
                "qualitySupport": (
                    f"高胜率{len(high_win_advisors)}家"
                    if high_win_advisors
                    else (f"均值正{len(positive_quality_advisors)}家" if positive_quality_advisors else "质量弱")
                ),
                "anomalyText": product_anomaly_text(
                    fund_name=fund["fundName"],
                    direction=direction,
                    net=net,
                    gross=gross,
                    consensus=consensus * 100,
                    advisor_count=len(advisors),
                    strategy_count=len(units),
                    high_win_count=len(high_win_advisors),
                    positive_quality_count=len(positive_quality_advisors),
                    top_reasons=top_reasons,
                ),
                "avgFundT20": round2(avg_t20),
                "matureFundT20Count": len(mature),
                "topAdvisors": top_items(advisor_counter, 4),
                "topReasons": top_reasons,
                "attentionScore": round2(score),
            }
        )
    return sorted(products, key=lambda row: (-(row["attentionScore"] or 0), -(row["grossChange"] or 0), row["fundName"]))[:MAX_TABLE_ROWS]


def summarize_institutions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[row["advisor"] or "未披露机构"].append(row)
    output = []
    for advisor, group in grouped.items():
        net = sum(row["change"] or 0 for row in group)
        gross = sum(abs(row["change"] or 0) for row in group)
        fund_counter: dict[str, float] = defaultdict(float)
        for row in group:
            fund_counter[row["fundName"]] += row["change"] or 0
        mature = [row["strategyT20"] for row in group if row.get("strategyT20") is not None]
        output.append(
            {
                "advisor": advisor,
                "isGfStrategyProvider": any(row["isGfStrategy"] for row in group),
                "netChange": round2(net),
                "grossChange": round2(gross),
                "fundCount": len({row["fundCode"] or row["fundName"] for row in group}),
                "strategyUnitCount": len({row["unitId"] for row in group}),
                "addCount": sum(1 for row in group if row["direction"] == "add"),
                "cutCount": sum(1 for row in group if row["direction"] == "cut"),
                "avgStrategyT20": round2(sum(mature) / len(mature)) if mature else None,
                "matureStrategyT20Count": len(mature),
                "topFunds": top_items(fund_counter, 4),
            }
        )
    return sorted(output, key=lambda row: (-(row["grossChange"] or 0), row["advisor"]))[:MAX_TABLE_ROWS]


def summarize_strategies(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[row["unitId"]].append(row)
    output = []
    for _, group in grouped.items():
        latest = max(group, key=lambda row: row["rebalanceDate"])
        net = sum(row["change"] or 0 for row in group)
        gross = sum(abs(row["change"] or 0) for row in group)
        fund_counter: dict[str, float] = defaultdict(float)
        for row in group:
            fund_counter[row["fundName"]] += row["change"] or 0
        output.append(
            {
                "unitId": latest["unitId"],
                "unitName": latest["unitName"],
                "strategyId": latest["strategyId"],
                "advisor": latest["advisor"],
                "rebalanceDate": latest["rebalanceDate"],
                "isTargetSeries": latest["isTargetSeries"],
                "isGfStrategy": latest["isGfStrategy"],
                "netChange": round2(net),
                "grossChange": round2(gross),
                "fundCount": len({row["fundCode"] or row["fundName"] for row in group}),
                "addCount": sum(1 for row in group if row["direction"] == "add"),
                "cutCount": sum(1 for row in group if row["direction"] == "cut"),
                "strategyT20": latest.get("strategyT20"),
                "topFunds": top_items(fund_counter, 3),
                "reason": latest["reason"],
            }
        )
    return sorted(output, key=lambda row: (row["rebalanceDate"], row["grossChange"] or 0), reverse=True)[:MAX_TABLE_ROWS]


def build_matrix(records: list[dict[str, Any]], products: list[dict[str, Any]], institutions: list[dict[str, Any]]) -> dict[str, Any]:
    top_funds = [row["fundName"] for row in products[:8]]
    top_advisors = [row["advisor"] for row in institutions[:10]]
    cells = []
    for advisor in top_advisors:
        for fund in top_funds:
            group = [row for row in records if row["advisor"] == advisor and row["fundName"] == fund]
            if not group:
                continue
            net = sum(row["change"] or 0 for row in group)
            gross = sum(abs(row["change"] or 0) for row in group)
            cells.append(
                {
                    "advisor": advisor,
                    "fundName": fund,
                    "netChange": round2(net),
                    "grossChange": round2(gross),
                    "count": len(group),
                    "details": [
                        {
                            "strategyName": row["unitName"],
                            "strategyId": row["strategyId"],
                            "date": row["rebalanceDate"],
                            "action": row["action"],
                            "change": row["change"],
                            "reason": row["reason"],
                        }
                        for row in sorted(group, key=lambda item: item["rebalanceDate"], reverse=True)[:12]
                    ],
                }
            )
    return {"funds": top_funds, "advisors": top_advisors, "cells": cells}


def build_cases(products: list[dict[str, Any]], strategies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    add = next((row for row in products if (row["netChange"] or 0) > 0 and row["advisorCount"] >= 2 and (row["consensus"] or 0) >= 45), None)
    cut = next((row for row in products if (row["netChange"] or 0) < 0 and row["advisorCount"] >= 2 and (row["consensus"] or 0) >= 45), None)
    split = next((row for row in products if row["addCount"] and row["cutCount"] and (row["grossChange"] or 0) >= 10 and (row["consensus"] or 0) < 45), None)
    high = next((row for row in products if row.get("avgFundT20") is not None and row.get("matureFundT20Count", 0) >= 3), None)
    if add:
        cases.append({"title": "一致调入", "headline": f"{add['fundName']} 获 {add['advisorCount']} 家机构净调入", "detail": f"净变化 {pct_text(add['netChange'])}，涉及 {add['strategyUnitCount']} 个策略/系列，主要机构：{'、'.join(add['topAdvisors'])}。"})
    if cut:
        cases.append({"title": "一致调出", "headline": f"{cut['fundName']} 被集中调出", "detail": f"净变化 {pct_text(cut['netChange'])}，涉及 {cut['advisorCount']} 家机构，需结合调仓原因判断是产品观点还是组合再平衡。"})
    if split:
        cases.append({"title": "分歧产品", "headline": f"{split['fundName']} 同时出现加减仓", "detail": f"调整绝对值 {pct_text(split['grossChange'])}，一致性仅 {split['consensus']:.1f}%，适合下钻查看不同机构逻辑。"})
    if high:
        cases.append({"title": "后验观察", "headline": f"{high['fundName']} T+20 样本均值 {pct_text(high['avgFundT20'])}", "detail": f"可评价样本 {high['matureFundT20Count']} 条；这是绝对收益观察，不替代同类超额胜率。"})
    if not cases and strategies:
        top = strategies[0]
        cases.append({"title": "最新动作", "headline": f"{top['advisor']} / {top['unitName']}", "detail": f"{top['rebalanceDate']} 调整 {top['fundCount']} 只基金，净变化 {pct_text(top['netChange'])}。"})
    return cases[:4]


def select_product_slices(products: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    non_cash = [row for row in products if row.get("businessGroup") != "现金承接"]
    opportunities = sorted(
        [row for row in non_cash if (row.get("netChange") or 0) > 0],
        key=lambda row: (-(row.get("advisorCount") or 0), -(row.get("strategyUnitCount") or 0), -(row.get("netChange") or 0), -(row.get("grossChange") or 0)),
    )[:12]
    risks = sorted(
        [row for row in non_cash if (row.get("netChange") or 0) < 0],
        key=lambda row: (-(row.get("advisorCount") or 0), -(row.get("strategyUnitCount") or 0), (row.get("netChange") or 0), -(row.get("grossChange") or 0)),
    )[:12]
    divergences = sorted(
        [
            row
            for row in non_cash
            if row.get("addCount") and row.get("cutCount") and (row.get("grossChange") or 0) >= 5
        ],
        key=lambda row: (-(row.get("grossChange") or 0), row.get("fundName") or ""),
    )[:12]
    cash = sorted(
        [row for row in products if row.get("businessGroup") == "现金承接"],
        key=lambda row: (-(row.get("grossChange") or 0), -(abs(row.get("netChange") or 0)), row.get("fundName") or ""),
    )[:8]
    return {"opportunities": opportunities, "risks": risks, "divergences": divergences, "cashMoves": cash}


def build_broadcasts(
    kpis: dict[str, Any],
    opportunities: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    divergences: list[dict[str, Any]],
    cash_moves: list[dict[str, Any]],
    gf_opportunities: list[dict[str, Any]] | None = None,
    gf_risks: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    broadcasts: list[dict[str, str]] = []
    if opportunities:
        top = opportunities[0]
        broadcasts.append(
            {
                "type": "up",
                "title": "全市场集中增持",
                "text": f"{top['fundName']} 被 {top['advisorCount']} 家机构净调入 {pct_text(top['netChange'])}，涉及 {top['strategyUnitCount']} 个策略/系列，优先核对是否代表同类资产配置上调。",
            }
        )
    if risks:
        top = risks[0]
        broadcasts.append(
            {
                "type": "down",
                "title": "全市场集中减持",
                "text": f"{top['fundName']} 被机构净调出 {pct_text(top['netChange'])}，调整绝对值 {pct_text(top['grossChange'])}，需要区分产品观点转弱还是组合风险再平衡。",
            }
        )
    if divergences:
        top = divergences[0]
        broadcasts.append(
            {
                "type": "focus",
                "title": "机构观点分歧",
                "text": f"{top['fundName']} 同时出现调入和调出，调整绝对值 {pct_text(top['grossChange'])}，一致性 {top.get('consensus', 0):.1f}%，适合下钻比较不同机构理由。",
            }
        )
    if cash_moves:
        top = cash_moves[0]
        broadcasts.append(
            {
                "type": "neutral",
                "title": "现金承接单独看",
                "text": f"{top['fundName']} 调整绝对值 {pct_text(top['grossChange'])}。货币/现金类更多反映风险预算和流动性安排，不与权益机会榜混排。",
            }
        )
    gf_opportunities = gf_opportunities or []
    gf_risks = gf_risks or []
    if gf_opportunities:
        top = gf_opportunities[0]
        broadcasts.append(
            {
                "type": "focus",
                "title": "广发基金增持线索",
                "text": f"{top['fundName']} 在广发基金榜中净调入 {pct_text(top['netChange'])}，涉及 {top['advisorCount']} 家机构、{top['strategyUnitCount']} 个策略/系列。",
            }
        )
    elif gf_risks:
        top = gf_risks[0]
        broadcasts.append(
            {
                "type": "focus",
                "title": "广发基金减持线索",
                "text": f"{top['fundName']} 在广发基金榜中净调出 {pct_text(top['netChange'])}，调整绝对值 {pct_text(top['grossChange'])}。",
            }
        )
    if not broadcasts:
        broadcasts.append(
            {
                "type": "warn",
                "title": "本窗口主动调仓信号不足",
                "text": f"当前纳入主动分析的策略/系列 {kpis.get('marketStrategyUnitCount', 0)} 个，暂未形成明确的基金或资产配置异动。",
            }
        )
    return broadcasts[:4]


def build_substitutions(records: list[dict[str, Any]], peer_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peers_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in peer_rows:
        event_id = str(row.get("event_id") or "")
        if event_id:
            peers_by_event[event_id].append(row)
    rows: list[dict[str, Any]] = []
    for record in records:
        event_id = str(record.get("eventId") or "")
        opposite = "cut" if record.get("direction") == "add" else "add"
        same_key = record.get("fundCode") or record.get("fundName")
        peers = [
            row
            for row in peers_by_event.get(event_id, [])
            if row.get("direction") == opposite and (row.get("fund_code") or row.get("fund_name")) != same_key
        ]
        if not peers:
            continue
        peers = sorted(peers, key=lambda row: abs(row.get("change") or 0), reverse=True)
        total = sum(abs(row.get("change") or 0) for row in peers)
        top = peers[:4]
        rows.append(
            {
                "date": record.get("rebalanceDate") or "",
                "advisor": record.get("advisor") or "",
                "strategyId": record.get("strategyId") or "",
                "strategyName": record.get("unitName") or record.get("strategyName") or "",
                "gfFundCode": record.get("fundCode") or "",
                "gfFundName": record.get("fundName") or "",
                "gfAction": record.get("action") or "",
                "gfChange": record.get("change"),
                "isGfStrategy": bool(record.get("isGfStrategy")),
                "oppositeTotal": round2(total),
                "oppositeFunds": [
                    {
                        "fundCode": row.get("fund_code") or "",
                        "fundName": row.get("fund_name") or "",
                        "fundCompany": row.get("fund_company") or "",
                        "action": row.get("action") or "",
                        "change": round2(row.get("change") or 0),
                        "isGfFund": bool(row.get("is_gf_fund")),
                    }
                    for row in top
                ],
                "reason": record.get("reason") or "",
            }
        )
    return sorted(rows, key=lambda row: (row["date"], row.get("oppositeTotal") or 0), reverse=True)[:40]


def build_conclusions(kpis: dict[str, Any], products: list[dict[str, Any]], internal_products: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not kpis.get("externalStrategyUnitCount"):
        return [
            {
                "type": "warn",
                "title": "外部机构暂无强信号",
                "text": f"本窗口没有通过筛选的外部机构/策略调仓样本；广发内部策略/系列 {kpis.get('internalStrategyUnitCount', 0)} 个，只能作为内部产品观点观察。",
            }
        ]
    net = kpis["externalNetChange"]
    direction = "偏加仓" if net > 0 else ("偏减仓" if net < 0 else "方向均衡")
    cards = [
        {
            "type": "up" if net > 0 else ("down" if net < 0 else "neutral"),
            "title": f"外部方向：{direction}",
            "text": f"外部样本净变化 {pct_text(net)}，调整绝对值 {pct_text(kpis['externalGrossChange'])}，涉及 {kpis['externalAdvisorCount']} 家机构、{kpis['externalStrategyUnitCount']} 个策略/系列。",
        }
    ]
    if products:
        top = products[0]
        cards.append(
            {
                "type": "focus",
                "title": f"最强产品信号：{top['fundName']}",
                "text": f"{top['direction']} {pct_text(top['netChange'])}，外部机构覆盖 {top['advisorCount']} 家，一致性 {top['consensus']:.1f}%，分类为 {top.get('businessGroup') or '未分类'}。",
            }
        )
    mature = kpis.get("externalMatureFundT20Count") or 0
    if mature >= 5:
        cards.append(
            {
                "type": "effect",
                "title": "后验表现可观察",
                "text": f"外部 T+20 可评价产品样本 {mature} 条，平均绝对收益 {pct_text(kpis.get('externalAvgFundT20'))}；用于复盘，不直接等同于产品胜率。",
            }
        )
    else:
        cards.append(
            {
                "type": "warn",
                "title": "后验窗口尚不充分",
                "text": f"T+20 可评价样本 {mature} 条，近期调仓更多用于发现线索，暂不直接下胜率结论。",
            }
        )
    internal_gross = kpis.get("internalGrossChange") or 0
    internal_note = f"广发内部策略调整绝对值 {pct_text(internal_gross)}"
    if internal_products:
        top_internal = internal_products[0]
        internal_note += f"，内部最活跃产品为 {top_internal['fundName']}。"
    cards.append(
        {
            "type": "neutral",
            "title": "内部动作单独隔离",
            "text": internal_note,
        }
    )
    return cards[:5]


def build_executive_summary(
    kpis: dict[str, Any],
    market: dict[str, Any],
    opportunities: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    gf_opportunities: list[dict[str, Any]],
    gf_risks: list[dict[str, Any]],
    cash_moves: list[dict[str, Any]],
    quality_signals: dict[str, Any],
) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    cards.extend(market.get("narrative") or [])
    if opportunities:
        top = opportunities[0]
        cards.append(
            {
                "type": "up",
                "title": f"全市场增持：{top['fundName']}",
                "text": f"{top['advisorCount']} 家机构、{top['strategyUnitCount']} 个策略/系列净调入 {pct_text(top['netChange'])}；质量支持 {top.get('qualitySupport', '待观察')}。",
            }
        )
    if risks:
        top = risks[0]
        cards.append(
            {
                "type": "down",
                "title": f"全市场减持：{top['fundName']}",
                "text": f"净调出 {pct_text(top['netChange'])}，调整绝对值 {pct_text(top['grossChange'])}；优先核对同次替代产品和披露原因。",
            }
        )
    if gf_opportunities:
        top = gf_opportunities[0]
        cards.append(
            {
                "type": "focus",
                "title": f"广发专项增持：{top['fundName']}",
                "text": f"广发基金产品中净调入 {pct_text(top['netChange'])}，涉及 {top['advisorCount']} 家机构、{top['strategyUnitCount']} 个策略/系列。",
            }
        )
    elif gf_risks:
        top = gf_risks[0]
        cards.append(
            {
                "type": "focus",
                "title": f"广发专项减持：{top['fundName']}",
                "text": f"广发基金产品中净调出 {pct_text(top['netChange'])}，调整绝对值 {pct_text(top['grossChange'])}。",
            }
        )
    elif cash_moves and len(cards) < 3:
        top = cash_moves[0]
        cards.append(
            {
                "type": "neutral",
                "title": "现金承接单独观察",
                "text": f"{top['fundName']} 调整绝对值 {pct_text(top['grossChange'])}，更像风险预算来源或流动性安排，不直接代表权益/固收方向性观点。",
            }
        )
    if len(cards) <= 1:
        cards.append(
            {
                "type": "neutral",
                "title": "全市场非现金基金暂无强单边信号",
                "text": "当前主动调仓更多体现结构轮动或小样本动作，页面保留榜单和明细用于后续窗口跟踪。",
            }
        )
    cards.extend(quality_signals.get("narrative") or [])
    return cards[:4]


def signed_flow_text(name: str, value: float | None, *, add_word: str = "增加", cut_word: str = "减少") -> str:
    if value is None:
        return f"{name}--"
    action = add_word if value > 0 else (cut_word if value < 0 else "基本持平")
    return f"{name}{action}{pct_text(abs(value) if action != '基本持平' else 0)}"


def summarize_strategy_performance(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_unit: dict[str, dict[str, Any]] = {}
    for row in records:
        unit_id = row.get("unitId") or row.get("strategyId") or row.get("unitName")
        if not unit_id:
            continue
        value = as_float(row.get("strategyT20"))
        if value is None:
            continue
        current = by_unit.get(str(unit_id))
        if current is None or str(row.get("rebalanceDate") or "") >= str(current.get("rebalanceDate") or ""):
            by_unit[str(unit_id)] = {
                "strategyType": row.get("strategyType") or "未披露策略类型",
                "unitName": row.get("unitName") or row.get("strategyName") or "",
                "advisor": row.get("advisor") or "",
                "rebalanceDate": row.get("rebalanceDate") or "",
                "strategyT20": value,
            }
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in by_unit.values():
        grouped[row["strategyType"]].append(as_float(row.get("strategyT20")) or 0)
    by_type = [
        {
            "strategyType": strategy_type,
            "count": len(values),
            "avgStrategyT20": round2(sum(values) / len(values)) if values else None,
            "positiveCount": sum(1 for value in values if value > 0),
            "negativeCount": sum(1 for value in values if value < 0),
        }
        for strategy_type, values in grouped.items()
        if values
    ]
    by_type = sorted(by_type, key=lambda row: (-(row.get("count") or 0), -(row.get("avgStrategyT20") or -999), row["strategyType"]))[:8]
    all_values = [as_float(row.get("strategyT20")) or 0 for row in by_unit.values()]
    return {
        "horizon": 20,
        "sampleStrategyCount": len(all_values),
        "avgStrategyT20": round2(sum(all_values) / len(all_values)) if all_values else None,
        "positiveCount": sum(1 for value in all_values if value > 0),
        "negativeCount": sum(1 for value in all_values if value < 0),
        "byType": by_type,
    }


def summarize_strategy_type_flows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[row.get("strategyType") or "未披露策略类型"].append(row)
    output: list[dict[str, Any]] = []
    for strategy_type, group in grouped.items():
        asset_counter: dict[str, float] = defaultdict(float)
        fund_type_counter: dict[str, float] = defaultdict(float)
        for row in group:
            change = row.get("change") or 0
            asset_counter[row.get("businessGroup") or "未分类"] += change
            label = row.get("assetLabel") or row.get("fundType") or "未分类"
            fund_type_counter[label] += change
        adds = [
            f"{name}增加{pct_text(value)}"
            for name, value in sorted(asset_counter.items(), key=lambda item: (-item[1], item[0]))
            if value > 0
        ][:2]
        cuts = [
            f"{name}减少{pct_text(abs(value))}"
            for name, value in sorted(asset_counter.items(), key=lambda item: (item[1], item[0]))
            if value < 0
        ][:2]
        fund_adds = [
            f"{name}增加{pct_text(value)}"
            for name, value in sorted(fund_type_counter.items(), key=lambda item: (-item[1], item[0]))
            if value > 0
        ][:2]
        fund_cuts = [
            f"{name}减少{pct_text(abs(value))}"
            for name, value in sorted(fund_type_counter.items(), key=lambda item: (item[1], item[0]))
            if value < 0
        ][:2]
        gross = sum(abs(row.get("change") or 0) for row in group)
        output.append(
            {
                "strategyType": strategy_type,
                "strategyUnitCount": len({row.get("unitId") for row in group if row.get("unitId")}),
                "eventCount": len({row.get("eventId") for row in group if row.get("eventId")}),
                "netChange": round2(sum(row.get("change") or 0 for row in group)) or 0,
                "grossChange": round2(gross) or 0,
                "topAdds": adds,
                "topCuts": cuts,
                "fundTypeAdds": fund_adds,
                "fundTypeCuts": fund_cuts,
            }
        )
    return sorted(output, key=lambda row: (-(row.get("grossChange") or 0), row["strategyType"]))[:6]


def describe_signal_row(row: dict[str, Any] | None, *, add_word: str = "净调入", cut_word: str = "净调出") -> str:
    if not row or not row.get("name"):
        return "未形成集中方向"
    net = row.get("netChange") or 0
    direction = add_word if net > 0 else (cut_word if net < 0 else "方向分歧")
    consensus = row.get("consensus")
    suffix = f"，方向一致性{consensus:.1f}%" if consensus is not None else ""
    return f"{row['name']}{direction}{pct_text(abs(net) if net else 0)}，调整强度{pct_text(row.get('grossChange'))}{suffix}"


def build_business_brief_sections(
    start: date,
    latest: date,
    kpis: dict[str, Any],
    market: dict[str, Any],
    market_context: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    gf_opportunities: list[dict[str, Any]],
    gf_risks: list[dict[str, Any]],
    quality_signals: dict[str, Any],
    quality_strategies: list[dict[str, Any]],
    performance: dict[str, Any],
    type_flows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    raw_strategies = as_int(kpis.get("marketRawStrategyUnitCount"))
    active_strategies = as_int(kpis.get("marketStrategyUnitCount"))
    active_events = as_int(kpis.get("marketEventCount"))
    active_funds = as_int(kpis.get("marketFundCount"))
    raw_events = as_int(kpis.get("marketRawEventCount"))
    market_sorted = sorted(market_context, key=lambda row: row.get("returnPct") or 0, reverse=True)
    market_best = market_sorted[0] if market_sorted else None
    market_worst = market_sorted[-1] if market_sorted else None
    market_bits: list[str] = []
    for row in market_context:
        if row.get("name") in {"沪深300", "创业板指", "中证全债", "上海黄金", "恒生指数", "纳斯达克100"}:
            market_bits.append(f"{row['name']}{pct_text(row.get('returnPct'))}")
    asset = (market.get("assetBuckets") or [{}])[0]
    asset_rows = (market.get("assetBuckets") or [])[:3]
    industry_adds = (market.get("industryAdds") or [])[:3]
    industry_cuts = (market.get("industryCuts") or [])[:3]
    quality_rows = (quality_signals.get("advisors") or [])[:2]
    sections: list[dict[str, str]] = []

    by_type = performance.get("byType") or []
    perf_bits = [
        f"{row['strategyType']}平均{pct_text(row.get('avgStrategyT20'))}（{row.get('count') or 0}个）"
        for row in by_type[:4]
    ]
    best_type = max(by_type, key=lambda row: row.get("avgStrategyT20") or -999, default=None)
    worst_type = min(by_type, key=lambda row: row.get("avgStrategyT20") or 999, default=None)
    if performance.get("sampleStrategyCount"):
        perf_body = (
            f"本窗口可验证调仓后T+20表现的策略{performance.get('sampleStrategyCount')}个，"
            f"平均收益{pct_text(performance.get('avgStrategyT20'))}，上涨样本{performance.get('positiveCount') or 0}个、"
            f"下跌样本{performance.get('negativeCount') or 0}个。"
            f"按策略类型看，{join_items(perf_bits, '类型样本不足', 4)}。"
        )
        if best_type and worst_type and best_type is not worst_type:
            perf_body += f"横向看，{best_type['strategyType']}阶段表现相对更强，{worst_type['strategyType']}相对偏弱。"
        perf_body += "该口径反映调仓后可跟踪收益，不等同于自然月全量投顾产品收益。"
    else:
        perf_body = "当前窗口调仓后T+20收益样本尚未成熟，业绩统计以主要指数同期表现和后续T+20跟踪为主。"
    sections.append({"title": "业绩统计", "body": perf_body})

    market_body = (
        f"同期主要市场表现为{join_items(market_bits, '代表指数样本不足', 6)}。"
        if market_bits
        else "同期代表指数样本不足。"
    )
    if market_best and market_worst:
        market_body += f"其中{market_best['name']}相对更强，{market_worst['name']}相对偏弱。"
    sections.append({"title": "近期市场参照", "body": market_body})

    overview_body = (
        f"{start:%Y年%m月%d日}至{latest:%Y年%m月%d日}，全市场披露{raw_strategies}个策略、{raw_events}个事件存在调仓；"
        f"剔除测试、信号服务、明确建仓、已停止策略及小幅变化后，主动分析口径纳入{active_strategies}个策略、"
        f"{active_events}个有效调仓事件、{active_funds}只基金。"
    )
    sections.append({"title": "调仓行为概览", "body": overview_body})

    asset_bits = [describe_signal_row(row) for row in asset_rows if row.get("name")]
    reason_bits: list[str] = []
    for row in quality_rows:
        reason_bits.extend(row.get("topReasons") or [])
    for row in asset_rows:
        reason_bits.extend(row.get("topReasons") or [])
    reason_bits = [clean_reason_for_text(item) for item in reason_bits if clean_reason_for_text(item)]
    asset_body = (
        f"资产配置方面，{join_items(asset_bits, '未形成单一集中方向', 3)}。"
        f"调仓理由集中在{join_items(reason_bits, '未披露明确原因', 3)}。"
    )
    if asset.get("name"):
        consensus = asset.get("consensus") or 0
        asset_body += (
            f"其中最突出方向的一致性为{consensus:.1f}%，"
            f"{'说明机构在该方向上的共识较强' if consensus >= 50 else '说明更可能是组合内部轮动或机构间分歧'}。"
        )
    sections.append({"title": "调仓理由与资产配置", "body": asset_body})

    flow_bits = []
    for row in type_flows[:4]:
        add_text = join_items(row.get("topAdds") or [], "未形成主要增配方向", 2)
        cut_text = join_items(row.get("topCuts") or [], "未形成主要减配方向", 2)
        flow_bits.append(
            f"{row['strategyType']}（{row.get('strategyUnitCount') or 0}个策略）{add_text}，{cut_text}"
        )
    sections.append({"title": "各类型策略调仓结构", "body": join_items(flow_bits, "策略类型样本不足", 4) + "。"})

    industry_add_bits = [
        f"{row['name']}增持强度{pct_text(row.get('addGross'))}，涉及{row.get('advisorCount') or 0}家机构/{row.get('strategyUnitCount') or 0}个策略"
        for row in industry_adds
    ]
    industry_cut_bits = [
        f"{row['name']}减持强度{pct_text(row.get('cutGross'))}，涉及{row.get('advisorCount') or 0}家机构/{row.get('strategyUnitCount') or 0}个策略"
        for row in industry_cuts
    ]
    sections.append(
        {
            "title": "行业配置",
            "body": f"行业增配主要集中在{join_items(industry_add_bits, '未形成集中增配行业', 3)}；减配主要集中在{join_items(industry_cut_bits, '未形成集中减配行业', 3)}。",
        }
    )

    offshore_rows = [
        row
        for row in asset_rows + (market.get("assetDetails") or [])[:8]
        if any(token in str(row.get("name") or "") for token in ("QDII", "海外", "港股", "美股", "纳斯达克", "商品", "黄金", "原油"))
    ]
    offshore_bits = [describe_signal_row(row) for row in offshore_rows[:3]]
    product_bits: list[str] = []
    if opportunities:
        top = opportunities[0]
        product_bits.append(f"全市场增持最集中的基金是{top['fundName']}，净调入{pct_text(top.get('netChange'))}，涉及{top.get('advisorCount') or 0}家机构")
    if risks:
        top = risks[0]
        product_bits.append(f"减持最集中的基金是{top['fundName']}，净调出{pct_text(abs(top.get('netChange') or 0))}，需结合异动说明区分观点转弱和组合再平衡")
    if gf_opportunities or gf_risks:
        top = (gf_opportunities or gf_risks)[0]
        action = "净调入" if (top.get("netChange") or 0) > 0 else "净调出"
        product_bits.append(f"广发基金产品中{top['fundName']}本期最值得跟踪，{action}{pct_text(abs(top.get('netChange') or 0))}")
    sections.append(
        {
            "title": "QDII/商品与基金异动",
            "body": f"海外、QDII和商品方向上，{join_items(offshore_bits, '未形成集中方向', 3)}。{join_items(product_bits, '基金层面暂无强集中异动', 3)}。",
        }
    )

    qk = quality_signals.get("kpis") or {}
    high_win_count = as_int(qk.get("highWinAdvisorCount"))
    advisor_bits = [
        f"{row['advisor']}调{row.get('strategyUnitCount') or 0}个策略、平均策略换手{pct_text(row.get('avgTurnover'))}，主线为{join_items(row.get('topAssets') or [], '未形成集中资产线', 2)}"
        for row in quality_rows
    ]
    strategy_bits = [
        f"{row['unitName']}单次换手{pct_text(row.get('turnoverRate'))}，资产线{join_items(row.get('topAssets') or [], '未形成集中资产线', 2)}，行业线{join_items(row.get('topIndustries') or [], '未形成集中行业线', 2)}"
        for row in quality_strategies[:2]
    ]
    quality_body = (
        f"历史质量层面有{high_win_count}家机构达到高胜率线。"
        if high_win_count
        else f"历史质量层面暂无机构达到{HIGH_WIN_RATE_FLOOR:.0f}%高胜率线，近期动作更适合做线索跟踪，不宜直接作为强结论。"
    )
    quality_body += f"重点机构：{join_items(advisor_bits, '暂无质量较优机构样本', 2)}。重点策略：{join_items(strategy_bits, '暂无质量较优策略样本', 2)}。"
    sections.append({"title": "重点机构与策略线索", "body": quality_body})
    return sections


def build_business_brief(
    start: date,
    latest: date,
    kpis: dict[str, Any],
    market: dict[str, Any],
    market_context: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    gf_opportunities: list[dict[str, Any]],
    gf_risks: list[dict[str, Any]],
    quality_signals: dict[str, Any],
    quality_strategies: list[dict[str, Any]],
    performance: dict[str, Any],
    type_flows: list[dict[str, Any]],
) -> str:
    sections = build_business_brief_sections(
        start,
        latest,
        kpis,
        market,
        market_context,
        opportunities,
        risks,
        gf_opportunities,
        gf_risks,
        quality_signals,
        quality_strategies,
        performance,
        type_flows,
    )
    return "".join([f"{section['title']}：{section['body']}" for section in sections])


def build_window(
    conn: sqlite3.Connection,
    returns: ReturnCache,
    latest: date,
    window: dict[str, Any],
    quality_by_strategy: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    start = latest - timedelta(days=window["days"] - 1)
    raw = [enrich_row(row) for row in load_rows(conn, start, latest)]
    stats = exclusion_stats(raw)
    usable = [row for row in raw if usable_row(row)]
    market_raw = [enrich_row(row) for row in load_rows(conn, start, latest, only_gf_funds=False)]
    market_stats = exclusion_stats(market_raw)
    market_filter_flow = build_filter_flow(market_raw)
    market_usable = [row for row in market_raw if usable_row(row)]
    market_records = aggregate_market_records(market_usable, quality_by_strategy)
    market_unit_records = aggregate_unit_records(market_usable, returns, quality_by_strategy)
    external_market_records = [row for row in market_records if not row["isGfStrategy"]]
    prev_start = start - timedelta(days=window["days"])
    prev_end = start - timedelta(days=1)
    prev_market_raw = [enrich_row(row) for row in load_rows(conn, prev_start, prev_end, only_gf_funds=False)]
    prev_market_usable = [row for row in prev_market_raw if usable_row(row)]
    prev_market_records = aggregate_market_records(prev_market_usable, quality_by_strategy)
    event_ids = {str(row.get("event_id")) for row in usable if row.get("event_id")}
    peer_rows: list[dict[str, Any]] = []
    if event_ids:
        peer_rows = [
            enrich_row(row)
            for row in load_rows(conn, start, latest, event_ids=event_ids, only_gf_funds=False)
        ]
        peer_rows = [row for row in peer_rows if usable_row(row)]
    records = aggregate_unit_records(usable, returns, quality_by_strategy)
    external_records = [row for row in records if not row["isGfStrategy"]]
    internal_records = [row for row in records if row["isGfStrategy"]]
    market_net = sum(row["change"] or 0 for row in market_unit_records)
    market_gross = sum(abs(row["change"] or 0) for row in market_unit_records)
    market_advisors = {row["advisor"] for row in market_usable if row.get("advisor")}
    market_units = {row["unit_id"] for row in market_usable if row.get("unit_id")}
    market_funds = {row.get("fund_code") or row["fund_name"] for row in market_usable}
    market_events = {row.get("event_id") for row in market_usable if row.get("event_id")}
    net = sum(row["change"] or 0 for row in records)
    gross = sum(abs(row["change"] or 0) for row in records)
    external_net = sum(row["change"] or 0 for row in external_records)
    external_gross = sum(abs(row["change"] or 0) for row in external_records)
    internal_net = sum(row["change"] or 0 for row in internal_records)
    internal_gross = sum(abs(row["change"] or 0) for row in internal_records)
    advisors = {row["advisor"] for row in records if row["advisor"]}
    units = {row["unitId"] for row in records if row["unitId"]}
    funds = {row["fundCode"] or row["fundName"] for row in records}
    external_advisors = {row["advisor"] for row in external_records if row["advisor"]}
    external_units = {row["unitId"] for row in external_records if row["unitId"]}
    external_funds = {row["fundCode"] or row["fundName"] for row in external_records}
    internal_units = {row["unitId"] for row in internal_records if row["unitId"]}
    internal_funds = {row["fundCode"] or row["fundName"] for row in internal_records}
    mature_fund = [row["fundT20"] for row in records if row.get("fundT20") is not None]
    mature_strategy = [row["strategyT20"] for row in records if row.get("strategyT20") is not None]
    external_mature_fund = [row["fundT20"] for row in external_records if row.get("fundT20") is not None]
    external_mature_strategy = [row["strategyT20"] for row in external_records if row.get("strategyT20") is not None]
    kpis = {
        "rawRows": len(raw),
        "usableRows": len(records),
        "marketRawRows": len(market_raw),
        "marketRawStrategyUnitCount": len({row["unit_id"] for row in market_raw if row.get("unit_id")}),
        "marketRawEventCount": len({row.get("event_id") for row in market_raw if row.get("event_id")}),
        "marketUsableRawRows": len(market_usable),
        "marketEventCount": len(market_events),
        "marketAdvisorCount": len(market_advisors),
        "marketStrategyUnitCount": len(market_units),
        "marketFundCount": len(market_funds),
        "marketNetChange": round2(market_net) or 0,
        "marketGrossChange": round2(market_gross) or 0,
        "advisorCount": len(advisors),
        "strategyUnitCount": len(units),
        "fundCount": len(funds),
        "externalUnitCount": len(external_units),
        "externalUnitShare": (len(external_units) / len(units) * 100) if units else 0,
        "externalAdvisorCount": len(external_advisors),
        "externalStrategyUnitCount": len(external_units),
        "externalFundCount": len(external_funds),
        "internalStrategyUnitCount": len(internal_units),
        "internalFundCount": len(internal_funds),
        "addCount": sum(1 for row in records if row["direction"] == "add"),
        "cutCount": sum(1 for row in records if row["direction"] == "cut"),
        "netChange": round2(net) or 0,
        "grossChange": round2(gross) or 0,
        "externalNetChange": round2(external_net) or 0,
        "externalGrossChange": round2(external_gross) or 0,
        "internalNetChange": round2(internal_net) or 0,
        "internalGrossChange": round2(internal_gross) or 0,
        "avgFundT20": round2(sum(mature_fund) / len(mature_fund)) if mature_fund else None,
        "matureFundT20Count": len(mature_fund),
        "avgStrategyT20": round2(sum(mature_strategy) / len(mature_strategy)) if mature_strategy else None,
        "matureStrategyT20Count": len(mature_strategy),
        "externalAvgFundT20": round2(sum(external_mature_fund) / len(external_mature_fund)) if external_mature_fund else None,
        "externalMatureFundT20Count": len(external_mature_fund),
        "externalAvgStrategyT20": round2(sum(external_mature_strategy) / len(external_mature_strategy)) if external_mature_strategy else None,
        "externalMatureStrategyT20Count": len(external_mature_strategy),
    }
    market_products = summarize_products(market_unit_records)
    market_product_slices = select_product_slices(market_products)
    market_institutions = summarize_institutions(market_unit_records)
    market_strategies = summarize_strategies(market_unit_records)
    products = summarize_products(records)
    external_products = summarize_products(external_records)
    internal_products = summarize_products(internal_records)
    product_slices = select_product_slices(products)
    market_signals = summarize_market_signals(market_records, prev_market_records)
    quality_signals = summarize_quality_advisors(market_records)
    high_win_strategies = summarize_quality_strategies(market_unit_records)
    market_context = load_market_context(conn, start, latest)
    performance_summary = summarize_strategy_performance(market_unit_records)
    type_flows = summarize_strategy_type_flows(market_unit_records)
    brief_sections = build_business_brief_sections(
        start,
        latest,
        kpis,
        market_signals,
        market_context,
        market_product_slices["opportunities"],
        market_product_slices["risks"],
        product_slices["opportunities"],
        product_slices["risks"],
        quality_signals,
        high_win_strategies,
        performance_summary,
        type_flows,
    )
    institutions = summarize_institutions(records)
    external_institutions = summarize_institutions(external_records)
    internal_institutions = summarize_institutions(internal_records)
    strategies = summarize_strategies(records)
    external_strategies = summarize_strategies(external_records)
    internal_strategies = summarize_strategies(internal_records)
    return {
        "key": window["key"],
        "label": window["label"],
        "startDate": start.isoformat(),
        "endDate": latest.isoformat(),
        "minAbsChange": MIN_ABS_CHANGE,
        "kpis": kpis,
        "excluded": stats,
        "marketExcluded": market_stats,
        "filterFlow": build_filter_flow(raw),
        "marketFilterFlow": market_filter_flow,
        "brief": build_business_brief(
            start,
            latest,
            kpis,
            market_signals,
            market_context,
            market_product_slices["opportunities"],
            market_product_slices["risks"],
            product_slices["opportunities"],
            product_slices["risks"],
            quality_signals,
            high_win_strategies,
            performance_summary,
            type_flows,
        ),
        "briefSections": brief_sections,
        "performanceSummary": performance_summary,
        "strategyTypeFlows": type_flows,
        "summary": build_executive_summary(
            kpis,
            market_signals,
            market_product_slices["opportunities"],
            market_product_slices["risks"],
            product_slices["opportunities"],
            product_slices["risks"],
            market_product_slices["cashMoves"],
            quality_signals,
        ),
        "market": {
            **market_signals,
            "rawRows": len(market_raw),
            "usableRows": len(market_usable),
            "previousStartDate": prev_start.isoformat(),
            "previousEndDate": prev_end.isoformat(),
        },
        "marketContext": market_context,
        "qualitySignals": quality_signals,
        "highWinStrategies": high_win_strategies,
        "conclusions": build_conclusions(kpis, external_products, internal_products),
        "broadcasts": build_broadcasts(
            kpis,
            market_product_slices["opportunities"],
            market_product_slices["risks"],
            market_product_slices["divergences"],
            market_product_slices["cashMoves"],
            product_slices["opportunities"],
            product_slices["risks"],
        ),
        "products": market_products,
        "allProducts": market_products,
        "gfProducts": products,
        "externalGfProducts": external_products,
        "internalProducts": internal_products,
        "opportunities": market_product_slices["opportunities"],
        "risks": market_product_slices["risks"],
        "divergences": market_product_slices["divergences"],
        "cashMoves": market_product_slices["cashMoves"],
        "gfOpportunities": product_slices["opportunities"],
        "gfRisks": product_slices["risks"],
        "gfDivergences": product_slices["divergences"],
        "gfCashMoves": product_slices["cashMoves"],
        "institutions": market_institutions,
        "allInstitutions": institutions,
        "internalInstitutions": internal_institutions,
        "strategies": market_strategies,
        "allStrategies": strategies,
        "internalStrategies": internal_strategies,
        "matrix": build_matrix(market_unit_records, market_products, market_institutions),
        "substitutions": build_substitutions(external_records, peer_rows),
        "cases": build_cases(market_products, market_strategies),
        "details": sorted(market_unit_records, key=lambda row: row["rebalanceDate"], reverse=True)[:300],
    }


def build_payload(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    latest = load_latest_date(conn)
    algorithm = latest_algorithm(conn)
    returns = ReturnCache(conn, algorithm)
    quality_by_strategy = load_strategy_quality(conn)
    windows = {window["key"]: build_window(conn, returns, latest, window, quality_by_strategy) for window in WINDOWS}
    return {
        "meta": {
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "dbPath": str(db_path),
            "latestRebalanceDate": latest.isoformat(),
            "algorithm": algorithm,
            "minAbsChange": MIN_ABS_CHANGE,
            "horizons": HORIZONS,
            "defaultWindow": "30d",
            "scope": "全市场主动调仓监控；默认剔除测试、信号服务、明确建仓事件和已停止策略，首次观察到的普通调仓不再默认剔除。目标盈运行期次按同系列合并，广发基金产品作为专项榜单单独展示。",
        },
        "windows": windows,
    }


def system_topbar_html(active: str = "gf_rebalance") -> str:
    return render_system_topbar(active)


def render_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    generated = escape(payload["meta"]["generatedAt"])
    latest = escape(payload["meta"]["latestRebalanceDate"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>全市场调仓监控</title>
  <style>
    :root {{
      --bg:#f5f8fb; --panel:#ffffff; --line:#dfe8f2; --ink:#122033; --muted:#607086;
      --blue:#2563eb; --teal:#0f8a7a; --orange:#d97706; --red:#dc2626; --purple:#7c3aed;
      --soft-blue:#e8f2ff; --soft-teal:#e6f6f3; --soft-red:#fff0f0; --soft-orange:#fff7e8;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif; }}
    a {{ color:inherit; text-decoration:none; border-bottom:1px solid rgba(37,99,235,.25); }}
    .topbar {{ position:sticky; top:0; z-index:20; background:rgba(255,255,255,.96); border-bottom:1px solid var(--line); }}
    .topbar-inner {{ max-width:1440px; margin:0 auto; padding:12px 24px; display:flex; align-items:center; justify-content:space-between; gap:20px; }}
    .brand {{ display:inline-flex; align-items:center; gap:10px; min-width:220px; color:#182230; border-bottom:0; }}
    .brand-mark {{ width:34px; height:34px; display:inline-grid; place-items:center; background:#166c77; color:#fff; border-radius:6px; font-weight:800; }}
    .brand small {{ display:block; color:var(--muted); font-size:12px; margin-top:1px; }}
    .nav {{ display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }}
    .nav-link {{ padding:7px 10px; border-radius:6px; color:#405063; font-size:14px; border-bottom:0; font-weight:500; }}
    .nav-link:hover,.nav-link.is-active {{ background:#eef3f8; color:#0f4f58; }}
    {SIDEBAR_CSS}
    .page {{ max-width:1420px; margin:0 auto; padding:44px 48px 64px; }}
    .hero {{ display:grid; grid-template-columns:1fr 420px; gap:32px; align-items:start; border-bottom:3px solid var(--line); padding-bottom:28px; }}
    .eyebrow {{ color:#0f766e; font-weight:800; letter-spacing:0; margin-bottom:10px; }}
    h1 {{ font-size:42px; line-height:1.12; margin:0 0 14px; letter-spacing:0; }}
    .lead {{ font-size:18px; color:var(--muted); font-weight:700; max-width:880px; }}
    .period {{ background:#e9f4ff; border:1px solid #cfe2f5; border-radius:8px; padding:22px 26px; }}
    .period b {{ display:block; color:#2563eb; margin-bottom:8px; }}
    .period .range {{ font-size:28px; line-height:1.2; font-weight:900; margin-bottom:10px; }}
    .toolbar {{ display:flex; justify-content:space-between; gap:16px; align-items:center; margin:22px 0 16px; flex-wrap:wrap; }}
    .seg {{ display:flex; gap:8px; flex-wrap:wrap; }}
    button {{ border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:6px; padding:8px 13px; font-weight:800; cursor:pointer; }}
    button.active {{ color:#fff; background:#122033; border-color:#122033; }}
    .hint {{ color:var(--muted); font-size:13px; }}
    .kpis {{ display:grid; grid-template-columns:repeat(6,1fr); gap:14px; margin:18px 0 22px; }}
    .kpi {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px 18px; min-height:104px; }}
    .kpi .value {{ font-size:30px; line-height:1; font-weight:900; margin-bottom:10px; }}
    .kpi .label {{ color:#53657b; font-weight:800; }}
    .section {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:22px 26px; margin:20px 0; }}
    .section h2 {{ font-size:25px; margin:0 0 8px; }}
    .section p.note {{ margin:0 0 16px; color:var(--muted); font-weight:700; }}
    .brief-card {{ border:1px solid #d9e4ee; border-left:8px solid var(--blue); border-radius:8px; background:#f8fbff; padding:18px 22px; }}
    .brief-card p {{ margin:0; color:#223042; font-size:16px; line-height:1.8; font-weight:760; }}
    .brief-sections {{ display:grid; gap:12px; }}
    .brief-section {{ background:#fff; border:1px solid #dfe8f1; border-radius:8px; padding:13px 15px; }}
    .brief-section h3 {{ margin:0 0 6px; font-size:16px; color:#122033; }}
    .brief-section p {{ margin:0; color:#34465d; font-size:14px; line-height:1.75; font-weight:720; }}
    .market-context {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }}
    .market-context .tag {{ background:#fff; border:1px solid #dfe8f1; }}
    .definition-note {{ margin-top:12px; padding:10px 12px; background:#fff; border:1px solid #dfe8f1; border-radius:8px; color:#415169; font-size:13px; font-weight:760; line-height:1.55; }}
    .filter-detail {{ margin-top:14px; border-top:1px solid #dfe8f1; padding-top:12px; }}
    .filter-detail summary {{ cursor:pointer; color:#0f4f58; font-weight:900; }}
    .filter-flow {{ display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin-top:12px; }}
    .filter-step {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:10px 12px; min-height:102px; }}
    .filter-step b {{ display:block; font-size:14px; margin-bottom:7px; }}
    .filter-step .count {{ font-size:18px; font-weight:900; color:#122033; }}
    .filter-step .memo {{ margin-top:6px; font-size:12px; color:var(--muted); line-height:1.45; font-weight:700; }}
    .signal-list {{ display:grid; gap:12px; }}
    .signal-row {{ display:grid; grid-template-columns:150px 1fr 112px; gap:12px; align-items:center; padding:10px 0; border-bottom:1px solid #e7edf4; }}
    .signal-row:last-child {{ border-bottom:0; }}
    .signal-name {{ font-weight:900; }}
    .signal-meta {{ color:var(--muted); font-size:12px; font-weight:760; margin-top:2px; }}
    .meter {{ height:16px; background:#edf2f7; border-radius:5px; overflow:hidden; }}
    .meter span {{ display:block; height:100%; border-radius:5px; }}
    .quality-list {{ display:grid; gap:10px; }}
    .quality-card {{ border:1px solid var(--line); border-radius:8px; padding:13px 14px; background:#fbfdff; }}
    .quality-card b {{ display:block; margin-bottom:4px; }}
    .quality-card .meta {{ color:var(--muted); font-size:12px; font-weight:760; }}
    .industry-block + .industry-block {{ margin-top:18px; padding-top:14px; border-top:1px solid #e3ebf3; }}
    .industry-block h3 {{ margin:0 0 8px; font-size:16px; }}
    details.section > summary {{ cursor:pointer; font-size:21px; font-weight:900; list-style:none; }}
    details.section > summary::-webkit-details-marker {{ display:none; }}
    details.section > summary::after {{ content:"展开"; float:right; font-size:13px; color:var(--muted); padding-top:5px; }}
    details.section[open] > summary::after {{ content:"收起"; }}
    .conclusions {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }}
    .conclusion {{ border-radius:8px; padding:16px 18px; background:#f8fbff; border-left:7px solid var(--blue); min-height:126px; }}
    .conclusion.up {{ border-left-color:var(--red); background:var(--soft-red); }}
    .conclusion.down {{ border-left-color:var(--teal); background:var(--soft-teal); }}
    .conclusion.warn {{ border-left-color:var(--orange); background:var(--soft-orange); }}
    .conclusion.focus {{ border-left-color:var(--purple); }}
    .conclusion h3 {{ margin:0 0 8px; font-size:17px; }}
    .conclusion p {{ margin:0; color:#415169; font-weight:700; }}
    .broadcasts {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }}
    .broadcast {{ border:1px solid var(--line); border-radius:8px; background:#fbfdff; padding:15px 16px; min-height:118px; }}
    .broadcast.up {{ border-left:6px solid var(--red); }}
    .broadcast.down {{ border-left:6px solid var(--teal); }}
    .broadcast.warn {{ border-left:6px solid var(--orange); }}
    .broadcast.focus {{ border-left:6px solid var(--purple); }}
    .broadcast h3 {{ margin:0 0 8px; font-size:16px; }}
    .broadcast p {{ margin:0; color:#415169; font-weight:700; }}
    .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
    .grid2 > *, .section {{ min-width:0; }}
    .product-block {{ margin-bottom:22px; overflow-x:auto; }}
    .product-block h3 {{ font-size:20px; margin:0 0 10px; }}
    .product-block:last-child {{ margin-bottom:0; }}
    .subhead {{ margin:24px 0 12px; padding-top:18px; border-top:2px solid #dfe8f1; color:#122033; font-size:21px; font-weight:900; }}
    .fund-cell {{ min-width:240px; }}
    .anomaly-cell {{ min-width:360px; max-width:560px; color:#34465d; line-height:1.55; font-weight:700; }}
    .more-list {{ margin-top:8px; }}
    .more-list summary,.more-group summary {{ cursor:pointer; color:#0f4f58; font-weight:900; padding:6px 0; }}
    .more-group {{ margin-top:14px; border-top:1px solid #e1e9f1; padding-top:12px; }}
    table {{ width:100%; border-collapse:collapse; }}
    #productTable,#substitutionTable,#institutionTable,#strategyTable {{ max-width:100%; overflow-x:auto; }}
    th {{ background:#edf4fb; color:#314157; font-size:13px; text-align:left; padding:10px 9px; white-space:nowrap; }}
    td {{ border-bottom:1px solid #e7edf4; padding:9px; vertical-align:top; }}
    tr:hover td {{ background:#fbfdff; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .pos {{ color:var(--red); font-weight:900; }}
    .neg {{ color:var(--teal); font-weight:900; }}
    .tag {{ display:inline-flex; align-items:center; border-radius:999px; padding:2px 8px; background:#eef4f8; color:#516174; font-size:12px; font-weight:800; margin:0 4px 4px 0; }}
    .barrow {{ display:grid; grid-template-columns:140px 1fr 72px; gap:10px; align-items:center; margin:9px 0; }}
    .bar {{ height:22px; background:#edf2f7; border-radius:5px; overflow:hidden; position:relative; }}
    .bar span {{ display:block; height:100%; border-radius:5px; }}
    .matrix-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:8px; }}
    .matrix {{ min-width:720px; display:grid; gap:1px; background:#e5edf5; }}
    .mcell {{ min-height:44px; padding:6px; background:#fff; font-size:12px; display:flex; align-items:center; justify-content:center; text-align:center; cursor:pointer; }}
    .mhead {{ background:#f1f6fb; font-weight:900; color:#415169; cursor:default; }}
    .mcell.posbg {{ background:#fff0f0; color:#dc2626; font-weight:900; }}
    .mcell.negbg {{ background:#e5f7f3; color:#0f766e; font-weight:900; }}
    .cases {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; }}
    .case {{ border:1px solid var(--line); border-radius:8px; background:#fbfdff; padding:16px; min-height:130px; }}
    .case b {{ display:block; font-size:16px; margin-bottom:8px; }}
    .case .type {{ color:#0f766e; font-weight:900; margin-bottom:8px; }}
    .opposite-list {{ color:#415169; font-weight:700; }}
    .opposite-list span {{ display:block; margin-bottom:4px; }}
    .drawer {{ position:fixed; inset:0; display:none; background:rgba(8,18,33,.35); z-index:20; align-items:center; justify-content:center; padding:24px; }}
    .drawer.active {{ display:flex; }}
    .drawer-panel {{ width:min(820px,96vw); max-height:86vh; overflow:auto; background:#fff; border-radius:10px; padding:24px; box-shadow:0 20px 70px rgba(0,0,0,.25); }}
    .drawer-panel h3 {{ margin:0 0 14px; font-size:22px; }}
    .quality {{ color:#52667d; font-size:13px; }}
    details.section section {{ min-width:0; overflow:hidden; margin-top:20px; }}
    @media (max-width:1100px) {{
      .topbar-inner {{ align-items:flex-start; flex-direction:column; }}
      .page {{ padding:28px 18px 52px; }}
      .hero,.grid2 {{ grid-template-columns:1fr; }}
      .kpis {{ grid-template-columns:repeat(2,1fr); }}
      .conclusions,.cases,.broadcasts,.filter-flow {{ grid-template-columns:1fr; }}
      .signal-row {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
{system_topbar_html('gf_rebalance')}
<div class="page">
  <header class="hero">
    <div>
      <div class="eyebrow">基金投顾策略库 · 全市场调仓监控</div>
      <h1>全市场调仓监控简报</h1>
      <div class="lead">先看全市场投顾策略的资产配置风向、基金增减持和历史质量较优机构动作，再把广发基金产品作为专项榜单单独跟踪。</div>
    </div>
    <aside class="period">
      <b>统计口径</b>
      <div class="range" id="periodRange">--</div>
      <div>全市场调仓先展示原始触达，再用主动调仓样本做观点分析；剔除测试、信号服务、明确建仓和已停止策略，首次观察到的普通调仓不再默认剔除。目标盈期次按系列合并。高胜率线：有效调仓不少于 {QUALITY_EFFECTIVE_FLOOR} 次且胜率不低于 {HIGH_WIN_RATE_FLOOR:.0f}%。</div>
    </aside>
  </header>

  <div class="toolbar">
    <div class="seg" id="windowButtons"></div>
    <div class="hint">最新调仓日：{latest}；页面生成：{generated}</div>
  </div>

  <section class="section">
    <h2>本期调仓简报</h2>
    <div id="summaryBox"></div>
  </section>

  <section class="kpis" id="kpiBox"></section>

  <section class="grid2">
    <div class="section">
      <h2>资产配置风向</h2>
      <p class="note">看全市场有效主动调仓在大类资产上的净变化、调整强度和方向一致性。</p>
      <div id="assetSignals"></div>
    </div>
    <div class="section">
      <h2>行业配置风向</h2>
      <p class="note">按行业增持强度和减持强度分别展示 top3，并标注参与机构和策略数。</p>
      <div id="industrySignals"></div>
    </div>
  </section>

  <section class="grid2">
    <div class="section">
      <h2>历史质量较优机构调仓动态</h2>
      <p class="note">按机构汇总历史调仓质量，本期观点优先看核心资产和幅度最大的基金；货币/现金承接单独提示，不进入主观点排序，未达到高胜率线的样本只作观察级线索。</p>
      <div id="qualityAdvisorSignals"></div>
    </div>
    <div class="section">
      <h2>历史质量较优调仓策略动态</h2>
      <p class="note">按策略展示本期换手率、资产/行业变化和核心调仓思路，用于下钻复盘。</p>
      <div id="qualityStrategySignals"></div>
    </div>
  </section>

  <section class="section">
      <h2>基金增减持榜</h2>
      <p class="note">全市场基金增持榜、减持榜各占完整行；每只入榜基金给出异动说明。下方单独展示广发基金增持榜、减持榜，格式保持一致。</p>
      <div id="productTable"></div>
  </section>

  <details class="section">
    <summary>支撑明细</summary>
    <section>
      <h2>机构观点矩阵</h2>
      <p class="note">行是机构，列是本窗口调整强度靠前的基金。红色代表净调入，绿色代表净调出；点击格子查看命中策略和原因。</p>
      <div class="matrix-wrap"><div class="matrix" id="matrix"></div></div>
    </section>

    <section>
      <h2>广发基金同次替代关系</h2>
      <p class="note">专项观察同一次调仓里，策略加仓广发产品时减掉了哪些产品，或减仓广发产品时买入了哪些产品，用于判断替代逻辑。</p>
      <div id="substitutionTable"></div>
    </section>

    <section class="grid2">
      <div>
      <h2>机构排行</h2>
      <p class="note">用于识别哪些机构近期在集中表达资产配置或基金选择观点。</p>
      <div id="institutionTable"></div>
      </div>
      <div>
      <h2>策略/系列动作</h2>
      <p class="note">目标盈按系列合并展示；策略名称可跳转现有策略详情页。</p>
      <div id="strategyTable"></div>
      </div>
    </section>

    <section class="grid2">
      <div>
        <h2>典型案例观察</h2>
        <p class="note">只保留能代表本窗口特征的案例，不把明细表当结论。</p>
        <div class="cases" id="caseBox"></div>
      </div>
      <div>
        <h2>调仓后观察</h2>
        <p class="note">展示产品调仓后 T+20 绝对收益观察；当前不是同类超额胜率。</p>
        <div id="effectBars"></div>
      </div>
    </section>
  </details>

  <section class="section quality">
    <h2>口径与质量说明</h2>
    <div id="qualityBox"></div>
  </section>
</div>

<div class="drawer" id="drawer" role="dialog" aria-modal="true">
  <div class="drawer-panel">
    <button style="float:right" onclick="closeDrawer()">关闭</button>
    <h3 id="drawerTitle">明细</h3>
    <div id="drawerBody"></div>
  </div>
</div>

<script>
window.__GF_REBALANCE_MONITOR__ = {data};
const payload = window.__GF_REBALANCE_MONITOR__;
let currentKey = payload.meta.defaultWindow || "30d";

function fmtPct(v) {{
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "--";
  const n = Number(v);
  return `${{n > 0 ? "+" : ""}}${{n.toFixed(2)}}%`;
}}
function cls(v) {{ return Number(v || 0) > 0 ? "pos" : (Number(v || 0) < 0 ? "neg" : ""); }}
function esc(s) {{ return String(s ?? "").replace(/[&<>"']/g, m => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m])); }}
function colorizePctText(s) {{
  return esc(s).replace(/([+-]?\\d+(?:\\.\\d+)?%)/g, (m) => {{
    const n = Number.parseFloat(m);
    if (Number.isNaN(n) || n === 0) return m;
    return `<span class="${{n > 0 ? "pos" : "neg"}}">${{m}}</span>`;
  }});
}}
function strategyLink(row) {{ return row.strategyId ? `<a href="./strategy.html?id=${{encodeURIComponent(row.strategyId)}}">${{esc(row.unitName || row.strategyName)}}</a>` : esc(row.unitName || row.strategyName); }}
function fundLink(row) {{ return row.fundCode ? `<a href="./fund.html?code=${{encodeURIComponent(row.fundCode)}}">${{esc(row.fundName)}}</a>` : esc(row.fundName); }}
function fundAnchor(code, name) {{ return code ? `<a href="./fund.html?code=${{encodeURIComponent(code)}}">${{esc(name || code)}}</a>` : esc(name || ""); }}
function strategyAnchor(id, name) {{ return id ? `<a href="./strategy.html?id=${{encodeURIComponent(id)}}">${{esc(name || id)}}</a>` : esc(name || ""); }}
function cleanReasonText(s) {{ return String(s || "").trim().replace(/[。；;、,.，\\s]+$/g, ""); }}
function joinText(items, fallback = "未披露", sep = "、") {{
  const values = (items || []).map(cleanReasonText).filter(Boolean);
  return values.length ? values.map(esc).join(sep) : fallback;
}}

function table(headers, rows) {{
  return `<table><thead><tr>${{headers.map(h=>`<th class="${{h.num?'num':''}}">${{h.t}}</th>`).join("")}}</tr></thead><tbody>${{rows.join("") || `<tr><td colspan="${{headers.length}}">暂无样本</td></tr>`}}</tbody></table>`;
}}

function renderButtons() {{
  const box = document.getElementById("windowButtons");
  box.innerHTML = Object.values(payload.windows).map(w => `<button class="${{w.key===currentKey?'active':''}}" data-key="${{w.key}}">${{w.label}}</button>`).join("");
  box.querySelectorAll("button").forEach(btn => btn.addEventListener("click", () => {{ currentKey = btn.dataset.key; render(); }}));
}}

function renderSummary(w) {{
  const flow = w.marketFilterFlow || [];
  const marketChips = (w.marketContext || []).map(row => `<span class="tag">${{esc(row.name)}} <span class="${{cls(row.returnPct)}}">${{fmtPct(row.returnPct)}}</span></span>`).join("");
  const sectionHtml = (w.briefSections || []).length
    ? `<div class="brief-sections">${{(w.briefSections || []).map(section => {{
        const body = /业绩|市场/.test(section.title || "") ? colorizePctText(section.body || "") : esc(section.body || "");
        return `<div class="brief-section"><h3>${{esc(section.title || "")}}</h3><p>${{body}}</p></div>`;
      }}).join("")}}</div>`
    : `<p>${{esc(w.brief || "本窗口暂无可解释信号。")}}</p>`;
  const flowHtml = flow.map(step => `
    <div class="filter-step">
      <b>${{esc(step.name)}}</b>
      <div class="count">${{step.strategyUnitCount || 0}} 策略 / ${{step.eventCount || 0}} 事件</div>
      <div class="memo">${{esc(step.note || "")}}</div>
    </div>`).join("");
  document.getElementById("summaryBox").innerHTML = `
    <div class="brief-card">
      ${{sectionHtml}}
      <div class="market-context">${{marketChips}}</div>
      <div class="definition-note"><b>如何理解方向一致性：</b>方向一致性=净变化绝对值/调整强度。接近100%表示多数调仓同向，机构共识更强；接近0%表示同时有加仓和减仓，更可能是结构轮动或机构分歧，不应简单解读为单边看多或看空。</div>
      <details class="filter-detail">
        <summary>查看原始调仓如何纳入主动分析</summary>
        <div class="filter-flow">${{flowHtml}}</div>
      </details>
    </div>`;
}}

function renderKpis(w) {{
  const k = w.kpis;
  const m = (w.market && w.market.kpis) || {{}};
  const q = (w.qualitySignals && w.qualitySignals.kpis) || {{}};
  const cards = [
    ["调仓策略（原始）", k.marketRawStrategyUnitCount || 0, "个"],
    ["纳入主动分析", k.marketStrategyUnitCount || m.strategyUnitCount || 0, "个"],
    ["有效调仓事件", k.marketEventCount || 0, "个"],
    ["全市场调整强度", fmtPct(k.marketGrossChange || m.grossChange || 0), ""],
    ["全市场方向一致性", `${{Number(m.consensus || 0).toFixed(1)}}%`, ""],
    ["高胜率机构", q.highWinAdvisorCount || 0, "家"]
  ];
  document.getElementById("kpiBox").innerHTML = cards.map(([label,val,unit]) => `<div class="kpi"><div class="value">${{val}}${{unit?`<span style="font-size:16px">${{unit}}</span>`:""}}</div><div class="label">${{label}}</div></div>`).join("");
}}

function renderMarketSignals(w) {{
  const renderRows = (rows, valueField = "grossChange") => {{
    const maxGross = Math.max(1, ...rows.map(r => Math.abs(Number(r[valueField]) || Number(r.grossChange) || 0)));
    return rows.map(row => {{
      const value = Number(row[valueField]) || Number(row.grossChange) || 0;
    const width = Math.max(3, Math.abs(value) / maxGross * 100);
    const displayValue = valueField === "cutGross" ? -Math.abs(value) : (valueField === "addGross" ? Math.abs(value) : Number(row.netChange || 0));
    const color = displayValue >= 0 ? "var(--red)" : "var(--teal)";
    const valueLabel = valueField === "addGross" ? "增持强度" : (valueField === "cutGross" ? "减持强度" : "净变化");
    return `<div class="signal-row">
      <div><div class="signal-name">${{esc(row.name)}}</div><div class="signal-meta">${{esc(row.strength)}} · ${{row.advisorCount}}家 / ${{row.strategyUnitCount}}策略</div></div>
      <div><div class="meter"><span style="width:${{width}}%;background:${{color}}"></span></div><div class="signal-meta">上期净变化 ${{fmtPct(row.previousNetChange)}}，变化 ${{fmtPct(row.netDelta)}}，方向一致性 ${{Number(row.consensus || 0).toFixed(1)}}%</div></div>
      <div class="num ${{cls(displayValue)}}">${{fmtPct(displayValue)}}<div class="signal-meta">${{valueLabel}}；净 ${{fmtPct(row.netChange)}}</div></div>
    </div>`;
    }}).join("");
  }};
  const assetRows = ((w.market && w.market.assetBuckets) || []).slice(0, 6);
  document.getElementById("assetSignals").innerHTML = assetRows.length
    ? `<div class="signal-list">${{renderRows(assetRows)}}</div><div class="hint" style="margin-top:10px">方向一致性=净变化绝对值/调整强度。越高表示机构越单边，越低表示分歧或轮动越明显。</div>`
    : "<div class='hint'>本窗口全市场有效调仓样本不足。</div>";
  const addRows = ((w.market && w.market.industryAdds) || []).slice(0, 3);
  const cutRows = ((w.market && w.market.industryCuts) || []).slice(0, 3);
  const industryBlock = (title, rows, field) => `<div class="industry-block"><h3>${{title}}</h3>${{rows.length ? `<div class="signal-list">${{renderRows(rows, field)}}</div>` : "<div class='hint'>暂无行业样本。</div>"}}</div>`;
  document.getElementById("industrySignals").innerHTML =
    industryBlock("行业增持强度 Top3", addRows, "addGross") +
    industryBlock("行业减持强度 Top3", cutRows, "cutGross");
}}

function renderQualitySignals(w) {{
  const rows = ((w.qualitySignals && w.qualitySignals.advisors) || []).slice(0, 5);
  const advisorsHtml = rows.map(row => `
    <div class="quality-card">
      <b>${{esc(row.advisor)}} <span class="tag">${{esc(row.qualityBand)}}</span></b>
      <div class="meta">${{esc(row.qualityText)}}；本期调 ${{row.strategyUnitCount || 0}} 个策略，平均策略换手 ${{fmtPct(row.avgTurnover)}}；核心资产调整 ${{fmtPct(row.coreGrossChange || row.currentGrossChange)}}，净变化 <span class="${{cls(row.coreNetChange || row.currentNetChange)}}">${{fmtPct(row.coreNetChange || row.currentNetChange)}}</span></div>
      <div style="margin-top:7px"><b style="display:inline">调仓思路：</b>${{joinText(row.topAssets, "未分类")}}；核心基金：${{joinText((row.topFunds || []).slice(0,3), "未披露")}}；原因：${{joinText(row.topReasons, "未披露明确原因", "；")}}</div>
    </div>`).join("") || "<div class='hint'>本窗口没有可评价的历史调仓质量样本。</div>";
  const strategyRows = (w.highWinStrategies || []).slice(0, 5);
  const strategyHtml = strategyRows.length ? `
      ${{strategyRows.map(row => `
        <div class="quality-card">
          <b>${{strategyAnchor(row.strategyId, row.unitName)}} <span class="tag">${{esc(row.qualityBand)}}</span><span class="tag">${{esc(row.strategyType || "未披露策略类型")}}</span></b>
          <div class="meta">${{esc(row.qualityText)}}；赔率 ${{row.odds !== null && row.odds !== undefined ? Number(row.odds).toFixed(2) : "--"}}；${{esc(row.rebalanceDate)}} 单次换手 ${{fmtPct(row.turnoverRate || row.grossChange)}}，净变化 <span class="${{cls(row.netChange)}}">${{fmtPct(row.netChange)}}</span></div>
          <div style="margin-top:7px"><b style="display:inline">资产/行业：</b>${{joinText(row.topAssets, "未分类")}} / ${{joinText(row.topIndustries, "未形成集中行业线")}}；核心基金：${{joinText((row.topFunds || []).slice(0,3), "未披露")}}；原因：${{joinText(row.topReasons, "未披露明确原因", "；")}}</div>
        </div>`).join("")}}
    ` : "<div class='hint'>本窗口没有历史质量较优的策略调仓样本。</div>";
  document.getElementById("qualityAdvisorSignals").innerHTML = advisorsHtml;
  document.getElementById("qualityStrategySignals").innerHTML = strategyHtml;
}}

function renderBroadcasts(w) {{
  document.getElementById("broadcastBox").innerHTML = (w.broadcasts || []).map(c => `<div class="broadcast ${{c.type || ""}}"><h3>${{esc(c.title)}}</h3><p>${{esc(c.text)}}</p></div>`).join("") || "<div class='hint'>本窗口暂无可播报信号。</div>";
}}

function renderConclusions(w) {{
  document.getElementById("conclusionBox").innerHTML = w.conclusions.map(c => `<div class="conclusion ${{c.type}}"><h3>${{esc(c.title)}}</h3><p>${{esc(c.text)}}</p></div>`).join("");
}}

function renderProducts(w) {{
  const headers = [
    {{t:"基金/分类"}},{{t:"异动说明"}},{{t:"方向"}},{{t:"净变化",num:true}},{{t:"调整强度",num:true}},{{t:"机构/策略"}},{{t:"方向一致性",num:true}},{{t:"质量支持"}},{{t:"T+20",num:true}}
  ];
  const renderRow = row => `<tr>
    <td class="fund-cell">${{fundLink(row)}}<div>${{row.businessGroup?`<span class="tag">${{esc(row.businessGroup)}}</span>`:""}}${{row.assetLabel?`<span class="tag">${{esc(row.assetLabel)}}</span>`:""}}${{row.themeLabel?`<span class="tag">${{esc(row.themeLabel)}}</span>`:""}}</div></td>
    <td class="anomaly-cell">${{esc(row.anomalyText || "")}}</td>
    <td>${{esc(row.direction)}}</td>
    <td class="num ${{cls(row.netChange)}}">${{fmtPct(row.netChange)}}</td>
    <td class="num">${{fmtPct(row.grossChange)}}</td>
    <td>${{row.advisorCount}}家 / ${{row.strategyUnitCount}}策略</td>
    <td class="num">${{row.consensus !== null && row.consensus !== undefined ? Number(row.consensus).toFixed(1)+"%" : "--"}}</td>
    <td>${{esc(row.qualitySupport || "质量观察")}}</td>
    <td class="num ${{cls(row.avgFundT20)}}">${{fmtPct(row.avgFundT20)}}</td>
  </tr>`;
  const renderBlock = (title, rows, emptyText, limit = 3) => {{
    const body = rows.map(renderRow);
    const topRows = body.slice(0, limit);
    const extraRows = body.slice(limit);
    const topTable = table(headers, topRows.length ? topRows : [`<tr><td colspan="9">${{esc(emptyText)}}</td></tr>`]);
    const more = extraRows.length
      ? `<details class="more-list"><summary>展开其余 ${{extraRows.length}} 条</summary>${{table(headers, extraRows)}}</details>`
      : "";
    return `<div class="product-block"><h3>${{esc(title)}}</h3>${{topTable}}${{more}}</div>`;
  }};
  document.getElementById("productTable").innerHTML = [
    renderBlock("全市场基金增持榜", w.opportunities || [], "本窗口暂无非现金类净调入基金。"),
    renderBlock("全市场基金减持榜", w.risks || [], "本窗口暂无非现金类净调出基金。"),
    `<div class="subhead">广发基金专项榜单</div>`,
    renderBlock("广发基金增持榜", w.gfOpportunities || [], "本窗口暂无广发基金净调入样本。"),
    renderBlock("广发基金减持榜", w.gfRisks || [], "本窗口暂无广发基金净调出样本。")
  ].join("");
}}

function renderEffects(w) {{
  const products = w.products.filter(x => x.avgFundT20 !== null && x.avgFundT20 !== undefined).slice(0, 10);
  const maxAbs = Math.max(1, ...products.map(x => Math.abs(Number(x.avgFundT20)||0)));
  document.getElementById("effectBars").innerHTML = products.map(row => {{
    const width = Math.max(4, Math.abs(row.avgFundT20) / maxAbs * 100);
    const color = row.avgFundT20 >= 0 ? "var(--red)" : "var(--teal)";
    return `<div class="barrow"><div>${{fundLink(row)}}</div><div class="bar"><span style="width:${{width}}%;background:${{color}}"></span></div><div class="num ${{cls(row.avgFundT20)}}">${{fmtPct(row.avgFundT20)}}</div></div>`;
  }}).join("") || "<div class='hint'>本窗口暂无成熟的 T+20 产品收益样本。</div>";
}}

function renderMatrix(w) {{
  const m = w.matrix;
  const byKey = new Map(m.cells.map(c => [`${{c.advisor}}\\u0000${{c.fundName}}`, c]));
  const cols = m.funds.length + 1;
  const html = [`<div class="mcell mhead">机构 / 产品</div>`, ...m.funds.map(f => `<div class="mcell mhead">${{esc(f)}}</div>`)];
  m.advisors.forEach(advisor => {{
    html.push(`<div class="mcell mhead">${{esc(advisor)}}</div>`);
    m.funds.forEach(fund => {{
      const cell = byKey.get(`${{advisor}}\\u0000${{fund}}`);
      if (!cell) html.push(`<div class="mcell"></div>`);
      else {{
        const bg = cell.netChange > 0 ? "posbg" : "negbg";
        html.push(`<div class="mcell ${{bg}}" data-cell='${{esc(JSON.stringify(cell))}}'>${{fmtPct(cell.netChange)}}<br><span style="font-weight:700;color:#607086">${{cell.count}}条</span></div>`);
      }}
    }});
  }});
  const box = document.getElementById("matrix");
  box.style.gridTemplateColumns = `160px repeat(${{Math.max(1, m.funds.length)}}, 132px)`;
  box.innerHTML = html.join("");
  box.querySelectorAll("[data-cell]").forEach(el => el.addEventListener("click", () => openCell(JSON.parse(el.dataset.cell))));
}}

function renderInstitutions(w) {{
  const rows = w.institutions.slice(0, 16).map(row => `<tr>
    <td>${{esc(row.advisor)}}${{row.isGfStrategyProvider?'<span class="tag">广发相关策略</span>':''}}</td>
    <td class="num ${{cls(row.netChange)}}">${{fmtPct(row.netChange)}}</td>
    <td class="num">${{fmtPct(row.grossChange)}}</td>
    <td class="num">${{row.fundCount}}</td>
    <td class="num">${{row.strategyUnitCount}}</td>
    <td>${{row.topFunds.map(esc).join("、")}}</td>
  </tr>`);
  document.getElementById("institutionTable").innerHTML = table([
    {{t:"机构"}},{{t:"净变化",num:true}},{{t:"调整绝对值",num:true}},{{t:"基金",num:true}},{{t:"策略/系列",num:true}},{{t:"主要产品"}}
  ], rows);
}}

function renderStrategies(w) {{
  const rows = w.strategies.slice(0, 18).map(row => `<tr>
    <td>${{strategyLink(row)}}${{row.isTargetSeries?'<span class="tag">目标盈合并</span>':''}}</td>
    <td>${{esc(row.advisor)}}</td>
    <td>${{esc(row.rebalanceDate)}}</td>
    <td class="num ${{cls(row.netChange)}}">${{fmtPct(row.netChange)}}</td>
    <td>${{row.topFunds.map(esc).join("、")}}</td>
    <td>${{esc(row.reason)}}</td>
  </tr>`);
  document.getElementById("strategyTable").innerHTML = table([
    {{t:"策略/系列"}},{{t:"机构"}},{{t:"日期"}},{{t:"净变化",num:true}},{{t:"主要基金"}},{{t:"披露原因"}}
  ], rows);
}}

function renderSubstitutions(w) {{
  const rows = (w.substitutions || []).slice(0, 24).map(row => `<tr>
    <td>${{strategyAnchor(row.strategyId, row.strategyName)}}<div><span class="tag">${{esc(row.advisor)}}</span></div></td>
    <td>${{esc(row.date)}}</td>
    <td>${{fundAnchor(row.gfFundCode, row.gfFundName)}}<div><span class="tag">${{esc(row.gfAction)}}</span></div></td>
    <td class="num ${{cls(row.gfChange)}}">${{fmtPct(row.gfChange)}}</td>
    <td class="opposite-list">${{(row.oppositeFunds || []).map(f => `<span>${{fundAnchor(f.fundCode, f.fundName)}} <b class="${{cls(f.change)}}">${{esc(f.action)}} ${{fmtPct(f.change)}}</b>${{f.isGfFund?'<span class="tag">广发</span>':''}}</span>`).join("")}}</td>
    <td class="num">${{fmtPct(row.oppositeTotal)}}</td>
    <td>${{esc(row.reason)}}</td>
  </tr>`);
  document.getElementById("substitutionTable").innerHTML = table([
    {{t:"策略/系列"}},{{t:"日期"}},{{t:"广发产品动作"}},{{t:"变化",num:true}},{{t:"同次反向产品"}},{{t:"反向绝对值",num:true}},{{t:"披露原因"}}
  ], rows);
}}

function renderCases(w) {{
  document.getElementById("caseBox").innerHTML = w.cases.map(c => `<div class="case"><div class="type">${{esc(c.title)}}</div><b>${{esc(c.headline)}}</b><div>${{esc(c.detail)}}</div></div>`).join("") || "<div class='hint'>暂无典型案例。</div>";
}}

function renderQuality(w) {{
  const ex = w.excluded;
  const mex = w.marketExcluded || ex;
  document.getElementById("qualityBox").innerHTML = `
    <p><b>数据来源：</b>本地 SQLite 分析库的策略调仓事件、策略调仓明细、策略治理标签、基金信息、基金经济暴露快照、基金日度净值、策略标准业绩净值。</p>
    <p><b>原始调仓与主动分析：</b>“调仓策略（原始）”按全市场调仓明细原始去重，尽量和调仓明细保持一致；“纳入主动分析”再剔除小幅变化、测试组合、信号服务、明确建仓和已停止策略，用于形成资产配置观点。</p>
    <p><b>主动调仓定义：</b>调仓明细中基金权重变化绝对值 ≥ ${{w.minAbsChange}}pct；只剔除标题或动作明确写有“建仓”的事件，事件序号为 1 但标题为普通“调仓”的记录仍参与主动分析。目标盈运行期次按“投顾机构 + 去期次后的系列名”合并。</p>
    <p><b>胜率与赔率：</b>胜率比较每次调仓后仓位与调仓前仓位在下一调仓日前的收益，调后仓位明显跑赢记为胜，明显跑输记为负，±0.05pct 内记为平且进入分母；赔率=平均正超额 / 平均负超额绝对值，用来观察胜的时候赚多少、错的时候亏多少。</p>
    <p><b>方向一致性：</b>方向一致性=净变化绝对值/调整强度。高一致性代表调仓方向更单边，低一致性代表同一资产、行业或基金同时被加仓和减仓，业务上更应理解为机构分歧或组合内部替换。</p>
    <p><b>市场风向：</b>使用全市场有效主动调仓样本，并与上一同长度窗口比较；广发基金产品只是专项榜单，不作为本报告的唯一关注范围。</p>
    <p><b>广发专项：</b>广发基金增持榜、减持榜只从全市场有效主动调仓中筛出基金公司或基金名称属于广发基金的产品，用于观察广发产品被配置或替代的情况。</p>
    <p><b>本窗口全市场剔除：</b>原始调仓明细 ${{mex.rawRows}} 条；低于门槛 ${{mex.belowThreshold}} 条、测试 ${{mex.testRows}} 条、信号服务 ${{mex.signalRows}} 条、明确建仓 ${{mex.initialRows}} 条、已停止 ${{mex.stoppedRows}} 条。事件序号为 1 但无建仓证据的普通调仓明细 ${{mex.firstObservedNotInitialRows || 0}} 条不再默认剔除。广发基金专项原始明细 ${{ex.rawRows}} 条。</p>
    <p><b>后验观察：</b>T+20 产品表现使用基金累计净值从调仓日后首个可用净值点到 T+20 后首个可用净值点计算；策略表现使用统一回放净值。当前页面只展示绝对收益观察，不把它解释为同类超额胜率。</p>
    <p><b>基金分类：</b>资产/行业/主题来自基金经济暴露快照；页面只展示分类标签，不把标签直接当作百分比暴露。货币/现金类归入“现金承接”，不与权益/主题、固收、商品类机会风险混排。</p>
    <p><b>同次替代关系：</b>从触达广发基金产品的调仓事件中读取同事件全部基金明细；展示广发产品动作的反方向基金，用于初步判断替代或风险预算来源。</p>`;
}}

function openCell(cell) {{
  document.getElementById("drawerTitle").textContent = `${{cell.advisor}} / ${{cell.fundName}}`;
  document.getElementById("drawerBody").innerHTML = table([
    {{t:"策略"}},{{t:"日期"}},{{t:"动作"}},{{t:"变化",num:true}},{{t:"原因"}}
  ], cell.details.map(row => `<tr><td>${{row.strategyId?`<a href="./strategy.html?id=${{encodeURIComponent(row.strategyId)}}">${{esc(row.strategyName)}}</a>`:esc(row.strategyName)}}</td><td>${{esc(row.date)}}</td><td>${{esc(row.action)}}</td><td class="num ${{cls(row.change)}}">${{fmtPct(row.change)}}</td><td>${{esc(row.reason)}}</td></tr>`));
  document.getElementById("drawer").classList.add("active");
}}
function closeDrawer() {{ document.getElementById("drawer").classList.remove("active"); }}
document.getElementById("drawer").addEventListener("click", ev => {{ if (ev.target.id === "drawer") closeDrawer(); }});

function render() {{
  renderButtons();
  const w = payload.windows[currentKey] || payload.windows["30d"];
  document.getElementById("periodRange").textContent = `${{w.startDate}} 至 ${{w.endDate}}`;
  renderSummary(w); renderKpis(w); renderMarketSignals(w); renderQualitySignals(w); renderProducts(w); renderEffects(w); renderMatrix(w); renderSubstitutions(w); renderInstitutions(w); renderStrategies(w); renderCases(w); renderQuality(w);
}}
render();
</script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成全市场主动调仓监控专题 HTML。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = args.db_path.resolve()
    output = (args.output or (args.site_dir.resolve() / "gf-rebalance-monitor.html")).resolve()
    if not db_path.exists():
        raise SystemExit(f"analysis db not found: {db_path}")
    payload = build_payload(db_path)
    html = render_html(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(json.dumps({"output": str(output), "latestRebalanceDate": payload["meta"]["latestRebalanceDate"], "windows": list(payload["windows"].keys())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
