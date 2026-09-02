# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from statistics import mean, median
from typing import Any

from basic_data_navigation import SIDEBAR_CSS, render_system_topbar
from report_periods import previous_completed_month

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.patches import Patch


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
REPORT_ROOT = PROJECT_ROOT / "site"
DEFAULT_SITE_DIR = REPORT_ROOT / "basic_data"
FALLBACK_SITE_DIR = PROJECT_ROOT / "site" / "basic_data"
DEFAULT_MONTH = previous_completed_month()
BROAD_EQUITY_BUCKET_ORDER = [f"L{index}" for index in range(11)]

TOKENS = {
    "surface": "#FCFCFD",
    "panel": "#FFFFFF",
    "ink": "#1F2430",
    "muted": "#6F768A",
    "grid": "#E6E8F0",
    "axis": "#D7DBE7",
}
NEUTRAL_MARKS = {
    "open": TOKENS["panel"],
    "xlight": "#F4F5F7",
    "light": "#E2E5EA",
    "base": "#C5CAD3",
    "mid": "#7A828F",
    "dark": "#464C55",
}
COLOR_FAMILIES = {
    "blue": {"open": TOKENS["panel"], "xlight": "#EAF1FE", "light": "#CEDFFE", "base": "#A3BEFA", "mid": "#5477C4", "dark": "#2E4780"},
    "gold": {"open": TOKENS["panel"], "xlight": "#FFF4C2", "light": "#FFEA8F", "base": "#FFE15B", "mid": "#B8A037", "dark": "#736422"},
    "orange": {"open": TOKENS["panel"], "xlight": "#FFEDDE", "light": "#FFBDA1", "base": "#F0986E", "mid": "#CC6F47", "dark": "#804126"},
    "olive": {"open": TOKENS["panel"], "xlight": "#D8ECBD", "light": "#BEEB96", "base": "#A3D576", "mid": "#71B436", "dark": "#386411"},
    "pink": {"open": TOKENS["panel"], "xlight": "#FCDAD6", "light": "#F5BACC", "base": "#F390CA", "mid": "#BD569B", "dark": "#8A3A6F"},
}


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def raw(value: Any) -> str:
    return "" if value is None else str(value)


def num(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def round2(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, 2)


def pct(value: Any, digits: int = 2, signed: bool = True) -> str:
    number = num(value)
    if number is None:
        return "--"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.{digits}f}%"


def int_text(value: Any) -> str:
    number = num(value, 0) or 0
    return f"{int(round(number)):,}"


def safe_div(a: float, b: float) -> float | None:
    return None if not b else a / b


def month_bounds(month: str) -> tuple[date, date, date]:
    start = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    if start.month == 12:
        next_start = date(start.year + 1, 1, 1)
    else:
        next_start = date(start.year, start.month + 1, 1)
    end = next_start - timedelta(days=1)
    prev_end = start - timedelta(days=1)
    return start, end, prev_end


def parse_js_assignment(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    idx = text.find("=")
    if idx < 0:
        raise ValueError(f"cannot parse JS assignment: {path}")
    payload = text[idx + 1 :].strip()
    if payload.endswith(";"):
        payload = payload[:-1]
    return json.loads(payload)


def parse_month_pack(path: Path, month: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r'\["' + re.escape(month) + r'"\]\s*=\s*(.*);\s*$', text, flags=re.S)
    if not match:
        raise ValueError(f"cannot find month assignment {month}: {path}")
    return json.loads(match.group(1))


def table_rows(table: dict[str, Any]) -> list[dict[str, Any]]:
    fields = table.get("fields") or []
    return [dict(zip(fields, row)) for row in table.get("rows") or []]


def data_dir_for(site_dir: Path) -> Path:
    if (site_dir / "data" / "basic_summary_core.js").exists():
        return site_dir / "data"
    return FALLBACK_SITE_DIR / "data"


def load_summary(data_dir: Path) -> dict[str, Any]:
    payload = parse_js_assignment(data_dir / "basic_summary_core.js")
    return payload.get("summary", payload)


def load_rebalance_month(data_dir: Path, month: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pack = parse_month_pack(data_dir / "insight_rebalance_months" / f"{month}.js", month)
    return table_rows(pack["策略资产变化明细"]), table_rows(pack["调仓基金月度汇总"])


def load_rebalance_month_optional(data_dir: Path, month: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = data_dir / "insight_rebalance_months" / f"{month}.js"
    if not path.exists():
        return [], []
    return load_rebalance_month(data_dir, month)


def strategy_id(row: dict[str, Any]) -> str:
    return raw(row.get("统一策略ID"))


def is_regular_strategy(row: dict[str, Any]) -> bool:
    if raw(row.get("数据完整性")) != "完整":
        return False
    if raw(row.get("研报产品类型")) == "持仓缺失/不入池":
        return False
    if str(row.get("是否纳入常规排名", "1")) in {"0", "否", "False", "false"}:
        return False
    if str(row.get("是否测试组合", "0")) in {"1", "是", "True", "true"}:
        return False
    if str(row.get("是否信号类组合", "0")) in {"1", "是", "True", "true"}:
        return False
    if str(row.get("是否已停止", "0")) in {"1", "是", "True", "true"}:
        return False
    return True


def is_client_strategy(row: dict[str, Any]) -> bool:
    if raw(row.get("天天当前对客展示")) == "是":
        return True
    text = f"{row.get('渠道') or ''} {row.get('投顾机构') or ''}"
    return "广发基金" in text


def is_gf(row: dict[str, Any]) -> bool:
    text = f"{row.get('渠道') or ''} {row.get('投顾机构') or ''} {row.get('策略名称') or ''} {row.get('基金公司') or ''} {row.get('基金名称') or ''}"
    return "广发" in text


def group_sum(
    rows: list[dict[str, Any]],
    key: str,
    *,
    value: str = "净增配",
    min_abs: float = 0.0,
    extra: list[str] | None = None,
) -> list[dict[str, Any]]:
    extra = extra or []
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = raw(row.get(key)).strip()
        if not label or label in {"-", "--", "未识别", "未分类", "其他"}:
            continue
        item = grouped.setdefault(
            label,
            {
                "名称": label,
                "净增配": 0.0,
                "加仓权重": 0.0,
                "减仓权重": 0.0,
                "绝对净增配": 0.0,
                "调前权重": 0.0,
                "调后权重": 0.0,
                "明细数": 0,
                "策略": set(),
                "对客策略": set(),
            },
        )
        net = num(row.get(value), 0) or 0
        item["净增配"] += net
        item["加仓权重"] += num(row.get("加仓权重"), 0) or 0
        item["减仓权重"] += num(row.get("减仓权重"), 0) or 0
        item["绝对净增配"] += abs(net)
        item["调前权重"] += num(row.get("调前权重"), 0) or 0
        item["调后权重"] += num(row.get("调后权重"), 0) or 0
        item["明细数"] += int(num(row.get("明细数"), 0) or 0)
        sid = strategy_id(row)
        if sid:
            item["策略"].add(sid)
            if raw(row.get("天天当前对客展示")) == "是" or raw(row.get("是否广发策略")) == "是":
                item["对客策略"].add(sid)
        for field in extra:
            if row.get(field):
                item[field] = row.get(field)
    result = []
    for item in grouped.values():
        if abs(item["净增配"]) < min_abs and item["绝对净增配"] < min_abs:
            continue
        item["策略数"] = len(item.pop("策略"))
        item["对客策略数"] = len(item.pop("对客策略"))
        item["净增配"] = round2(item["净增配"]) or 0
        item["加仓权重"] = round2(item["加仓权重"]) or 0
        item["减仓权重"] = round2(item["减仓权重"]) or 0
        item["绝对净增配"] = round2(item["绝对净增配"]) or 0
        item["调前权重"] = round2(item["调前权重"]) or 0
        item["调后权重"] = round2(item["调后权重"]) or 0
        result.append(item)
    return sorted(result, key=lambda item: (-abs(item["净增配"]), -item["绝对净增配"], item["名称"]))


def fund_group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = raw(row.get("基金代码"))
        name = raw(row.get("基金名称"))
        key = code or name
        if not key:
            continue
        item = grouped.setdefault(
            key,
            {
                "基金代码": code,
                "基金名称": name,
                "基金公司": raw(row.get("基金公司")),
                "基金类型": raw(row.get("基金类型")),
                "研报大类资产": raw(row.get("研报大类资产")),
                "研报A股行业": raw(row.get("研报A股行业")),
                "权益行业主题": raw(row.get("权益行业主题")),
                "是否广发基金": raw(row.get("是否广发基金")),
                "净增配": 0.0,
                "加仓权重": 0.0,
                "减仓权重": 0.0,
                "调仓策略数": 0,
                "调仓事件数": 0,
                "调仓后收益贡献": 0.0,
            },
        )
        item["净增配"] += num(row.get("净增配"), 0) or 0
        item["加仓权重"] += num(row.get("加仓权重"), 0) or 0
        item["减仓权重"] += num(row.get("减仓权重"), 0) or 0
        item["调仓策略数"] += int(num(row.get("调仓策略数"), 0) or 0)
        item["调仓事件数"] += int(num(row.get("调仓事件数"), 0) or 0)
        item["调仓后收益贡献"] += num(row.get("调仓后收益贡献"), 0) or 0
        for field in ["基金公司", "基金类型", "研报大类资产", "研报A股行业", "权益行业主题"]:
            if not item.get(field) and row.get(field):
                item[field] = raw(row.get(field))
    result = []
    for item in grouped.values():
        item["净增配"] = round2(item["净增配"]) or 0
        item["加仓权重"] = round2(item["加仓权重"]) or 0
        item["减仓权重"] = round2(item["减仓权重"]) or 0
        item["调仓后收益贡献"] = round2(item["调仓后收益贡献"]) or 0
        result.append(item)
    return sorted(result, key=lambda item: (-abs(item["净增配"]), -item["调仓策略数"], item["基金名称"]))


def latest_algorithm(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT 算法版本
        FROM 策略标准业绩净值
        GROUP BY 算法版本
        ORDER BY COUNT(*) DESC
        LIMIT 1
        """
    ).fetchone()
    return row[0]


def load_monthly_returns(
    conn: sqlite3.Connection,
    algorithm: str,
    strategy_map: dict[str, dict[str, Any]],
    regular_ids: set[str],
    month: str,
) -> list[dict[str, Any]]:
    start, end, prev_end = month_bounds(month)
    query_start = (start - timedelta(days=45)).isoformat()
    rows = conn.execute(
        """
        SELECT 统一策略ID, 交易日期, 标准费后单位净值
        FROM 策略标准业绩净值
        WHERE 算法版本 = ?
          AND 交易日期 >= ?
          AND 交易日期 <= ?
        ORDER BY 统一策略ID, 交易日期
        """,
        (algorithm, query_start, end.isoformat()),
    ).fetchall()
    by_sid: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for sid, dt, nav in rows:
        value = num(nav)
        if sid in regular_ids and value and value > 0:
            by_sid[sid].append((dt, value))

    result: list[dict[str, Any]] = []
    for sid, values in by_sid.items():
        start_candidates = [(dt, nav) for dt, nav in values if dt <= prev_end.isoformat()]
        end_candidates = [(dt, nav) for dt, nav in values if dt <= end.isoformat()]
        if not start_candidates or not end_candidates:
            continue
        start_dt, start_nav = start_candidates[-1]
        end_dt, end_nav = end_candidates[-1]
        if start_dt >= start.isoformat() or end_dt < start.isoformat() or start_nav <= 0:
            continue
        strategy = strategy_map.get(sid) or {}
        month_return = (end_nav / start_nav - 1) * 100
        result.append(
            {
                "统一策略ID": sid,
                "策略名称": raw(strategy.get("策略名称")),
                "投顾机构": raw(strategy.get("投顾机构")),
                "渠道": raw(strategy.get("渠道")),
                "研报产品类型": raw(strategy.get("研报产品类型") or "未分类"),
                "基准风险资产权重": raw(strategy.get("基准风险资产权重") or "未分档"),
                "研报股票子类型": raw(strategy.get("研报股票子类型")),
                "业务分类": raw(strategy.get("业务分类")),
                "风险等级": raw(strategy.get("风险等级")),
                "是否对客": "是" if is_client_strategy(strategy) else "否",
                "是否广发": "是" if is_gf(strategy) else "否",
                "月收益": round2(month_return),
                "今年以来": round2(num(strategy.get("今年以来"))),
                "近一年": round2(num(strategy.get("近1年"))),
                "最大回撤": round2(num(strategy.get("最大回撤"))),
                "月初基准日": start_dt,
                "月末日期": end_dt,
            }
        )
    return result


def summarize_performance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["研报产品类型"] or "未分类"].append(row)
    result = []
    for label, items in grouped.items():
        returns = [num(row.get("月收益")) for row in items if num(row.get("月收益")) is not None]
        returns = [x for x in returns if x is not None]
        if not returns:
            continue
        q25, q75 = np.percentile(returns, [25, 75])
        result.append(
            {
                "研报产品类型": label,
                "样本数": len(returns),
                "对客样本数": sum(1 for row in items if row.get("是否对客") == "是"),
                "平均收益": round2(mean(returns)),
                "中位收益": round2(median(returns)),
                "上四分位": round2(float(q75)),
                "下四分位": round2(float(q25)),
                "正收益占比": round2(sum(1 for x in returns if x > 0) / len(returns) * 100),
            }
        )
    order = {"纯债型": 0, "固收+型": 1, "股债混合型": 2, "股票型": 3, "多元配置型": 4}
    return sorted(result, key=lambda row: (order.get(row["研报产品类型"], 99), row["研报产品类型"]))


def load_event_records(
    conn: sqlite3.Connection,
    month: str,
    strategy_map: dict[str, dict[str, Any]],
    regular_ids: set[str],
) -> list[dict[str, Any]]:
    start, end, _ = month_bounds(month)
    rows = conn.execute(
        """
        WITH detail AS (
          SELECT 调仓事件ID,
                 SUM(ABS(COALESCE(权重变化_百分比, 0))) AS gross_change,
                 SUM(CASE WHEN ABS(COALESCE(权重变化_百分比, 0)) >= 0.5 THEN 1 ELSE 0 END) AS changed_details,
                 COUNT(DISTINCT 基金代码) AS fund_count
          FROM 策略调仓明细
          WHERE 调仓日期 >= ? AND 调仓日期 <= ?
          GROUP BY 调仓事件ID
        )
        SELECT e.调仓事件ID, e.统一策略ID, e.调仓日期, e.调仓标题, e.调仓原因,
               detail.gross_change, detail.changed_details, detail.fund_count
        FROM 策略调仓事件 e
        JOIN detail ON detail.调仓事件ID = e.调仓事件ID
        WHERE e.调仓日期 >= ? AND e.调仓日期 <= ?
          AND detail.gross_change >= 0.5
        ORDER BY e.调仓日期, e.调仓事件ID
        """,
        (start.isoformat(), end.isoformat(), start.isoformat(), end.isoformat()),
    ).fetchall()
    result = []
    for row in rows:
        sid = row[1]
        if sid not in regular_ids:
            continue
        strategy = strategy_map.get(sid) or {}
        result.append(
            {
                "调仓事件ID": row[0],
                "统一策略ID": sid,
                "调仓日期": row[2],
                "策略名称": raw(strategy.get("策略名称")),
                "投顾机构": raw(strategy.get("投顾机构")),
                "研报产品类型": raw(strategy.get("研报产品类型") or "未分类"),
                "基准风险资产权重": raw(strategy.get("基准风险资产权重") or "未分档"),
                "业务分类": raw(strategy.get("业务分类")),
                "是否对客": "是" if is_client_strategy(strategy) else "否",
                "是否广发": "是" if is_gf(strategy) else "否",
                "调仓标题": raw(row[3]),
                "调仓原因": raw(row[4]),
                "单次换手": round2(num(row[5], 0) or 0),
                "变化明细数": int(num(row[6], 0) or 0),
                "涉及基金数": int(num(row[7], 0) or 0),
            }
        )
    return result


def fallback_event_records_from_asset_rows(
    asset_rows: list[dict[str, Any]],
    strategy_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    event_rows = [row for row in asset_rows if raw(row.get("研报大类资产"))] or asset_rows
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in event_rows:
        sid = strategy_id(row)
        dt = raw(row.get("调仓日期"))
        if not sid or not dt:
            continue
        strategy = strategy_map.get(sid) or {}
        item = grouped.setdefault(
            (sid, dt),
            {
                "调仓事件ID": f"{sid}__{dt}",
                "统一策略ID": sid,
                "调仓日期": dt,
                "策略名称": raw(strategy.get("策略名称") or row.get("策略名称")),
                "投顾机构": raw(strategy.get("投顾机构") or row.get("投顾机构")),
                "研报产品类型": raw(strategy.get("研报产品类型") or row.get("研报产品类型") or "未分类"),
                "基准风险资产权重": raw(strategy.get("基准风险资产权重") or row.get("基准风险资产权重") or "未分档"),
                "业务分类": raw(strategy.get("业务分类") or row.get("业务分类")),
                "是否对客": "是"
                if is_client_strategy(strategy) or raw(row.get("天天当前对客展示")) == "是" or raw(row.get("是否广发策略")) == "是"
                else "否",
                "是否广发": "是" if is_gf(strategy) or raw(row.get("是否广发策略")) == "是" else "否",
                "调仓标题": "",
                "调仓原因": "",
                "单次换手": 0.0,
                "变化明细数": 0,
                "涉及基金数": 0,
            },
        )
        item["单次换手"] += abs(num(row.get("净增配"), 0) or num(row.get("绝对净增配"), 0) or 0)
        item["变化明细数"] += int(num(row.get("明细数"), 0) or 0)
        item["涉及基金数"] += int(num(row.get("明细数"), 0) or 0)
    result = []
    for item in grouped.values():
        item["单次换手"] = round2(item.get("单次换手")) or 0
        result.append(item)
    return sorted(result, key=lambda row: (row["调仓日期"], row["调仓事件ID"]))


def summarize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in events:
        label = row.get("研报产品类型") or "未分类"
        item = grouped.setdefault(label, {"研报产品类型": label, "调仓事件数": 0, "调仓策略": set(), "对客策略": set(), "总换手": 0.0, "涉及基金数": 0})
        item["调仓事件数"] += 1
        item["调仓策略"].add(row["统一策略ID"])
        if row.get("是否对客") == "是":
            item["对客策略"].add(row["统一策略ID"])
        item["总换手"] += num(row.get("单次换手"), 0) or 0
        item["涉及基金数"] += int(num(row.get("涉及基金数"), 0) or 0)
    result = []
    for item in grouped.values():
        event_count = item["调仓事件数"]
        result.append(
            {
                "研报产品类型": item["研报产品类型"],
                "调仓事件数": event_count,
                "调仓策略数": len(item["调仓策略"]),
                "对客策略数": len(item["对客策略"]),
                "平均单次换手": round2(item["总换手"] / event_count) if event_count else None,
                "涉及基金数": item["涉及基金数"],
            }
        )
    order = {"纯债型": 0, "固收+型": 1, "股债混合型": 2, "股票型": 3, "多元配置型": 4}
    return sorted(result, key=lambda row: (order.get(row["研报产品类型"], 99), row["研报产品类型"]))


def broad_bucket_sort_key(label: str) -> tuple[int, str]:
    value = raw(label) or "未分档"
    try:
        return BROAD_EQUITY_BUCKET_ORDER.index(value), value
    except ValueError:
        return 999, value


def summarize_broad_bucket_rebalance(
    events: list[dict[str, Any]],
    asset_rows: list[dict[str, Any]],
    strategy_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in events:
        label = raw(row.get("基准风险资产权重") or "未分档")
        item = grouped.setdefault(
            label,
            {
                "基准风险资产权重": label,
                "调仓事件数": 0,
                "调仓策略": set(),
                "对客策略": set(),
                "总换手": 0.0,
                "资产净增配": defaultdict(float),
            },
        )
        item["调仓事件数"] += 1
        item["调仓策略"].add(row["统一策略ID"])
        if row.get("是否对客") == "是":
            item["对客策略"].add(row["统一策略ID"])
        item["总换手"] += num(row.get("单次换手"), 0) or 0

    for row in asset_rows:
        sid = strategy_id(row)
        strategy = strategy_map.get(sid) or {}
        label = raw(strategy.get("基准风险资产权重") or row.get("基准风险资产权重") or "未分档")
        asset = raw(row.get("研报大类资产"))
        change = num(row.get("净增配"))
        if not asset or change is None or label not in grouped:
            continue
        grouped[label]["资产净增配"][asset] += change

    result = []
    for item in grouped.values():
        event_count = int(item["调仓事件数"])
        asset_changes = item["资产净增配"]
        positive = [(name, value) for name, value in asset_changes.items() if value > 0.01]
        negative = [(name, value) for name, value in asset_changes.items() if value < -0.01]
        positive.sort(key=lambda pair: (-pair[1], pair[0]))
        negative.sort(key=lambda pair: (pair[1], pair[0]))
        result.append(
            {
                "基准风险资产权重": item["基准风险资产权重"],
                "调仓事件数": event_count,
                "调仓策略数": len(item["调仓策略"]),
                "对客策略数": len(item["对客策略"]),
                "平均单次换手": round2(item["总换手"] / event_count) if event_count else None,
                "主要净增配": f"{positive[0][0]} {positive[0][1]:+.2f}点" if positive else "无明确净增配",
                "主要净减配": f"{negative[0][0]} {negative[0][1]:+.2f}点" if negative else "无明确净减配",
            }
        )
    return sorted(result, key=lambda row: broad_bucket_sort_key(row["基准风险资产权重"]))


def previous_months(month: str, count: int = 3) -> list[str]:
    start, _, _ = month_bounds(month)
    months = []
    current = start
    for _ in range(count):
        months.append(f"{current.year:04d}-{current.month:02d}")
        current = date(current.year - 1, 12, 1) if current.month == 1 else date(current.year, current.month - 1, 1)
    return list(reversed(months))


def summarize_event_records_for_month(month: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "月份": month,
        "调仓事件数": len(events),
        "调仓策略数": len({row["统一策略ID"] for row in events}),
        "对客策略数": len({row["统一策略ID"] for row in events if row.get("是否对客") == "是"}),
        "平均单次换手": round2(mean([num(row.get("单次换手"), 0) or 0 for row in events])) if events else None,
    }


def summarize_three_months(
    conn: sqlite3.Connection,
    months: list[str],
    strategy_map: dict[str, dict[str, Any]],
    regular_ids: set[str],
) -> list[dict[str, Any]]:
    rows = []
    for month in months:
        events = load_event_records(conn, month, strategy_map, regular_ids)
        rows.append(summarize_event_records_for_month(month, events))
    return rows


def load_index_returns(conn: sqlite3.Connection, month: str) -> list[dict[str, Any]]:
    start, end, prev_end = month_bounds(month)
    codes = {
        "000300.SH": "沪深300",
        "000905.SH": "中证500",
        "000852.SH": "中证1000",
        "930950.CSI": "偏股基金指数",
        "H11001.CSI": "中证全债",
        "H11015.CSI": "中证短债",
        "HSI.HI": "恒生指数",
        "NDX.GI": "纳斯达克100",
        "AU9999.SGE": "上海黄金",
    }
    query_start = (start - timedelta(days=45)).isoformat()
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"""
        SELECT 指数代码, 指数名称, 交易日期, 收盘点位
        FROM 指数日度行情
        WHERE 指数代码 IN ({placeholders})
          AND 交易日期 >= ?
          AND 交易日期 <= ?
        ORDER BY 指数代码, 交易日期
        """,
        (*codes.keys(), query_start, end.isoformat()),
    ).fetchall()
    by_code: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for code, name, dt, close in rows:
        value = num(close)
        if value and value > 0:
            by_code[code].append((dt, value))
    result = []
    for code, label in codes.items():
        values = by_code.get(code) or []
        start_candidates = [(dt, value) for dt, value in values if dt <= prev_end.isoformat()]
        end_candidates = [(dt, value) for dt, value in values if dt <= end.isoformat()]
        if not start_candidates or not end_candidates:
            continue
        start_dt, start_value = start_candidates[-1]
        end_dt, end_value = end_candidates[-1]
        result.append({"指数": label, "代码": code, "月收益": round2((end_value / start_value - 1) * 100), "起点": start_dt, "终点": end_dt})
    return result


def offshore_or_commodity(row: dict[str, Any]) -> bool:
    # This section should describe the fund itself. Strategy-level market region can
    # pull ordinary A-share funds into global strategies, so it is intentionally excluded.
    asset_text = raw(row.get("研报大类资产"))
    name_text = " ".join(raw(row.get(field)) for field in ["基金名称", "基金类型"])
    asset_hit = any(token in asset_text for token in ["海外", "港股", "美股", "商品", "黄金", "原油", "贵金属"])
    name_hit = any(token in name_text for token in ["QDII", "全球", "海外", "港股", "美股", "纳斯达克", "恒生", "标普", "黄金", "商品", "原油", "贵金属"])
    return asset_hit or name_hit


def top_reason_words(events: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    patterns = [
        ("权益机会", ["权益", "股票", "A股", "港股", "估值", "反弹"]),
        ("科技成长", ["科技", "人工智能", "AI", "半导体", "通信", "创新药", "互联网"]),
        ("债券收益", ["债券", "固收", "利率", "久期", "信用", "短债", "收益率"]),
        ("海外配置", ["海外", "全球", "美股", "港股", "QDII", "美元", "亚太"]),
        ("商品黄金", ["黄金", "商品", "原油", "贵金属"]),
        ("风险控制", ["风险", "止盈", "回撤", "波动", "均衡", "再平衡"]),
        ("流动性", ["流动性", "现金", "货币", "存单"]),
    ]
    counts = defaultdict(int)
    for event in events:
        text = raw(event.get("调仓原因"))
        for label, words in patterns:
            if any(word in text for word in words):
                counts[label] += 1
    return [{"关键词": k, "出现次数": v} for k, v in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def use_chart_theme() -> None:
    sns.set_theme(
        style="whitegrid",
        rc={
            "figure.facecolor": TOKENS["surface"],
            "figure.edgecolor": "none",
            "savefig.facecolor": TOKENS["surface"],
            "savefig.edgecolor": "none",
            "axes.facecolor": TOKENS["panel"],
            "axes.edgecolor": TOKENS["axis"],
            "axes.labelcolor": TOKENS["ink"],
            "axes.grid": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": TOKENS["grid"],
            "grid.linewidth": 0.8,
            "font.family": "sans-serif",
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans", "Arial", "sans-serif"],
            "axes.unicode_minus": False,
            "patch.linewidth": 1.0,
        },
    )


def add_chart_header(fig: plt.Figure, ax: plt.Axes, title: str, subtitle: str, *, title_width: int = 78, subtitle_width: int = 112) -> None:
    title = textwrap.fill(str(title).strip(), width=title_width, break_long_words=False)
    subtitle = textwrap.fill(str(subtitle).strip(), width=subtitle_width, break_long_words=False)
    title_lines = title.count("\n") + 1
    subtitle_lines = subtitle.count("\n") + 1
    ax.set_title("")
    fig.subplots_adjust(top=max(0.62, 0.86 - 0.045 * (title_lines - 1) - 0.032 * (subtitle_lines - 1)))
    left = ax.get_position().x0
    fig.text(left, 0.985, title, ha="left", va="top", fontsize=13, fontweight="semibold", color=TOKENS["ink"], linespacing=1.08)
    fig.text(left, 0.93 - 0.045 * (title_lines - 1), subtitle, ha="left", va="top", fontsize=9, color=TOKENS["muted"], linespacing=1.18)
    sns.despine(ax=ax)


def save_ranked_bar(
    rows: list[dict[str, Any]],
    *,
    label: str,
    value: str,
    path: Path,
    title: str,
    subtitle: str,
    signed: bool = False,
    signed_labels: tuple[str, str] = ("净增配", "净减配"),
    top_n: int = 12,
    family_name: str = "blue",
) -> None:
    use_chart_theme()
    data = [row for row in rows if num(row.get(value)) is not None]
    if signed:
        data = sorted(data, key=lambda row: abs(num(row.get(value), 0) or 0), reverse=True)[:top_n]
        data = sorted(data, key=lambda row: num(row.get(value), 0) or 0)
    else:
        data = sorted(data, key=lambda row: num(row.get(value), 0) or 0, reverse=True)[:top_n]
        data = list(reversed(data))
    df = pd.DataFrame(data)
    if df.empty:
        return
    height = max(4.8, 0.44 * len(df) + 2.1)
    fig, ax = plt.subplots(figsize=(10.5, height), dpi=180)
    if signed:
        values = df[value].astype(float)
        positive_family = COLOR_FAMILIES["orange"]
        negative_family = COLOR_FAMILIES["blue"]
        colors = np.where(values >= 0, positive_family["base"], negative_family["light"])
        edge_colors = np.where(values >= 0, positive_family["dark"], negative_family["dark"])
        bars = ax.barh(df[label], values, color=colors, edgecolor=edge_colors, linewidth=1.0)
        ax.axvline(0, color=TOKENS["ink"], linewidth=1.0)
        min_v = min(values.min(), 0)
        max_v = max(values.max(), 0)
        spread = max(max_v - min_v, 1)
        ax.set_xlim(min_v - spread * 0.18, max_v + spread * 0.18)
        for bar, val in zip(bars, values):
            x = val + spread * 0.015 if val >= 0 else val - spread * 0.015
            ax.text(x, bar.get_y() + bar.get_height() / 2, f"{val:+.1f}", ha="left" if val >= 0 else "right", va="center", fontsize=8, color=TOKENS["ink"])
        ax.legend(
            handles=[
                Patch(facecolor=positive_family["base"], edgecolor=positive_family["dark"], label=signed_labels[0]),
                Patch(facecolor=negative_family["light"], edgecolor=negative_family["dark"], label=signed_labels[1]),
            ],
            loc="lower left",
            bbox_to_anchor=(0, 1.02),
            frameon=False,
            ncol=2,
            borderaxespad=0,
        )
    else:
        family = COLOR_FAMILIES[family_name]
        bars = ax.barh(df[label], df[value].astype(float), color=family["base"], edgecolor=family["dark"], linewidth=1.0)
        max_v = max(float(df[value].max()), 1)
        ax.set_xlim(0, max_v * 1.18)
        for bar, val in zip(bars, df[value].astype(float)):
            ax.text(val + max_v * 0.015, bar.get_y() + bar.get_height() / 2, f"{val:.1f}", ha="left", va="center", fontsize=8, color=TOKENS["ink"])
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.tick_params(axis="y", labelsize=8.5)
    ax.tick_params(axis="x", labelsize=8, colors=TOKENS["muted"])
    ax.xaxis.set_major_formatter(mticker.StrMethodFormatter("{x:.1f}" if signed else "{x:.0f}"))
    add_chart_header(fig, ax, title, subtitle)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def save_grouped_bars(
    rows: list[dict[str, Any]],
    *,
    path: Path,
    title: str,
    subtitle: str,
    category: str,
    series_fields: list[tuple[str, str]],
) -> None:
    use_chart_theme()
    plot_rows = []
    for row in rows:
        for field, label in series_fields:
            plot_rows.append({category: row[category], "系列": label, "数值": num(row.get(field), 0) or 0})
    df = pd.DataFrame(plot_rows)
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(10.5, 5.2), dpi=180)
    series_order = [label for _, label in series_fields]
    palette = {
        series_order[0]: COLOR_FAMILIES["orange"]["base"],
        series_order[1]: COLOR_FAMILIES["blue"]["base"] if len(series_order) > 1 else COLOR_FAMILIES["orange"]["base"],
    }
    sns.barplot(data=df, x=category, y="数值", hue="系列", hue_order=series_order, palette=palette, ax=ax, edgecolor=TOKENS["ink"], linewidth=1.0)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), frameon=False, ncol=len(series_order), borderaxespad=0)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.tick_params(axis="x", labelrotation=0, labelsize=8.5)
    ax.tick_params(axis="y", labelsize=8, colors=TOKENS["muted"])
    for container in ax.containers:
        ax.bar_label(container, fmt="%.0f", fontsize=7, padding=2)
    add_chart_header(fig, ax, title, subtitle)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def save_stacked_asset_split(
    rows: list[dict[str, Any]],
    *,
    path: Path,
    title: str,
    subtitle: str,
) -> None:
    use_chart_theme()
    if not rows:
        return
    df = pd.DataFrame(rows)
    top_assets = (
        df.groupby("研报大类资产")["净增配"]
        .apply(lambda s: s.abs().sum())
        .sort_values(ascending=False)
        .head(8)
        .index.tolist()
    )
    df = df[df["研报大类资产"].isin(top_assets)]
    pivot = df.pivot_table(index="研报大类资产", columns="阶段", values="净增配", aggfunc="sum", fill_value=0)
    pivot = pivot.loc[top_assets]
    plot = pivot.reset_index().melt(id_vars="研报大类资产", var_name="阶段", value_name="净增配")
    fig, ax = plt.subplots(figsize=(10.5, 5.6), dpi=180)
    palette = {"上半月": COLOR_FAMILIES["gold"]["base"], "下半月": COLOR_FAMILIES["pink"]["base"]}
    sns.barplot(data=plot, y="研报大类资产", x="净增配", hue="阶段", palette=palette, ax=ax, edgecolor=TOKENS["ink"], linewidth=1.0)
    ax.axvline(0, color=TOKENS["ink"], linewidth=1.0)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), frameon=False, ncol=2, borderaxespad=0)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.tick_params(axis="y", labelsize=8.5)
    ax.tick_params(axis="x", labelsize=8, colors=TOKENS["muted"])
    add_chart_header(fig, ax, title, subtitle)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def save_chart_set(
    output_dir: Path,
    month: str,
    type_perf: list[dict[str, Any]],
    event_type_summary: list[dict[str, Any]],
    broad_bucket_rebalance: list[dict[str, Any]],
    asset_summary: list[dict[str, Any]],
    split_rows: list[dict[str, Any]],
    industry_summary: list[dict[str, Any]],
    qdii_funds: list[dict[str, Any]],
    index_returns: list[dict[str, Any]],
) -> dict[str, str]:
    chart_dir = output_dir / "assets" / f"monthly-rebalance-report-{month.replace('-', '')}"
    chart_dir.mkdir(parents=True, exist_ok=True)
    charts = {
        "type_performance": chart_dir / "type_performance.png",
        "event_type": chart_dir / "event_type.png",
        "broad_bucket_rebalance": chart_dir / "broad_bucket_rebalance.png",
        "asset_flow": chart_dir / "asset_flow.png",
        "asset_split": chart_dir / "asset_split.png",
        "industry_flow": chart_dir / "industry_flow.png",
        "qdii_flow": chart_dir / "qdii_flow.png",
        "index_return": chart_dir / "index_return.png",
    }
    save_ranked_bar(
        type_perf,
        label="研报产品类型",
        value="中位收益",
        path=charts["type_performance"],
        title="各类投顾产品月收益中位数",
        subtitle=f"{month}，标准费后净值，纳入常规排名且数据完整的策略样本；单位：%",
        signed=True,
        signed_labels=("正收益", "负收益"),
        top_n=12,
    )
    save_ranked_bar(
        event_type_summary,
        label="研报产品类型",
        value="调仓事件数",
        path=charts["event_type"],
        title="各类投顾产品调仓事件数",
        subtitle=f"{month}，剔除测试、信号类、停止及持仓缺失策略；单位：次",
        signed=False,
        top_n=12,
        family_name="orange",
    )
    save_ranked_bar(
        broad_bucket_rebalance,
        label="基准风险资产权重",
        value="调仓事件数",
        path=charts["broad_bucket_rebalance"],
        title="基准风险资产同类分档调仓事件数",
        subtitle=f"{month}，同类按权益、商品和另类合计权重划分 L0-L10；单位：次",
        signed=False,
        top_n=12,
        family_name="olive",
    )
    save_ranked_bar(
        asset_summary,
        label="名称",
        value="净增配",
        path=charts["asset_flow"],
        title="资产大类净增配方向",
        subtitle=f"{month} 调仓点位汇总；正值为净加仓，负值为净减仓，单位：百分点",
        signed=True,
        top_n=12,
    )
    save_stacked_asset_split(
        split_rows,
        path=charts["asset_split"],
        title="月初与月末资产切换",
        subtitle=f"{month} 上半月与下半月净增配对比，观察调仓节奏差异；单位：百分点",
    )
    save_ranked_bar(
        industry_summary,
        label="名称",
        value="净增配",
        path=charts["industry_flow"],
        title="A股行业净增配方向",
        subtitle=f"{month}，按基金穿透后的申万/研报行业口径汇总；单位：百分点",
        signed=True,
        top_n=14,
    )
    save_ranked_bar(
        qdii_funds,
        label="基金名称",
        value="净增配",
        path=charts["qdii_flow"],
        title="海外、QDII与商品基金异动",
        subtitle=f"{month}，对客及广发展示样本中的基金净增配；单位：百分点",
        signed=True,
        top_n=12,
    )
    save_ranked_bar(
        index_returns,
        label="指数",
        value="月收益",
        path=charts["index_return"],
        title="市场指数月度表现参考",
        subtitle=f"{month}，用月末收盘点位相对上月末收盘点位计算；单位：%",
        signed=True,
        signed_labels=("正收益", "负收益"),
        top_n=12,
        family_name="gold",
    )
    return {key: "./" + path.relative_to(output_dir).as_posix() for key, path in charts.items() if path.exists()}


def html_table(rows: list[dict[str, Any]], columns: list[tuple[str, str, str]], *, max_rows: int | None = None) -> str:
    selected = rows[:max_rows] if max_rows else rows
    head = "".join(f"<th>{escape(label)}</th>" for _, label, _ in columns)
    body = []
    for row in selected:
        classes = []
        if row.get("是否广发") == "是" or row.get("是否广发基金") == "是":
            classes.append("gf-row")
        cells = []
        for key, _, kind in columns:
            value = row.get(key)
            cls = ""
            if kind == "pct":
                text = pct(value)
                cls = "num " + ("pos" if (num(value, 0) or 0) > 0 else "neg" if (num(value, 0) or 0) < 0 else "")
            elif kind == "pct_plain":
                text = pct(value, signed=False)
                cls = "num"
            elif kind == "int":
                text = int_text(value)
                cls = "num"
            elif kind == "num":
                number = num(value)
                text = "--" if number is None else f"{number:.2f}"
                cls = "num"
            else:
                text = raw(value) or "--"
            cells.append(f'<td class="{cls}">{escape(text)}</td>')
        body.append(f'<tr class="{" ".join(classes)}">{"".join(cells)}</tr>')
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def kpi_card(value: str, label: str, note: str = "") -> str:
    return f"""<div class="kpi">
  <div class="kpi-value">{escape(value)}</div>
  <div class="kpi-label">{escape(label)}</div>
  {f'<div class="kpi-note">{escape(note)}</div>' if note else ''}
</div>"""


def month_short_label(month_text: str) -> str:
    try:
        return f"{int(month_text[-2:])}月"
    except ValueError:
        return month_text


def month_full_label(month_text: str) -> str:
    try:
        year_text, month_number = month_text.split("-", 1)
        return f"{int(year_text)}年{int(month_number)}月"
    except (TypeError, ValueError):
        return month_text


def next_month_text(month_text: str) -> str:
    _, end, _ = month_bounds(month_text)
    next_day = end + timedelta(days=1)
    return f"{next_day.year:04d}-{next_day.month:02d}"


def clamp_width(value: Any, max_value: float = 100.0) -> float:
    number = abs(num(value, 0) or 0)
    if max_value <= 0:
        return 0.0
    return max(0.0, min(100.0, number / max_value * 100.0))


def mini_metric_card(title: str, value_text: str, note: str, width_pct: float, *, tone: str = "blue") -> str:
    width = max(3.0, min(100.0, width_pct))
    return f"""<div class="mini-card tone-{tone}">
  <div class="mini-title">{escape(title)}</div>
  <div class="mini-value">{escape(value_text)}</div>
  <div class="mini-track"><i style="width:{width:.1f}%"></i></div>
  <div class="mini-note">{escape(note)}</div>
</div>"""


def trend_mini_card(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return mini_metric_card("调仓热度", "--", "近三个月样本不足", 0)
    max_events = max((num(row.get("调仓事件数"), 0) or 0) for row in rows) or 1
    bars = []
    for row in rows:
        events = num(row.get("调仓事件数"), 0) or 0
        width = max(4.0, min(100.0, events / max_events * 100.0))
        bars.append(
            f"""<div class="spark-row">
  <span>{escape(month_short_label(raw(row.get("月份"))))}</span>
  <b><i style="width:{width:.1f}%"></i></b>
  <em>{int_text(events)}</em>
</div>"""
        )
    latest = rows[-1]
    return f"""<div class="mini-card tone-orange">
  <div class="mini-title">调仓热度</div>
  <div class="mini-value">{int_text(latest.get("调仓事件数"))}次</div>
  <div class="spark-bars">{"".join(bars)}</div>
  <div class="mini-note">近三个月调仓事件数对比</div>
</div>"""


def compact_label(value: Any, limit: int = 12) -> str:
    text = raw(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def mini_bar_chart(
    title: str,
    rows: list[dict[str, Any]],
    *,
    label: str,
    value: str,
    note: str,
    top_n: int = 5,
    signed: bool = True,
    kind: str = "pct",
) -> str:
    data = [row for row in rows if num(row.get(value)) is not None]
    if signed:
        data = sorted(data, key=lambda row: abs(num(row.get(value), 0) or 0), reverse=True)[:top_n]
    else:
        data = sorted(data, key=lambda row: num(row.get(value), 0) or 0, reverse=True)[:top_n]
    if not data:
        return f"""<div class="mini-chart">
  <div class="mini-chart-title">{escape(title)}</div>
  <div class="mini-chart-empty">暂无足够样本</div>
  <div class="mini-chart-note">{escape(note)}</div>
</div>"""
    max_abs = max(abs(num(row.get(value), 0) or 0) for row in data) or 1
    bar_rows = []
    for row in data:
        number = num(row.get(value), 0) or 0
        width = max(4.0, min(100.0, abs(number) / max_abs * 100.0))
        tone = "pos" if number > 0 else "neg" if number < 0 else "flat"
        value_text = int_text(number) if kind == "int" else pct(number)
        bar_rows.append(
            f"""<div class="mini-bar-row">
  <span title="{escape(raw(row.get(label)))}">{escape(compact_label(row.get(label)))}</span>
  <b class="mini-bar-track {tone}"><i style="width:{width:.1f}%"></i></b>
  <em class="{tone}">{escape(value_text)}</em>
</div>"""
        )
    return f"""<div class="mini-chart">
  <div class="mini-chart-title">{escape(title)}</div>
  <div class="mini-bar-list">{"".join(bar_rows)}</div>
  <div class="mini-chart-note">{escape(note)}</div>
</div>"""


def fold_block(title: str, content: str, note: str = "") -> str:
    note_text = f"{note}，点击展开明细" if note else "点击展开明细"
    note_html = f"<small>{escape(note_text)}</small>"
    return f"""<details class="fold-block">
  <summary><span>{escape(title)}</span>{note_html}</summary>
  {content}
</details>"""


def topbar(active: str = "monthly_rebalance") -> str:
    return render_system_topbar(active)


def insight_text(
    type_perf: list[dict[str, Any]],
    asset_summary: list[dict[str, Any]],
    industry_summary: list[dict[str, Any]],
    qdii_funds: list[dict[str, Any]],
    index_returns: list[dict[str, Any]],
) -> dict[str, str]:
    best_type = max(type_perf, key=lambda row: num(row.get("中位收益"), -999) or -999, default={})
    worst_type = min(type_perf, key=lambda row: num(row.get("中位收益"), 999) or 999, default={})
    top_asset = max(asset_summary, key=lambda row: num(row.get("净增配"), -999) or -999, default={})
    cut_asset = min(asset_summary, key=lambda row: num(row.get("净增配"), 999) or 999, default={})
    add_ind = max(industry_summary, key=lambda row: num(row.get("净增配"), -999) or -999, default={})
    cut_ind = min(industry_summary, key=lambda row: num(row.get("净增配"), 999) or 999, default={})
    qdii_add = max(qdii_funds, key=lambda row: num(row.get("净增配"), -999) or -999, default={})
    qdii_cut = min(qdii_funds, key=lambda row: num(row.get("净增配"), 999) or 999, default={})
    best_index = max(index_returns, key=lambda row: num(row.get("月收益"), -999) or -999, default={})
    return {
        "performance": f"{raw(best_type.get('研报产品类型')) or '收益靠前类别'}收益中位数最高（{pct(best_type.get('中位收益'))}），{raw(worst_type.get('研报产品类型')) or '收益靠后类别'}相对靠后（{pct(worst_type.get('中位收益'))}）。市场参考中{raw(best_index.get('指数')) or '部分指数'}表现较好。",
        "asset": f"资产配置上净增配最明显的是{raw(top_asset.get('名称')) or '未形成集中资产'}（{pct(top_asset.get('净增配'))}），净减配最明显的是{raw(cut_asset.get('名称')) or '未形成集中减配'}（{pct(cut_asset.get('净增配'))}）。",
        "industry": f"行业层面加仓集中在{raw(add_ind.get('名称')) or '未形成集中行业'}（{pct(add_ind.get('净增配'))}），减仓集中在{raw(cut_ind.get('名称')) or '未形成集中行业'}（{pct(cut_ind.get('净增配'))}）。",
        "qdii": f"海外、QDII和商品基金中，{raw(qdii_add.get('基金名称')) or '未形成明显净流入基金'}净增配靠前（{pct(qdii_add.get('净增配'))}），{raw(qdii_cut.get('基金名称')) or '未形成明显净流出基金'}净减配靠前（{pct(qdii_cut.get('净增配'))}）。",
    }


def rebalance_trend_text(three_months: list[dict[str, Any]], month: str) -> dict[str, str]:
    if len(three_months) < 2:
        return {
            "summary": "本月调仓热度需要结合后续月份继续观察。",
            "heading": "2. 调仓行为概览：本月调仓热度待连续观察",
            "body": "本月调仓事件数和调仓策略数用于观察投顾策略是否出现集中换仓，但单月样本不宜直接解释为趋势。",
        }
    previous = three_months[-2]
    current = three_months[-1]
    current_label = month_short_label(month)
    previous_label = month_short_label(raw(previous.get("月份")))
    prev_events = num(previous.get("调仓事件数"), 0) or 0
    current_events = num(current.get("调仓事件数"), 0) or 0
    diff = current_events - prev_events
    if abs(diff) <= max(2, prev_events * 0.03):
        trend = "与上月基本持平"
        heading = f"2. 调仓行为概览：{current_label}调仓热度与{previous_label}基本持平"
        body = f"{current_label}调仓事件数维持在{previous_label}附近，说明调仓热度没有明显扩散；仍需结合产品类型和底层基金方向看结构变化。"
    elif diff > 0:
        trend = f"较上月增加{int(diff)}次"
        heading = f"2. 调仓行为概览：{current_label}调仓热度较上月回升"
        body = f"{current_label}调仓事件数较上月增加，说明投顾策略进入更活跃的配置观察窗口；需要区分是风险预算调整还是底层基金替换。"
    else:
        trend = f"较上月减少{int(abs(diff))}次"
        heading = f"2. 调仓行为概览：{current_label}调仓热度较上月回落"
        body = f"{current_label}调仓事件数较上月减少，说明整体换仓热度有所收敛；更需要关注少数高换手策略和集中行业方向。"
    return {
        "summary": trend,
        "heading": heading,
        "body": body,
    }


def render_html(payload: dict[str, Any]) -> str:
    month = payload["month"]
    month_label = month_full_label(month)
    month_short = month_short_label(month)
    next_month_short = month_short_label(next_month_text(month))
    start = payload["period"]["start"]
    end = payload["period"]["end"]
    charts = payload["charts"]
    kpis = payload["kpis"]
    text = payload["insights"]
    generated = payload["generatedAt"]
    refresh = payload["dataRefresh"]
    source_url = payload["templateSourceUrl"]
    rebalance_trend = payload.get("rebalanceTrend") or {}

    top_strategies = html_table(
        payload["topStrategies"],
        [
            ("策略名称", "策略名称", "text"),
            ("投顾机构", "投顾机构", "text"),
            ("研报产品类型", "产品类型", "text"),
            ("是否对客", "是否对客", "text"),
            ("月收益", f"{month_short}收益", "pct"),
            ("今年以来", "今年以来", "pct"),
            ("最大回撤", "最大回撤", "pct_plain"),
        ],
        max_rows=15,
    )
    type_table = html_table(
        payload["typePerformance"],
        [
            ("研报产品类型", "产品类型", "text"),
            ("样本数", "样本数", "int"),
            ("对客样本数", "对客样本数", "int"),
            ("平均收益", "平均收益", "pct"),
            ("中位收益", "中位收益", "pct"),
            ("正收益占比", "正收益占比", "pct_plain"),
            ("上四分位", "上四分位", "pct"),
            ("下四分位", "下四分位", "pct"),
        ],
    )
    month_table = html_table(
        payload["threeMonths"],
        [
            ("月份", "月份", "text"),
            ("调仓事件数", "调仓事件数", "int"),
            ("调仓策略数", "调仓策略数", "int"),
            ("对客策略数", "对客策略数", "int"),
            ("平均单次换手", "平均单次换手", "pct_plain"),
        ],
    )
    event_type_table = html_table(
        payload["eventTypeSummary"],
        [
            ("研报产品类型", "产品类型", "text"),
            ("调仓事件数", "调仓事件数", "int"),
            ("调仓策略数", "调仓策略数", "int"),
            ("对客策略数", "对客策略数", "int"),
            ("平均单次换手", "平均单次换手", "pct_plain"),
        ],
    )
    broad_bucket_rebalance_table = html_table(
        payload["broadBucketRebalanceSummary"],
        [
            ("基准风险资产权重", "基准风险资产权重", "text"),
            ("调仓事件数", "调仓事件数", "int"),
            ("调仓策略数", "调仓策略数", "int"),
            ("对客策略数", "对客策略数", "int"),
            ("平均单次换手", "平均单次换手", "pct_plain"),
            ("主要净增配", "主要净增配资产", "text"),
            ("主要净减配", "主要净减配资产", "text"),
        ],
    )
    asset_table = html_table(
        payload["assetSummary"],
        [
            ("名称", "资产大类", "text"),
            ("净增配", "净增配", "pct"),
            ("加仓权重", "加仓点位", "pct_plain"),
            ("减仓权重", "减仓点位", "pct_plain"),
            ("策略数", "策略数", "int"),
            ("对客策略数", "对客策略数", "int"),
        ],
        max_rows=16,
    )
    industry_table = html_table(
        payload["industrySummary"],
        [
            ("名称", "行业", "text"),
            ("净增配", "净增配", "pct"),
            ("加仓权重", "加仓点位", "pct_plain"),
            ("减仓权重", "减仓点位", "pct_plain"),
            ("策略数", "策略数", "int"),
        ],
        max_rows=18,
    )
    qdii_table = html_table(
        payload["qdiiFunds"],
        [
            ("基金名称", "基金名称", "text"),
            ("基金公司", "基金公司", "text"),
            ("基金类型", "基金类型", "text"),
            ("研报大类资产", "资产方向", "text"),
            ("净增配", "净增配", "pct"),
            ("调仓策略数", "调仓策略数", "int"),
        ],
        max_rows=16,
    )
    fund_table = html_table(
        payload["topFunds"],
        [
            ("基金名称", "基金名称", "text"),
            ("基金公司", "基金公司", "text"),
            ("研报大类资产", "资产方向", "text"),
            ("研报A股行业", "A股行业", "text"),
            ("净增配", "净增配", "pct"),
            ("调仓策略数", "调仓策略数", "int"),
        ],
        max_rows=20,
    )
    reason_table = html_table(
        payload["reasonSignals"],
        [("关键词", "调仓理由线索", "text"), ("出现次数", "出现次数", "int")],
    )
    index_table = html_table(
        payload["indexReturns"],
        [("指数", "市场参考", "text"), ("月收益", f"{month_short}收益", "pct"), ("终点", "统计至", "text")],
    )
    type_table = fold_block("产品类型收益明细", type_table, "含样本数、收益分位数和正收益占比")
    top_strategies = fold_block("绩优投顾产品列表", top_strategies, "广发相关策略以浅黄色高亮")
    month_table = fold_block("近三个月调仓热度明细", month_table, "调仓事件数、策略数和平均单次换手")
    event_type_table = fold_block("各类型调仓明细", event_type_table, "按研报产品类型汇总")
    broad_bucket_rebalance_table = fold_block(
        "基准风险资产同类分档调仓明细",
        broad_bucket_rebalance_table,
        "按权益、商品和另类合计权重划分 L0-L10",
    )
    asset_table = fold_block("资产大类净增减配明细", asset_table, "正值为净增配，负值为净减配")
    industry_table = fold_block("行业净增减配明细", industry_table, "按基金穿透行业口径汇总")
    qdii_table = fold_block("海外、QDII与商品基金明细", qdii_table, "按底层基金净增减配排序")
    fund_table = fold_block("底层基金净增减配榜", fund_table, "展示本月底层基金主要异动")
    reason_table = (
        fold_block("调仓理由线索明细", reason_table, "按关键词出现次数汇总")
        if payload["reasonSignals"]
        else '<div class="empty">当前样本未披露可归纳的调仓理由文本，暂不展示理由榜。</div>'
    )
    index_table = fold_block("市场参考指数明细", index_table, "用于辅助理解月度市场环境")

    def img(key: str, alt: str) -> str:
        src = charts.get(key)
        return f'<img class="chart-img" src="{escape(src)}" alt="{escape(alt)}">' if src else '<div class="empty">图表数据不足</div>'

    client_ratio = (safe_div(kpis["clientEventStrategyCount"], kpis["eventStrategyCount"]) or 0) * 100
    brief_cards = "\n".join(
        [
            mini_metric_card(f"{month_short}收益中位数", pct(kpis["medianReturn"]), f"{int_text(kpis['performanceSampleCount'])}个可比策略", clamp_width(kpis["medianReturn"], 2.0), tone="blue"),
            mini_metric_card("正收益策略占比", pct(kpis["positiveReturnRatio"], signed=False), "收益样本中月度收益为正", clamp_width(kpis["positiveReturnRatio"]), tone="olive"),
            trend_mini_card(payload["threeMonths"]),
            mini_metric_card("对客调仓策略占比", pct(client_ratio, signed=False), f"{int_text(kpis['clientEventStrategyCount'])}个对客策略有调仓", clamp_width(client_ratio), tone="orange"),
        ]
    )
    brief_visuals = "\n".join(
        [
            mini_bar_chart(
                "收益分布",
                payload["typePerformance"],
                label="研报产品类型",
                value="中位收益",
                note="类型中位收益",
                top_n=5,
                signed=True,
            ),
            mini_bar_chart(
                "调仓分布",
                payload["eventTypeSummary"],
                label="研报产品类型",
                value="调仓事件数",
                note="调仓事件数",
                top_n=5,
                signed=False,
                kind="int",
            ),
            mini_bar_chart(
                "基准风险资产权重调仓",
                payload["broadBucketRebalanceSummary"],
                label="基准风险资产权重",
                value="调仓事件数",
                note="同基准风险资产权重事件数",
                top_n=6,
                signed=False,
                kind="int",
            ),
            mini_bar_chart(
                "资产方向",
                payload["assetSummary"],
                label="名称",
                value="净增配",
                note="净增减配方向",
                top_n=6,
                signed=True,
            ),
            mini_bar_chart(
                "行业方向",
                payload["industrySummary"],
                label="名称",
                value="净增配",
                note="行业净增减配",
                top_n=5,
                signed=True,
            ),
        ]
    )
    best_type = max(payload["typePerformance"], key=lambda row: num(row.get("中位收益"), -999) or -999, default={})
    worst_type = min(payload["typePerformance"], key=lambda row: num(row.get("中位收益"), 999) or 999, default={})
    top_asset = max(payload["assetSummary"], key=lambda row: num(row.get("净增配"), -999) or -999, default={})
    cut_asset = min(payload["assetSummary"], key=lambda row: num(row.get("净增配"), 999) or 999, default={})
    brief_takeaway = (
        f"收益看{raw(best_type.get('研报产品类型')) or '靠前类型'}，"
        f"{raw(worst_type.get('研报产品类型')) or '靠后类型'}相对承压；"
        f"调仓{raw(rebalance_trend.get('summary')) or '待观察'}；"
        f"资产上净增配{raw(top_asset.get('名称')) or '未形成集中'}、净减配{raw(cut_asset.get('名称')) or '未形成集中'}。"
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(month_label)}基金投顾产品调仓分析</title>
  <style>
    :root {{
      --bg:#f6f7f9; --panel:#fff; --ink:#162033; --muted:#667085; --line:#dfe3eb;
      --accent:#2e4780; --accent-soft:#eaf1fe; --orange:#804126; --green:#386411; --blue:#2e4780;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif; }}
    a {{ color:#1d4ed8; text-decoration:none; }}
    .topbar {{ position:sticky; top:0; z-index:20; background:rgba(255,255,255,.96); border-bottom:1px solid var(--line); }}
    .topbar-inner {{ max-width:1440px; margin:0 auto; padding:12px 24px; display:flex; align-items:center; justify-content:space-between; gap:20px; }}
    .brand {{ display:inline-flex; align-items:center; gap:10px; min-width:220px; color:#182230; }}
    .brand-mark {{ width:34px; height:34px; display:inline-grid; place-items:center; background:#166c77; color:#fff; border-radius:6px; font-weight:800; }}
    .brand small {{ display:block; color:var(--muted); font-size:12px; margin-top:1px; }}
    .nav {{ display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }}
    .nav-link {{ padding:7px 10px; border-radius:6px; color:#405063; font-size:14px; border-bottom:0; font-weight:500; }}
    .nav-link:hover,.nav-link.is-active {{ background:#eef3f8; color:#0f4f58; }}
{SIDEBAR_CSS}
    main {{ max-width:1180px; margin:0 auto; padding:34px 26px 60px; }}
    .title-block {{ border-bottom:3px solid #d8dde7; padding:20px 0 22px; }}
    .eyebrow {{ color:#2e4780; font-weight:800; margin-bottom:8px; }}
    h1 {{ font-size:38px; line-height:1.15; margin:0 0 12px; letter-spacing:0; }}
    .meta {{ color:var(--muted); font-weight:700; display:flex; gap:14px; flex-wrap:wrap; }}
    section {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:22px 24px; margin:18px 0; }}
    section h2 {{ margin:0 0 10px; font-size:22px; line-height:1.25; }}
    section h3 {{ margin:20px 0 8px; font-size:17px; }}
    .brief-section {{ display:grid; gap:14px; }}
    .brief-takeaway {{ border-left:4px solid #2e4780; background:#f3f7fd; padding:10px 13px; border-radius:6px; color:#344054; font-weight:800; display:flex; gap:10px; align-items:flex-start; }}
    .brief-takeaway b {{ color:#162033; white-space:nowrap; }}
    .brief-takeaway span {{ min-width:0; }}
    .brief-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:14px 0 12px; }}
    .mini-card {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fbfcfd; min-height:132px; }}
    .mini-title {{ color:#4b5565; font-weight:900; font-size:13px; }}
    .mini-value {{ margin:6px 0 9px; font-size:25px; line-height:1.05; font-weight:950; }}
    .mini-note {{ margin-top:8px; color:var(--muted); font-size:12px; font-weight:700; }}
    .mini-track {{ height:10px; border-radius:999px; background:#eef2f7; overflow:hidden; }}
    .mini-track i {{ display:block; height:100%; border-radius:999px; background:#5477c4; }}
    .tone-olive .mini-track i,.tone-olive .spark-row i {{ background:#71b436; }}
    .tone-orange .mini-track i,.tone-orange .spark-row i {{ background:#cc6f47; }}
    .spark-bars {{ display:grid; gap:6px; margin-top:8px; }}
    .spark-row {{ display:grid; grid-template-columns:34px 1fr 38px; gap:8px; align-items:center; font-size:12px; color:#4b5565; }}
    .spark-row b {{ display:block; height:8px; border-radius:999px; background:#eef2f7; overflow:hidden; }}
    .spark-row i {{ display:block; height:100%; border-radius:999px; }}
    .spark-row em {{ font-style:normal; text-align:right; font-weight:900; color:#344054; }}
    .brief-visuals {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin:0; }}
    .mini-chart {{ border:1px solid var(--line); border-radius:8px; padding:13px 14px; background:#fff; min-height:178px; }}
    .mini-chart-title {{ color:#344054; font-size:13px; font-weight:950; margin-bottom:8px; }}
    .mini-chart-note {{ margin-top:8px; color:var(--muted); font-size:12px; font-weight:700; }}
    .mini-chart-empty {{ color:var(--muted); padding:22px 0; text-align:center; }}
    .mini-bar-list {{ display:grid; gap:7px; }}
    .mini-bar-row {{ display:grid; grid-template-columns:minmax(70px,92px) 1fr 58px; gap:8px; align-items:center; font-size:12px; }}
    .mini-bar-row span {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#475467; font-weight:800; }}
    .mini-bar-row em {{ font-style:normal; text-align:right; font-weight:950; font-variant-numeric:tabular-nums; color:#344054; }}
    .mini-bar-track {{ display:block; height:9px; border-radius:999px; background:#edf1f6; overflow:hidden; }}
    .mini-bar-track i {{ display:block; height:100%; border-radius:999px; background:#8e98a8; }}
    .mini-bar-track.pos i {{ background:#cc6f47; }}
    .mini-bar-track.neg i {{ background:#5477c4; }}
    .mini-bar-row em.pos {{ color:#9a3412; }}
    .mini-bar-row em.neg {{ color:#1d4ed8; }}
    .kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:14px; }}
    .kpi {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fbfcfd; min-height:112px; }}
    .kpi-value {{ font-size:27px; font-weight:900; line-height:1.1; }}
    .kpi-label {{ margin-top:8px; color:#344054; font-weight:800; }}
    .kpi-note {{ margin-top:6px; color:var(--muted); font-size:12px; }}
    .chart-block {{ margin:15px 0 18px; }}
    .chart-img {{ display:block; width:100%; height:auto; border:1px solid #e4e8f0; border-radius:8px; background:#fff; }}
    .fold-block {{ border:1px solid #e4e8f0; border-radius:8px; margin:10px 0 14px; background:#fbfcfd; overflow:hidden; }}
    .fold-block summary {{ cursor:pointer; list-style:none; padding:11px 14px; display:flex; justify-content:space-between; gap:12px; align-items:center; color:#344054; font-weight:900; }}
    .fold-block summary::-webkit-details-marker {{ display:none; }}
    .fold-block summary:before {{ content:"查看"; color:#2e4780; font-size:12px; font-weight:900; border:1px solid #d8dde7; border-radius:999px; padding:1px 8px; background:white; }}
    .fold-block[open] summary:before {{ content:"收起"; }}
    .fold-block summary small {{ color:var(--muted); font-size:12px; font-weight:700; text-align:right; }}
    .fold-block .table-wrap {{ margin:0; border:0; border-top:1px solid #e4e8f0; border-radius:0; background:white; }}
    .table-wrap {{ overflow:auto; border:1px solid #e4e8f0; border-radius:8px; margin:12px 0 4px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th {{ background:#f1f5f9; color:#344054; font-weight:900; padding:9px; text-align:left; white-space:nowrap; }}
    td {{ padding:9px; border-top:1px solid #e7ebf2; vertical-align:top; }}
    tr.gf-row td {{ background:#fff8e1; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .pos {{ color:#9a3412; font-weight:900; }}
    .neg {{ color:#1d4ed8; font-weight:900; }}
    .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .note {{ color:var(--muted); font-weight:700; margin:8px 0 0; }}
    .caption {{ color:#586174; margin:8px 0 0; font-size:13px; font-weight:700; }}
    .pill {{ display:inline-flex; align-items:center; border-radius:999px; padding:2px 9px; background:#eef3f8; color:#405063; font-size:12px; font-weight:800; margin-right:6px; }}
    .empty {{ border:1px dashed var(--line); color:var(--muted); padding:30px; border-radius:8px; text-align:center; }}
    @media (max-width:900px) {{
      main {{ padding:24px 14px 46px; }}
      .topbar-inner {{ align-items:flex-start; flex-direction:column; }}
      h1 {{ font-size:30px; }}
      .kpis,.brief-grid,.brief-visuals,.two-col {{ grid-template-columns:1fr; }}
      .brief-takeaway {{ display:block; }}
      .brief-takeaway b {{ display:block; margin-bottom:4px; }}
      .fold-block summary {{ align-items:flex-start; flex-direction:column; }}
      .fold-block summary small {{ text-align:left; }}
    }}
  </style>
</head>
<body>
{topbar()}
<main>
  <section id="pageLoadingStatus" class="page-loading-status" role="status" aria-live="polite">数据正在加载，请稍等。</section>
  <header class="title-block">
    <div class="eyebrow">基金投顾产品月报系列 · 调仓一览</div>
    <h1>{escape(month_label)}基金投顾产品调仓分析</h1>
    <p class="internal-test-notice">所有数据为测试模拟数据，不构成任何投资意见，仅内部测试使用。</p>
    <div class="meta">
      <span>统计区间：{escape(start)} 至 {escape(end)}</span>
      <span>页面生成：{escape(generated)}</span>
      <span>数据刷新：{escape(refresh)}</span>
      <span>参考框架：<a href="{escape(source_url)}">开源证券基金投顾产品月报</a></span>
    </div>
  </header>

  <section class="brief-section">
    <h2>本期调仓简报</h2>
    <div class="brief-visuals">
      {brief_visuals}
    </div>
    <div class="brief-grid">
      {brief_cards}
    </div>
    <div class="brief-takeaway"><b>本月结论</b><span>{escape(brief_takeaway)}</span></div>
  </section>

  <section>
    <h2>1. 业绩统计：股票和海外风险资产拉开收益差距</h2>
    <p><b>同类中位数比单个策略排名更能反映本月环境。</b>本节按研报产品类型划分纯债、固收+、股债混合、股票和多元配置，收益采用{escape(month_short)}完整月标准费后净值计算。</p>
    <div class="chart-block">{img("type_performance", "各类投顾产品月收益中位数")}</div>
    {type_table}
    <h3>绩优投顾产品</h3>
    <p class="note">下表列出{escape(month_short)}收益靠前策略；广发相关策略以浅黄色高亮。</p>
    {top_strategies}
    <div class="chart-block">{img("index_return", "市场指数月度表现参考")}</div>
    {index_table}
  </section>

  <section>
    <h2>{escape(raw(rebalance_trend.get("heading")) or f"2. 调仓行为概览：{month_short}调仓热度")}</h2>
    <p><b>{escape(raw(rebalance_trend.get("body")) or "本月调仓事件数和调仓策略数用于观察投顾策略是否出现集中换仓。")}</b>调仓事件数、调仓策略数和对客策略数同时观察，可以区分“全市场动作”和“可被客户看到的动作”。</p>
    {month_table}
    <div class="chart-block">{img("event_type", "各类投顾产品调仓事件数")}</div>
    {event_type_table}
    <h3>基准风险资产同类分档调仓</h3>
    <p class="note">同类固定按基准风险资产权重处理：权益、商品和另类风险资产合计权重每10个百分点划分一档。图表比较各档调仓活跃度，明细同时展示涉及策略、平均单次换手及主要资产方向。</p>
    <div class="chart-block">{img("broad_bucket_rebalance", "基准风险资产同类分档调仓事件数")}</div>
    {broad_bucket_rebalance_table}
    <h3>调仓理由线索</h3>
    {reason_table}
  </section>

  <section>
    <h2>2.1 股债配置：本月是资产内部切换，而非统一加仓</h2>
    <p><b>资产大类看，净增配和净减配同时存在。</b>图中正值代表调仓后配置点位净增加，负值代表净减少；该口径适合观察“调仓方向”，不等同于全市场存量仓位。</p>
    <div class="chart-block">{img("asset_flow", "资产大类净增配方向")}</div>
    {asset_table}
    <p><b>月内节奏上，上半月和下半月的配置方向不同。</b>拆分月初和月末后，可以看出部分资产在月末出现再平衡或反向切换。</p>
    <div class="chart-block">{img("asset_split", "月初与月末资产切换")}</div>
  </section>

  <section>
    <h2>2.2 行业与风格配置：科技制造仍是核心观察线索</h2>
    <p><b>行业信号用于看底层基金配置倾向，而不是直接等同于策略观点。</b>权益基金的行业暴露来自基金穿透后的行业口径，混合基金按权益资产占比折算。</p>
    <div class="chart-block">{img("industry_flow", "A股行业净增配方向")}</div>
    {industry_table}
  </section>

  <section>
    <h2>2.3 QDII、商品与基金异动：海外与商品方向需要逐只核验额度和可买性</h2>
    <p><b>海外、QDII和商品基金的调仓信号更容易受申购额度、暂停申购和跨市场交易日影响。</b>本节展示对客及广发展示样本中的净增减配基金，后续落地配置时仍需结合基金限额页核验。</p>
    <div class="chart-block">{img("qdii_flow", "海外、QDII与商品基金异动")}</div>
    {qdii_table}
    <h3>本月底层基金净增减配榜</h3>
    {fund_table}
  </section>

  <section>
    <h2>后续关注</h2>
    <ul>
      <li><b>对客策略优先：</b>优先跟踪本月有调仓、且当前仍对客展示的策略，避免把已结束或非展示组合误读为货架信号。</li>
      <li><b>行业信号看持续性：</b>{escape(month_short)}行业净增配需要结合{escape(next_month_short)}调仓和基金净值表现复核，确认是单次再平衡还是连续配置方向。</li>
      <li><b>QDII产品看可买性：</b>海外和商品基金若进入配置备选，应同步核验申购状态、个人/非个人单日限额和最新公告。</li>
    </ul>
  </section>

  <section>
    <h2>风险提示与口径说明</h2>
    <ul>
      <li>业绩统计为历史收益，不代表未来表现；同类比较只用于观察，不构成投资建议。</li>
      <li>调仓分析基于披露调仓事件和底层基金穿透分类；基金最新季报未披露前，行业和资产暴露可能滞后。</li>
      <li>“对客”按当前展示状态识别；若渠道后续上下架，样本范围会随数据刷新变化。</li>
      <li>参考研报仅用于章节框架和阅读顺序，本报告的数字和结论均来自本地全市场投顾策略库。</li>
    </ul>
  </section>
</main>
<script>
  window.addEventListener("load", function () {{
    var loading = document.getElementById("pageLoadingStatus");
    if (loading) loading.hidden = true;
  }});
</script>
</body>
</html>"""


def build_payload(month: str, site_dir: Path, db_path: Path) -> dict[str, Any]:
    data_dir = data_dir_for(site_dir)
    summary = load_summary(data_dir)
    strategies = summary.get("strategies") or []
    strategy_map = {strategy_id(row): row for row in strategies if strategy_id(row)}
    regular_ids = {sid for sid, row in strategy_map.items() if is_regular_strategy(row)}
    asset_rows_raw, fund_rows_raw = load_rebalance_month(data_dir, month)
    asset_rows = [row for row in asset_rows_raw if strategy_id(row) in regular_ids]
    visible_fund_rows = [row for row in fund_rows_raw if raw(row.get("天天当前对客展示")) == "是" or raw(row.get("是否广发策略")) == "是"]

    conn = sqlite3.connect(str(db_path))
    algorithm = latest_algorithm(conn)
    performance = load_monthly_returns(conn, algorithm, strategy_map, regular_ids, month)
    type_perf = summarize_performance(performance)
    events = load_event_records(conn, month, strategy_map, regular_ids)
    event_source = "原始调仓事件表"
    if not events and asset_rows:
        events = fallback_event_records_from_asset_rows(asset_rows, strategy_map)
        event_source = "调仓月度分片回退"
    event_type_summary = summarize_events(events)
    broad_bucket_rebalance = summarize_broad_bucket_rebalance(events, asset_rows, strategy_map)
    three_months = summarize_three_months(conn, previous_months(month, 3), strategy_map, regular_ids)
    if not any(num(row.get("调仓事件数"), 0) for row in three_months):
        fallback_months = []
        for fallback_month in previous_months(month, 3):
            month_asset_rows, _ = load_rebalance_month_optional(data_dir, fallback_month)
            month_asset_rows = [row for row in month_asset_rows if strategy_id(row) in regular_ids]
            fallback_events = fallback_event_records_from_asset_rows(month_asset_rows, strategy_map)
            fallback_months.append(summarize_event_records_for_month(fallback_month, fallback_events))
        if any(num(row.get("调仓事件数"), 0) for row in fallback_months):
            three_months = fallback_months
    index_returns = load_index_returns(conn, month)
    conn.close()

    asset_dimension_rows = [row for row in asset_rows if raw(row.get("研报大类资产"))]
    asset_summary = group_sum(asset_dimension_rows, "研报大类资产", min_abs=0.5)
    industry_summary = group_sum([row for row in asset_rows if raw(row.get("研报A股行业"))], "研报A股行业", min_abs=0.25)
    if len(industry_summary) < 6:
        industry_summary = group_sum([row for row in asset_rows if raw(row.get("权益行业主题"))], "权益行业主题", min_abs=0.25)

    split_rows = []
    for row in asset_dimension_rows:
        day = int(raw(row.get("调仓日期"))[-2:] or 0)
        split_rows.append(
            {
                "研报大类资产": raw(row.get("研报大类资产")),
                "阶段": "上半月" if day <= 15 else "下半月",
                "净增配": num(row.get("净增配"), 0) or 0,
            }
        )

    top_funds = fund_group_rows(visible_fund_rows)
    qdii_funds = fund_group_rows([row for row in visible_fund_rows if offshore_or_commodity(row)])
    qdii_funds = sorted(qdii_funds, key=lambda row: (-abs(num(row.get("净增配"), 0) or 0), row["基金名称"]))
    top_strategies = sorted(performance, key=lambda row: num(row.get("月收益"), -999) or -999, reverse=True)[:30]
    reason_signals = top_reason_words(events)
    returns = [num(row.get("月收益")) for row in performance if num(row.get("月收益")) is not None]
    kpis = {
        "performanceSampleCount": len(performance),
        "medianReturn": round2(median(returns)) if returns else None,
        "positiveReturnRatio": round2(sum(1 for item in returns if item > 0) / len(returns) * 100) if returns else None,
        "eventCount": len(events),
        "eventStrategyCount": len({row["统一策略ID"] for row in events}),
        "clientEventStrategyCount": len({row["统一策略ID"] for row in events if row.get("是否对客") == "是"}),
        "advisorCount": len({row["投顾机构"] for row in events if row.get("投顾机构")}),
        "assetRowCount": len(asset_rows),
        "fundMonthlyRows": len(visible_fund_rows),
    }
    insights = insight_text(type_perf, asset_summary, industry_summary, qdii_funds, index_returns)
    trend = rebalance_trend_text(three_months, month)
    start, end, _ = month_bounds(month)
    chart_map = save_chart_set(
        site_dir,
        month,
        type_perf,
        event_type_summary,
        broad_bucket_rebalance,
        asset_summary,
        split_rows,
        industry_summary,
        qdii_funds,
        index_returns,
    )
    return {
        "month": month,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "dataRefresh": raw((summary.get("overview") or {}).get("数据刷新时间") or (summary.get("overview") or {}).get("生成时间")),
        "algorithm": algorithm,
        "templateSourceUrl": "https://aigc.idigital.com.cn/djyanbao/%E3%80%90%E5%BC%80%E6%BA%90%E8%AF%81%E5%88%B8%E3%80%91%E5%9F%BA%E9%87%91%E6%8A%95%E9%A1%BE%E4%BA%A7%E5%93%81%E6%9C%88%E6%8A%A5%E7%B3%BB%E5%88%97%EF%BC%8816%EF%BC%89%EF%BC%9A%E5%9F%BA%E9%87%91%E6%8A%95%E9%A1%BE%E4%BA%A7%E5%93%813%E6%9C%88%E8%B0%83%E4%BB%93%E4%B8%80%E8%A7%88-2025-04-07.pdf",
        "kpis": kpis,
        "insights": insights,
        "rebalanceTrend": trend,
        "charts": chart_map,
        "typePerformance": type_perf,
        "topStrategies": top_strategies,
        "events": events[:500],
        "eventTypeSummary": event_type_summary,
        "broadBucketRebalanceSummary": broad_bucket_rebalance,
        "threeMonths": three_months,
        "assetSummary": asset_summary,
        "industrySummary": industry_summary,
        "qdiiFunds": qdii_funds,
        "topFunds": top_funds,
        "reasonSignals": reason_signals,
        "indexReturns": index_returns,
        "sourceCounts": {
            "assetRowsRaw": len(asset_rows_raw),
            "assetRowsRegular": len(asset_rows),
            "fundRowsRaw": len(fund_rows_raw),
            "fundRowsVisible": len(visible_fund_rows),
            "strategyCount": len(strategies),
            "regularStrategyCount": len(regular_ids),
            "eventSource": event_source,
            "eventCount": len(events),
        },
    }


def write_outputs(payload: dict[str, Any], site_dir: Path, month: str) -> tuple[Path, Path]:
    html_path = site_dir / f"monthly-rebalance-report-{month.replace('-', '')}.html"
    html = render_html(payload)
    site_dir.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")
    report_dir = site_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = report_dir / f"monthly-rebalance-report-{month.replace('-', '')}.snapshot.json"
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return html_path, snapshot_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成基金投顾调仓月度分析页面")
    parser.add_argument("--month", default=DEFAULT_MONTH)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(args.month, args.site_dir.resolve(), args.db_path.resolve())
    html_path, snapshot_path = write_outputs(payload, args.site_dir.resolve(), args.month)
    print(
        json.dumps(
            {
                "html": str(html_path),
                "snapshot": str(snapshot_path),
                "month": args.month,
                "kpis": payload["kpis"],
                "charts": payload["charts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
