from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site" / "strategy_center"
ALGORITHM_VERSION = "rebalance_asset_fee_dual_nav_v9_ttfund_rules_cifm_overseas_placeholder_20260527"

SUMMARY_JS = "summary.js"
QUALITY_JS = "quality.js"

STRATEGY_CENTER_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>策略中心 - 全市场投顾分析平台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #102033;
      --muted: #64748b;
      --line: #d9e2ec;
      --accent: #0e7490;
      --accent-soft: #e0f2fe;
      --good: #047857;
      --warn: #b45309;
      --bad: #b91c1c;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      padding: 22px 28px 16px;
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }
    h1 {
      margin: 0 0 8px;
      font-size: 24px;
      line-height: 1.25;
      letter-spacing: 0;
    }
    .meta {
      color: var(--muted);
      font-size: 13px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }
    main {
      padding: 20px 28px 32px;
      max-width: 1680px;
      margin: 0 auto;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(5, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .metric {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 88px;
    }
    .metric .label {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }
    .metric .value {
      font-size: 24px;
      font-weight: 700;
      line-height: 1.2;
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(220px, 2fr) minmax(140px, 1fr) minmax(140px, 1fr) minmax(140px, 1fr);
      gap: 10px;
      margin: 16px 0;
    }
    input, select {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      color: var(--text);
      padding: 0 10px;
      font-size: 14px;
    }
    .table-wrap {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: auto;
      max-height: calc(100vh - 260px);
    }
    table {
      width: 100%;
      min-width: 1180px;
      border-collapse: collapse;
      font-size: 13px;
    }
    thead th {
      position: sticky;
      top: 0;
      z-index: 1;
      background: #eef3f8;
      border-bottom: 1px solid var(--line);
      color: #334155;
      text-align: left;
      padding: 10px 12px;
      white-space: nowrap;
    }
    tbody td {
      border-bottom: 1px solid #edf2f7;
      padding: 10px 12px;
      vertical-align: top;
    }
    tbody tr:hover { background: #f8fbfd; }
    .name {
      font-weight: 650;
      max-width: 280px;
    }
    .sub {
      color: var(--muted);
      margin-top: 4px;
      font-size: 12px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: #075985;
      font-size: 12px;
      white-space: nowrap;
    }
    .pct.good { color: var(--good); }
    .pct.warn { color: var(--warn); }
    .pct.bad { color: var(--bad); }
    .empty {
      padding: 28px;
      color: var(--muted);
      text-align: center;
    }
    @media (max-width: 900px) {
      header, main { padding-left: 14px; padding-right: 14px; }
      .metrics { grid-template-columns: repeat(2, minmax(140px, 1fr)); }
      .toolbar { grid-template-columns: 1fr; }
      .table-wrap { max-height: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>策略中心</h1>
    <div class="meta" id="metaLine">加载中</div>
  </header>
  <main>
    <section class="metrics" id="metrics"></section>
    <section class="toolbar">
      <input id="searchInput" type="search" placeholder="搜索策略、投顾、基准、标签">
      <select id="channelFilter"><option value="">全部渠道</option></select>
      <select id="qualityFilter"><option value="">全部质量</option></select>
      <select id="navSourceFilter"><option value="">全部净值来源</option></select>
    </section>
    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>策略</th>
            <th>渠道</th>
            <th>最新净值日</th>
            <th>累计收益</th>
            <th>年化收益</th>
            <th>最大回撤</th>
            <th>波动率</th>
            <th>夏普</th>
            <th>调仓胜率</th>
            <th>持仓日期</th>
            <th>质量</th>
          </tr>
        </thead>
        <tbody id="strategyRows"></tbody>
      </table>
      <div class="empty" id="emptyState" hidden>没有符合条件的策略</div>
    </section>
  </main>
  <script src="data/summary.js"></script>
  <script src="data/quality.js"></script>
  <script>
    const store = window.__STRATEGY_CENTER_DATA__ || {};
    const summary = store.summary || {};
    const strategies = Array.isArray(summary.strategies) ? summary.strategies : [];
    const $ = (id) => document.getElementById(id);

    function text(value, fallback = "-") {
      return value === null || value === undefined || value === "" ? fallback : String(value);
    }
    function esc(value, fallback = "-") {
      return text(value, fallback).replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[ch]));
    }
    function pct(value) {
      return Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}%` : "-";
    }
    function number(value) {
      return Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN") : "-";
    }
    function pctClass(value, positiveGood = true) {
      const n = Number(value);
      if (!Number.isFinite(n)) return "";
      if (positiveGood) return n >= 0 ? "good" : "bad";
      if (n <= 10) return "good";
      if (n <= 25) return "warn";
      return "bad";
    }
    function optionList(field) {
      return [...new Set(strategies.map((item) => item[field]).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), "zh-CN"));
    }
    function fillOptions(selectId, field) {
      const select = $(selectId);
      for (const value of optionList(field)) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      }
    }
    function renderMetrics() {
      const metrics = [
        ["策略数", number(summary.strategyCount ?? strategies.length)],
        ["最新净值日", text(summary.latestNavDate)],
        ["可回放策略", number(summary.simulationIncludedCount)],
        ["严格完整", number(summary.strictCleanCount)],
        ["需关注", number(summary.invalidCount)],
      ];
      $("metrics").innerHTML = metrics.map(([label, value]) => `<div class="metric"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div></div>`).join("");
      $("metaLine").textContent = `数据生成：${text(summary.generatedAt)}；最新净值日：${text(summary.latestNavDate)}；数据来源：strategy_center/data/summary.js`;
    }
    function row(item) {
      const tags = Array.isArray(item.tags) && item.tags.length ? `<div class="sub">${item.tags.slice(0, 4).map((tag) => esc(tag)).join(" / ")}</div>` : "";
      return `<tr>
        <td class="name">${esc(item.name)}<div class="sub">${esc(item.advisor)} ${tags}</div></td>
        <td><span class="pill">${esc(item.channelName || item.channelId)}</span></td>
        <td>${esc(item.navLatestDate)}</td>
        <td class="pct ${pctClass(item.cumulativeReturnPct)}">${pct(item.cumulativeReturnPct)}</td>
        <td class="pct ${pctClass(item.annualizedReturnPct)}">${pct(item.annualizedReturnPct)}</td>
        <td class="pct ${pctClass(item.maxDrawdownPct, false)}">${pct(item.maxDrawdownPct)}</td>
        <td>${pct(item.volatilityPct)}</td>
        <td>${Number.isFinite(Number(item.sharpe)) ? Number(item.sharpe).toFixed(2) : "-"}</td>
        <td>${pct(item.rebalanceWinRatePct)}</td>
        <td>${esc(item.holdingDate)}</td>
        <td>${esc(item.qualityGrade)}<div class="sub">${esc(item.navSource)}</div></td>
      </tr>`;
    }
    function filterItems() {
      const q = $("searchInput").value.trim().toLowerCase();
      const channel = $("channelFilter").value;
      const quality = $("qualityFilter").value;
      const navSource = $("navSourceFilter").value;
      return strategies.filter((item) => {
        if (channel && item.channelName !== channel) return false;
        if (quality && item.qualityGrade !== quality) return false;
        if (navSource && item.navSource !== navSource) return false;
        if (!q) return true;
        const haystack = [
          item.name,
          item.advisor,
          item.benchmark,
          item.risk,
          item.channelName,
          ...(Array.isArray(item.tags) ? item.tags : []),
        ].filter(Boolean).join(" ").toLowerCase();
        return haystack.includes(q);
      });
    }
    function renderRows() {
      const items = filterItems();
      $("strategyRows").innerHTML = items.slice(0, 500).map(row).join("");
      $("emptyState").hidden = items.length > 0;
    }
    fillOptions("channelFilter", "channelName");
    fillOptions("qualityFilter", "qualityGrade");
    fillOptions("navSourceFilter", "navSource");
    renderMetrics();
    renderRows();
    ["searchInput", "channelFilter", "qualityFilter", "navSourceFilter"].forEach((id) => $(id).addEventListener("input", renderRows));
  </script>
</body>
</html>
"""

CHANNEL_COLORS = {
    "cmfchina": "#dc2626",
    "efundcf": "#16a34a",
    "fullgoal": "#7c3aed",
    "gffunds": "#1f67ff",
    "fund99": "#0891b2",
    "harvestwm": "#b45309",
    "huaxia_tougu": "#8b5cf6",
    "qieman": "#475569",
    "southern": "#059669",
    "ttfund": "#0ea5a4",
    "zocaifu": "#f97316",
}

CHANNEL_TYPE_LABELS = {
    "fund_company": "公募基金公司",
    "third_party": "第三方基金销售平台",
    "wealth_subsidiary": "财富管理子公司",
}

LOGIN_REQUIREMENT_LABELS = {
    "none": "无需登录",
    "partial": "部分公开",
    "required": "需要登录",
}

NAV_SOURCE_LABELS = {
    "simulated": "标准回放净值",
    "official": "官方披露净值",
    "none": "暂无可用净值",
}

QUALITY_GRADE_LABELS = {
    "完整": "完整",
    "完整_已修复": "完整（已修复）",
    "不可回放": "不可回放",
    "未模拟": "未模拟",
}

GOVERNANCE_STATUS_LABELS = {
    "pass": "通过",
    "warn": "提示",
    "fail": "失败",
}

GOVERNANCE_CATEGORY_LABELS = {
    "raw_lineage": "原始留痕",
    "count_reconciliation": "数量核对",
    "schema_required": "必填字段",
    "dedupe": "重复数据",
    "value_reconciliation": "数值口径",
    "fund_identity": "基金识别",
    "weight_check": "权重检查",
    "referential_integrity": "关联完整性",
}

GOVERNANCE_ITEM_LABELS = {
    "raw_snapshots_parse_status": "原始快照解析状态",
    "delta_source_missing_fund_code": "源文件调仓明细基金代码",
}

GOVERNANCE_WARNING_DETAILS = {
    "raw_snapshots_parse_status": "原始详情快照存在部分解析记录；标准化实体与入库数量已通过核对。",
    "delta_source_missing_fund_code": "源文件部分调仓明细缺基金代码；入库后已通过基金别名解析补齐。",
}

DATA_TABLES = [
    ("策略主档", "策略信息"),
    ("官方日度业绩", "策略日度业绩"),
    ("当前基金持仓", "策略当前持仓"),
    ("当前分组持仓", "策略当前持仓分组"),
    ("调仓事件", "策略调仓事件"),
    ("调仓基金明细", "策略调仓明细"),
    ("标准回放净值", "策略模拟净值"),
    ("最新持仓推算稽核", "最新持仓推算稽核策略汇总"),
    ("推算补齐持仓", "策略当前持仓推算补齐"),
]


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params)]


def zh_label(value: Any, mapping: dict[str, str], fallback: str = "未说明") -> str:
    text = norm_text(value)
    if not text:
        return fallback
    return mapping.get(text, text)


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


def round_or_none(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = norm_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def days_between(start: str | None, end: str | None) -> int | None:
    start_date = parse_date(start)
    end_date = parse_date(end)
    if not start_date or not end_date:
        return None
    return (end_date - start_date).days


def annualized_return(start_nav: float | None, end_nav: float | None, day_count: int | None) -> float | None:
    if start_nav in (None, 0) or end_nav is None or not day_count or day_count <= 0:
        return None
    years = day_count / 365.25
    if years <= 0:
        return None
    return (end_nav / start_nav) ** (1.0 / years) - 1.0


def trading_annualized_return(
    start_nav: float | None,
    end_nav: float | None,
    trading_period_count: int | None,
    annual_trading_days: int = 250,
) -> float | None:
    if start_nav in (None, 0) or end_nav is None or not trading_period_count or trading_period_count <= 0:
        return None
    return (end_nav / start_nav) ** (annual_trading_days / trading_period_count) - 1.0


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    month_days = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(value.day, month_days[month - 1]))


def sanitize_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value)


def json_js_assignment(path: Path, lhs: str, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"{lhs} = {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n"
    write_text_if_changed(path, text)


def write_text_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except UnicodeDecodeError:
            pass
    path.write_text(text, encoding="utf-8")


@dataclass
class StrategyNavBundle:
    source: str
    rows: list[dict[str, Any]]


def sql_in_placeholders(values: list[Any]) -> str:
    return ",".join("?" for _ in values)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export strategy center static data.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--algorithm-version", default=ALGORITHM_VERSION)
    return parser.parse_args()


def load_channel_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(conn, 'SELECT * FROM "渠道信息"')
    return {str(row["渠道ID"]): row for row in rows}


def load_strategy_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(conn, 'SELECT * FROM "策略信息"')
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        strategy_id = str(row["统一策略ID"])
        row["tags"] = []
        label_json = norm_text(row.get("标签JSON"))
        if label_json:
            try:
                parsed = json.loads(label_json)
                if isinstance(parsed, list):
                    row["tags"] = [str(item) for item in parsed[:8]]
            except json.JSONDecodeError:
                pass
        result[strategy_id] = row
    return result


def load_simulation_quality(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT *
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ?
        """,
        [ALGORITHM_VERSION],
    )
    return {str(row["统一策略ID"]): row for row in rows}


def load_rebalance_quality(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(conn, 'SELECT * FROM "调仓质量策略汇总"')
    return {str(row["统一策略ID"]): row for row in rows}


def load_current_projection_audit(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    try:
        rows = fetch_dicts(conn, 'SELECT * FROM "最新持仓推算稽核策略汇总"')
    except sqlite3.OperationalError:
        return {}
    return {str(row["统一策略ID"]): row for row in rows}


def load_inferred_current_holdings(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    try:
        rows = fetch_dicts(
            conn,
            'SELECT * FROM "策略当前持仓推算补齐" ORDER BY "统一策略ID", "推算基金权重_百分比" DESC',
        )
    except sqlite3.OperationalError:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["统一策略ID"])].append(row)
    return dict(grouped)


def load_disclosed_risk_map(conn: sqlite3.Connection) -> dict[str, dict[str, dict[str, Any]]]:
    try:
        rows = fetch_dicts(
            conn,
            """
            SELECT *
            FROM "策略披露风险指标"
            ORDER BY "统一策略ID", "区间代码", COALESCE("统计日期", '') DESC
            """,
        )
    except sqlite3.OperationalError:
        return {}

    result: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        strategy_id = str(row["统一策略ID"])
        interval_code = str(row["区间代码"])
        if interval_code not in result[strategy_id]:
            result[strategy_id][interval_code] = row
    return result


def load_latest_holdings(conn: sqlite3.Connection) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    latest_holding_date = {
        row["统一策略ID"]: row["holding_date"]
        for row in fetch_dicts(
            conn,
            """
            SELECT "统一策略ID", MAX("持仓日期") AS holding_date
            FROM "策略当前持仓"
            GROUP BY "统一策略ID"
            """,
        )
    }
    latest_group_date = {
        row["统一策略ID"]: row["holding_date"]
        for row in fetch_dicts(
            conn,
            """
            SELECT "统一策略ID", MAX("持仓日期") AS holding_date
            FROM "策略当前持仓分组"
            GROUP BY "统一策略ID"
            """,
        )
    }

    holding_rows = fetch_dicts(conn, 'SELECT * FROM "策略当前持仓" ORDER BY "统一策略ID", "持仓日期", "基金权重_百分比" DESC')
    grouped_holdings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in holding_rows:
        strategy_id = str(row["统一策略ID"])
        if row["持仓日期"] == latest_holding_date.get(strategy_id):
            grouped_holdings[strategy_id].append(row)

    group_rows = fetch_dicts(conn, 'SELECT * FROM "策略当前持仓分组" ORDER BY "统一策略ID", "持仓日期", "分组权重_百分比" DESC')
    grouped_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in group_rows:
        strategy_id = str(row["统一策略ID"])
        if row["持仓日期"] == latest_group_date.get(strategy_id):
            grouped_groups[strategy_id].append(row)

    return dict(grouped_holdings), dict(grouped_groups)


def load_rebalance_events(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT *
        FROM "策略调仓事件"
        ORDER BY "统一策略ID", "调仓日期" DESC, COALESCE("事件序号", 0) DESC, COALESCE("事件时间", '') DESC
        """,
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["统一策略ID"])].append(row)
    return dict(grouped)


def load_rebalance_details(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT *
        FROM "策略调仓明细"
        ORDER BY "调仓事件ID", COALESCE("权重变化_百分比", 0) DESC, COALESCE("调后权重_百分比", 0) DESC
        """,
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["调仓事件ID"])].append(row)
    return dict(grouped)


def load_event_quality(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(conn, 'SELECT * FROM "调仓质量事件分析"')
    return {str(row["调仓事件ID"]): row for row in rows}


def load_strategy_nav(conn: sqlite3.Connection) -> dict[str, StrategyNavBundle]:
    bundles: dict[str, StrategyNavBundle] = {}

    simulation_rows = fetch_dicts(
        conn,
        """
        SELECT
            "统一策略ID",
            "交易日期",
            "模拟单位净值" AS nav,
            "日收益率_百分比" AS daily_return_pct,
            "累计收益率_百分比" AS cumulative_return_pct,
            "最大回撤_百分比" AS max_drawdown_pct,
            "费前单位净值" AS gross_nav,
            "费前日收益率_百分比" AS gross_daily_return_pct,
            "费前累计收益率_百分比" AS gross_cumulative_return_pct,
            "费前最大回撤_百分比" AS gross_max_drawdown_pct
        FROM "策略模拟净值"
        WHERE "算法版本" = ?
        ORDER BY "统一策略ID", "交易日期"
        """,
        [ALGORITHM_VERSION],
    )
    grouped_sim: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in simulation_rows:
        grouped_sim[str(row["统一策略ID"])].append(
            {
                "date": row["交易日期"],
                "nav": round_or_none(to_float(row["nav"]), 8),
                "dailyReturnPct": round_or_none(to_float(row["daily_return_pct"]), 8),
                "cumulativeReturnPct": round_or_none(to_float(row["cumulative_return_pct"]), 8),
                "drawdownPct": round_or_none(to_float(row["max_drawdown_pct"]), 8),
                "grossNav": round_or_none(to_float(row.get("gross_nav")), 8),
                "grossDailyReturnPct": round_or_none(to_float(row.get("gross_daily_return_pct")), 8),
                "grossCumulativeReturnPct": round_or_none(to_float(row.get("gross_cumulative_return_pct")), 8),
                "grossDrawdownPct": round_or_none(to_float(row.get("gross_max_drawdown_pct")), 8),
            }
        )

    official_rows = fetch_dicts(
        conn,
        """
        SELECT
            "统一策略ID",
            "交易日期",
            "单位净值",
            "日收益率_百分比",
            "累计收益率_百分比",
            "最大回撤_百分比"
        FROM "策略日度业绩"
        ORDER BY "统一策略ID", "交易日期"
        """,
    )
    grouped_official: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in official_rows:
        strategy_id = str(row["统一策略ID"])
        nav = to_float(row["单位净值"])
        cumulative_return_pct = to_float(row["累计收益率_百分比"])
        if nav is None and cumulative_return_pct is not None:
            nav = 1.0 + cumulative_return_pct / 100.0
        if nav is None and cumulative_return_pct is None:
            continue
        grouped_official[strategy_id].append(
            {
                "date": row["交易日期"],
                "nav": round_or_none(nav, 8),
                "dailyReturnPct": round_or_none(to_float(row["日收益率_百分比"]), 8),
                "cumulativeReturnPct": round_or_none(cumulative_return_pct, 8),
                "drawdownPct": round_or_none(to_float(row["最大回撤_百分比"]), 8),
            }
        )

    for strategy_id, rows in grouped_sim.items():
        bundles[strategy_id] = StrategyNavBundle(source="simulated", rows=rows)
    for strategy_id, rows in grouped_official.items():
        bundles.setdefault(strategy_id, StrategyNavBundle(source="official", rows=rows))

    return bundles


def ensure_drawdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peak: float | None = None
    start_nav: float | None = None
    running_max_drawdown = 0.0
    gross_peak: float | None = None
    gross_start_nav: float | None = None
    gross_running_max_drawdown = 0.0
    output: list[dict[str, Any]] = []
    for row in rows:
        nav = to_float(row.get("nav"))
        cumulative_return_pct = to_float(row.get("cumulativeReturnPct"))
        if nav is None and cumulative_return_pct is not None:
            nav = 1.0 + cumulative_return_pct / 100.0
        if nav is None:
            continue
        if start_nav is None:
            start_nav = nav
        peak = nav if peak is None else max(peak, nav)
        if cumulative_return_pct is None and start_nav not in (None, 0):
            cumulative_return_pct = (nav / start_nav - 1.0) * 100.0
        current_drawdown_pct = (1.0 - nav / peak) * 100.0 if peak not in (None, 0) else 0.0
        running_max_drawdown = max(running_max_drawdown, current_drawdown_pct)
        drawdown_pct = running_max_drawdown

        normalized = {
            "date": row["date"],
            "nav": round_or_none(nav, 8),
            "dailyReturnPct": round_or_none(to_float(row.get("dailyReturnPct")), 8),
            "cumulativeReturnPct": round_or_none(cumulative_return_pct, 8),
            "currentDrawdownPct": round_or_none(current_drawdown_pct, 8),
            "drawdownPct": round_or_none(drawdown_pct, 8),
        }

        gross_nav = to_float(row.get("grossNav"))
        gross_cumulative_return_pct = to_float(row.get("grossCumulativeReturnPct"))
        if gross_nav is None and gross_cumulative_return_pct is not None:
            gross_nav = 1.0 + gross_cumulative_return_pct / 100.0
        has_gross = any(
            row.get(key) is not None
            for key in ("grossNav", "grossDailyReturnPct", "grossCumulativeReturnPct", "grossDrawdownPct")
        )
        if has_gross and gross_nav is not None:
            if gross_start_nav is None:
                gross_start_nav = gross_nav
            gross_peak = gross_nav if gross_peak is None else max(gross_peak, gross_nav)
            if gross_cumulative_return_pct is None and gross_start_nav not in (None, 0):
                gross_cumulative_return_pct = (gross_nav / gross_start_nav - 1.0) * 100.0
            gross_current_drawdown_pct = (1.0 - gross_nav / gross_peak) * 100.0 if gross_peak not in (None, 0) else 0.0
            gross_running_max_drawdown = max(gross_running_max_drawdown, gross_current_drawdown_pct)
            gross_drawdown_pct = gross_running_max_drawdown
            normalized.update(
                {
                    "grossNav": round_or_none(gross_nav, 8),
                    "grossDailyReturnPct": round_or_none(to_float(row.get("grossDailyReturnPct")), 8),
                    "grossCumulativeReturnPct": round_or_none(gross_cumulative_return_pct, 8),
                    "grossCurrentDrawdownPct": round_or_none(gross_current_drawdown_pct, 8),
                    "grossDrawdownPct": round_or_none(gross_drawdown_pct, 8),
                }
            )
        output.append(normalized)
    return output


def nearest_row_on_or_after(rows: list[dict[str, Any]], target: date) -> dict[str, Any] | None:
    for row in rows:
        row_date = parse_date(row["date"])
        if row_date and row_date >= target:
            return row
    return None


def nearest_row_on_or_before(rows: list[dict[str, Any]], target: date) -> dict[str, Any] | None:
    result = None
    for row in rows:
        row_date = parse_date(row["date"])
        if row_date and row_date <= target:
            result = row
        elif row_date and row_date > target:
            break
    return result


def compute_interval_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) < 2:
        return []
    start_date = parse_date(rows[0]["date"])
    end_date = parse_date(rows[-1]["date"])
    if not start_date or not end_date:
        return []

    windows = [
        ("1w", "近1周", end_date - timedelta(days=7)),
        ("1m", "近1月", add_months(end_date, -1)),
        ("3m", "近3月", add_months(end_date, -3)),
        ("6m", "近6月", add_months(end_date, -6)),
        ("1y", "近1年", add_months(end_date, -12)),
        ("ytd", "今年以来", date(end_date.year - 1, 12, 31)),
        ("std", "成立以来", start_date),
    ]
    result: list[dict[str, Any]] = []
    end_nav = to_float(rows[-1]["nav"])
    end_gross_nav = to_float(rows[-1].get("grossNav"))
    if end_nav in (None, 0):
        return result

    for code, label, target_start in windows:
        start_row = rows[0] if code == "std" else nearest_row_on_or_before(rows, target_start)
        if not start_row:
            result.append({"code": code, "label": label, "returnPct": None, "maxDrawdownPct": None, "currentDrawdownPct": None})
            continue
        start_nav = to_float(start_row["nav"])
        if start_nav in (None, 0):
            result.append({"code": code, "label": label, "returnPct": None, "maxDrawdownPct": None, "currentDrawdownPct": None})
            continue
        subset = [row for row in rows if start_row["date"] <= row["date"] <= rows[-1]["date"]]
        peak: float | None = None
        gross_peak: float | None = None
        max_drawdown = 0.0
        gross_max_drawdown = 0.0
        current_drawdown = 0.0
        gross_current_drawdown = 0.0
        for row in subset:
            nav = to_float(row["nav"])
            if nav is None:
                continue
            peak = nav if peak is None else max(peak, nav)
            if peak:
                current_drawdown = (1.0 - nav / peak) * 100.0
                max_drawdown = max(max_drawdown, current_drawdown)
            gross_nav = to_float(row.get("grossNav"))
            if gross_nav is not None:
                gross_peak = gross_nav if gross_peak is None else max(gross_peak, gross_nav)
                if gross_peak:
                    gross_current_drawdown = (1.0 - gross_nav / gross_peak) * 100.0
                    gross_max_drawdown = max(gross_max_drawdown, gross_current_drawdown)
        item = {
            "code": code,
            "label": label,
            "returnPct": round_or_none((end_nav / start_nav - 1.0) * 100.0, 6),
            "maxDrawdownPct": round_or_none(max_drawdown, 6),
            "currentDrawdownPct": round_or_none(current_drawdown, 6),
        }
        start_gross_nav = to_float(start_row.get("grossNav"))
        if end_gross_nav not in (None, 0) and start_gross_nav not in (None, 0):
            item["grossReturnPct"] = round_or_none((end_gross_nav / start_gross_nav - 1.0) * 100.0, 6)
            item["grossMaxDrawdownPct"] = round_or_none(gross_max_drawdown, 6)
            item["grossCurrentDrawdownPct"] = round_or_none(gross_current_drawdown, 6)
        result.append(item)
    return result


def snapshot_only_intervals(total_return_pct: float | None, total_drawdown_pct: float | None) -> list[dict[str, Any]]:
    result = [
        {"code": "1w", "label": "近1周", "returnPct": None, "maxDrawdownPct": None, "currentDrawdownPct": None},
        {"code": "1m", "label": "近1月", "returnPct": None, "maxDrawdownPct": None, "currentDrawdownPct": None},
        {"code": "3m", "label": "近3月", "returnPct": None, "maxDrawdownPct": None, "currentDrawdownPct": None},
        {"code": "6m", "label": "近6月", "returnPct": None, "maxDrawdownPct": None, "currentDrawdownPct": None},
        {"code": "1y", "label": "近1年", "returnPct": None, "maxDrawdownPct": None, "currentDrawdownPct": None},
        {"code": "ytd", "label": "今年以来", "returnPct": None, "maxDrawdownPct": None, "currentDrawdownPct": None},
    ]
    result.append(
        {
            "code": "std",
            "label": "成立以来",
            "returnPct": round_or_none(total_return_pct, 6),
            "maxDrawdownPct": round_or_none(total_drawdown_pct, 6),
            "currentDrawdownPct": None,
        }
    )
    return result


def summarize_group_mix(holding_rows: list[dict[str, Any]], group_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    if group_rows:
        groups = [
            {
                "name": row["分组名称"] or "未分组",
                "weightPct": round_or_none(to_float(row["分组权重_百分比"]), 4),
                "fundCount": int(row["基金数量"] or 0),
            }
            for row in group_rows
        ]
    else:
        sums: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for row in holding_rows:
            name = row.get("分组名称") or row.get("资产类型") or "未分组"
            weight = to_float(row.get("基金权重_百分比")) or 0.0
            sums[name] += weight
            counts[name] += 1
        groups = [
            {"name": name, "weightPct": round_or_none(weight, 4), "fundCount": counts[name]}
            for name, weight in sums.items()
        ]
        groups.sort(key=lambda item: item["weightPct"] or 0.0, reverse=True)

    summary = " / ".join(
        f'{item["name"]} {item["weightPct"]:.1f}%'
        for item in groups[:3]
        if item.get("weightPct") is not None
    )
    return groups, summary or None


def build_summary_item(
    strategy: dict[str, Any],
    channel_map: dict[str, dict[str, Any]],
    simulation_quality: dict[str, Any] | None,
    rebalance_quality: dict[str, Any] | None,
    current_projection_audit: dict[str, Any] | None,
    nav_bundle: StrategyNavBundle | None,
    disclosed_risk: dict[str, dict[str, Any]],
    holdings: list[dict[str, Any]],
    holding_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    strategy_id = str(strategy["统一策略ID"])
    channel_id = str(strategy["渠道ID"])
    channel = channel_map.get(channel_id, {})
    safe_name = sanitize_filename(strategy_id)

    nav_rows = ensure_drawdown(nav_bundle.rows) if nav_bundle else []
    intervals = compute_interval_metrics(nav_rows)
    latest = nav_rows[-1] if nav_rows else {}
    start = nav_rows[0] if nav_rows else {}
    holding_mix, holding_mix_text = summarize_group_mix(holdings, holding_groups)

    latest_date = latest.get("date")
    nav_day_count = days_between(start.get("date"), latest_date)
    derived_annualized = annualized_return(to_float(start.get("nav")), to_float(latest.get("nav")), nav_day_count)
    gross_derived_annualized = annualized_return(
        to_float(start.get("grossNav")),
        to_float(latest.get("grossNav")),
        nav_day_count,
    )
    official_trading_annualized = (
        trading_annualized_return(to_float(start.get("nav")), to_float(latest.get("nav")), max(len(nav_rows) - 1, 0))
        if nav_bundle and nav_bundle.source == "official"
        else None
    )

    quality_grade = simulation_quality.get("质量等级") if simulation_quality else "未模拟"
    included = int(simulation_quality.get("是否纳入模拟") or 0) if simulation_quality else 0
    repaired = quality_grade == "完整_已修复"
    latest_cumulative = (
        to_float(simulation_quality.get("模拟累计收益率_百分比")) if simulation_quality and included else to_float(latest.get("cumulativeReturnPct"))
    )
    official_snapshot_only = bool(
        nav_bundle
        and nav_bundle.source == "official"
        and latest_cumulative is not None
        and (len(nav_rows) <= 2 or (nav_day_count is not None and nav_day_count <= 2))
    )
    annualized_pct = (
        to_float(simulation_quality.get("模拟年化收益率_百分比"))
        if simulation_quality and included
        else (
            official_trading_annualized * 100.0
            if official_trading_annualized is not None
            else (derived_annualized * 100.0 if derived_annualized is not None else None)
        )
    )
    drawdown_pct = to_float(latest.get("drawdownPct"))
    current_drawdown_pct = to_float(latest.get("currentDrawdownPct"))
    gross_drawdown_pct = to_float(latest.get("grossDrawdownPct"))
    gross_current_drawdown_pct = to_float(latest.get("grossCurrentDrawdownPct"))
    disclosed_total_risk = disclosed_risk.get("std") or disclosed_risk.get("official_card") or {}
    disclosed_1y_risk = disclosed_risk.get("1y") or {}
    disclosed_card_risk = disclosed_risk.get("official_card") or {}
    if official_snapshot_only:
        inception_day_count = days_between(strategy.get("成立日期"), latest_date)
        snapshot_annualized = annualized_return(1.0, 1.0 + latest_cumulative / 100.0, inception_day_count)
        annualized_pct = snapshot_annualized * 100.0 if snapshot_annualized is not None else None
        drawdown_pct = None
        current_drawdown_pct = None
        gross_drawdown_pct = None
        gross_current_drawdown_pct = None
        intervals = snapshot_only_intervals(latest_cumulative, None)
    sparkline = [round_or_none(to_float(row.get("cumulativeReturnPct")), 4) for row in nav_rows[-90:] if row.get("cumulativeReturnPct") is not None]
    if official_snapshot_only and latest_cumulative is not None:
        sparkline = [round_or_none(latest_cumulative, 4)]

    return {
        "id": strategy_id,
        "detailFile": f"details/{safe_name}.js",
        "channelId": channel_id,
        "channelName": channel.get("渠道名称") or strategy.get("投顾机构") or channel_id,
        "channelColor": CHANNEL_COLORS.get(channel_id, "#1f67ff"),
        "name": strategy.get("策略名称"),
        "advisor": strategy.get("投顾机构"),
        "type": strategy.get("策略类型"),
        "risk": strategy.get("风险等级"),
        "benchmark": strategy.get("业绩基准"),
        "inceptionDate": strategy.get("成立日期"),
        "tags": strategy.get("tags") or [],
        "status": strategy.get("策略状态"),
        "description": strategy.get("策略描述"),
        "navSource": nav_bundle.source if nav_bundle else "none",
        "navLatestDate": latest_date,
        "navLatestValue": round_or_none(to_float(latest.get("nav")), 8),
        "grossNavLatestValue": round_or_none(to_float(latest.get("grossNav")), 8),
        "navLatestDailyReturnPct": round_or_none(to_float(latest.get("dailyReturnPct")), 6),
        "grossNavLatestDailyReturnPct": round_or_none(to_float(latest.get("grossDailyReturnPct")), 6),
        "cumulativeReturnPct": round_or_none(latest_cumulative, 6),
        "grossCumulativeReturnPct": round_or_none(
            to_float(simulation_quality.get("模拟费前累计收益率_百分比")) if simulation_quality and included else to_float(latest.get("grossCumulativeReturnPct")),
            6,
        ),
        "annualizedReturnPct": round_or_none(annualized_pct, 6),
        "grossAnnualizedReturnPct": round_or_none(gross_derived_annualized * 100.0 if gross_derived_annualized is not None else None, 6),
        "maxDrawdownPct": round_or_none(drawdown_pct, 6),
        "currentDrawdownPct": round_or_none(current_drawdown_pct, 6),
        "grossDrawdownPct": round_or_none(gross_drawdown_pct, 6),
        "grossCurrentDrawdownPct": round_or_none(gross_current_drawdown_pct, 6),
        "officialMaxDrawdownPct": round_or_none(to_float(disclosed_total_risk.get("官方最大回撤_百分比")), 6),
        "officialOneYearMaxDrawdownPct": round_or_none(to_float(disclosed_1y_risk.get("官方最大回撤_百分比")), 6),
        "officialCardDrawdownPct": round_or_none(to_float(disclosed_card_risk.get("官方最大回撤_百分比")), 6),
        "officialRiskDate": disclosed_total_risk.get("统计日期") or disclosed_1y_risk.get("统计日期") or disclosed_card_risk.get("统计日期"),
        "officialRiskSource": disclosed_total_risk.get("数据来源字段") or disclosed_1y_risk.get("数据来源字段") or disclosed_card_risk.get("数据来源字段"),
        "volatilityPct": round_or_none(to_float(simulation_quality.get("模拟波动率_年化_百分比")) if simulation_quality else None, 6),
        "sharpe": round_or_none(to_float(simulation_quality.get("模拟夏普_年化无风险0")) if simulation_quality else None, 6),
        "simulationIncluded": included,
        "qualityGrade": quality_grade,
        "qualityIssue": simulation_quality.get("首个问题类型") if simulation_quality else None,
        "qualityRepairNote": simulation_quality.get("修复说明") if simulation_quality else None,
        "officialDiffPct": round_or_none(
            to_float(simulation_quality.get("App展示官方收益差_百分点"))
            if simulation_quality and simulation_quality.get("App展示官方收益差_百分点") is not None
            else (to_float(simulation_quality.get("模拟费前官方收益差_百分点")) if simulation_quality else None),
            6,
        ),
        "netOfficialDiffPct": round_or_none(to_float(simulation_quality.get("模拟官方收益差_百分点")) if simulation_quality else None, 6),
        "grossOfficialDiffPct": round_or_none(to_float(simulation_quality.get("模拟费前官方收益差_百分点")) if simulation_quality else None, 6),
        "officialDisplayBasis": simulation_quality.get("App展示对比口径") if simulation_quality else None,
        "officialCompareRule": simulation_quality.get("官方对比区间规则") if simulation_quality else None,
        "officialAdjustedEndDate": simulation_quality.get("官方对比结束日期_调整后") if simulation_quality else None,
        "officialClosestBasis": simulation_quality.get("官方更接近口径") if simulation_quality else None,
        "rebalanceEventCount": int(rebalance_quality.get("历史调仓事件数") or 0) if rebalance_quality else 0,
        "rebalanceValidCount": int(rebalance_quality.get("有效调仓事件数") or 0) if rebalance_quality else 0,
        "rebalanceWinRatePct": round_or_none(to_float(rebalance_quality.get("胜率_有效事件_百分比")) if rebalance_quality else None, 4),
        "rebalanceAverageExcessPct": round_or_none(to_float(rebalance_quality.get("平均调仓超额_百分比")) if rebalance_quality else None, 6),
        "rebalanceHistoryRating": rebalance_quality.get("历史评价") if rebalance_quality else None,
        "currentProjectionAuditStatus": current_projection_audit.get("稽核状态") if current_projection_audit else None,
        "currentProjectionReasonCategory": current_projection_audit.get("归因分类") if current_projection_audit else None,
        "currentProjectionConclusion": current_projection_audit.get("稽核结论") if current_projection_audit else None,
        "currentProjectionMaxDiffPct": round_or_none(to_float(current_projection_audit.get("最大绝对差_百分点")) if current_projection_audit else None, 6),
        "currentProjectionTotalDiffPct": round_or_none(to_float(current_projection_audit.get("绝对差合计_百分点")) if current_projection_audit else None, 6),
        "currentProjectionDate": current_projection_audit.get("推算持仓日期") if current_projection_audit else None,
        "currentProjectionCanInfer": int(current_projection_audit.get("是否可推算补齐") or 0) if current_projection_audit else 0,
        "holdingDate": holdings[0]["持仓日期"] if holdings else (holding_groups[0]["持仓日期"] if holding_groups else None),
        "holdingExact": int(any(int(row.get("是否精确权重") or 0) == 1 for row in holdings)) if holdings else 0,
        "holdingFundCount": len(holdings),
        "holdingMix": holding_mix,
        "holdingMixText": holding_mix_text,
        "sparkline": sparkline,
        "intervals": intervals,
        "runningDays": days_between(strategy.get("成立日期"), latest_date),
        "repaired": repaired,
    }


def build_holdings_payload(holdings: list[dict[str, Any]], group_rows: list[dict[str, Any]]) -> dict[str, Any]:
    holding_mix, _ = summarize_group_mix(holdings, group_rows)
    funds = [
        {
            "fundCode": row.get("基金代码"),
            "fundName": row.get("基金名称"),
            "assetType": row.get("资产类型"),
            "groupName": row.get("分组名称"),
            "weightPct": round_or_none(to_float(row.get("基金权重_百分比")), 4),
            "nav": round_or_none(to_float(row.get("基金净值")), 6),
            "navDate": row.get("基金净值日期"),
            "dailyReturnPct": round_or_none(to_float(row.get("最新日涨幅_百分比")), 4),
            "exact": int(row.get("是否精确权重") or 0),
        }
        for row in holdings
    ]
    return {
        "holdingDate": holdings[0]["持仓日期"] if holdings else (group_rows[0]["持仓日期"] if group_rows else None),
        "disclosureDate": holdings[0]["披露日期"] if holdings else (group_rows[0]["披露日期"] if group_rows else None),
        "exactHolding": int(any(item["exact"] for item in funds)) if funds else 0,
        "groupMix": holding_mix,
        "funds": funds,
    }


def build_deviation_payload(summary_item: dict[str, Any], simulation_quality: dict[str, Any] | None) -> dict[str, Any] | None:
    diff_pct = to_float(summary_item.get("officialDiffPct"))
    if diff_pct is None:
        return None

    official_count = int(simulation_quality.get("官方可比记录数") or 0) if simulation_quality else 0
    official_start = simulation_quality.get("官方起始日期") if simulation_quality else None
    official_end = simulation_quality.get("官方结束日期") if simulation_quality else None
    channel_id = summary_item.get("channelId")
    abs_diff = abs(diff_pct)
    direction = "模拟高于官方" if diff_pct > 0 else "模拟低于官方" if diff_pct < 0 else "模拟与官方基本一致"
    if abs_diff >= 3:
        level = "高"
    elif abs_diff >= 2:
        level = "中高"
    elif abs_diff >= 1:
        level = "中"
    else:
        level = "低"

    reasons: list[str] = []
    optimizations: list[str] = []

    if official_count <= 2:
        reasons.append(f"官方可比点只有 {official_count} 个，当前偏差主要反映区间首尾快照差，不能证明日度走势完全对齐。")
        optimizations.append("优先补齐官方日度业绩时序，不再只用登录后快照首尾点做对比。")
    elif official_count < 30:
        reasons.append(f"官方可比点仅 {official_count} 个，样本偏稀，当前差值更适合作为区间快照校验。")
        optimizations.append("补长官方时间序列后，再做日度级误差归因。")
    else:
        reasons.append(f"官方可比点 {official_count} 个，当前偏差更像估值/执行时点口径差，而不是简单缺样本。")

    if channel_id == "ttfund":
        reasons.append("ttfund 当前官方对齐口径仍以登录态快照拼接为主，官方历史序列验证能力偏弱。")
        optimizations.append("补采 ttfund 官方历史业绩曲线或详情接口，作为更稳定的官方对齐基准。")

    if channel_id == "gffunds" and official_count >= 180:
        reasons.append("gffunds 渠道当前整体偏差同向为正，模拟普遍高于官方，更像调仓生效时点、现金残留、费率或成交延迟未建模。")
        optimizations.append("增加 gffunds 专用回放版本：按 T+1 生效、保留现金残留、叠加申赎费/换仓摩擦。")

    if channel_id == "zocaifu" and official_count >= 180 and abs_diff >= 2:
        reasons.append("zocaifu 官方日序列相对完整，若仍出现大偏差，通常是个别调仓事件或基金映射需要逐段复核。")
        optimizations.append("对异常策略逐段比对调前/调后权重、基金映射和区间收益。")

    if summary_item.get("qualityGrade") == "完整_已修复":
        repair_note = summary_item.get("qualityRepairNote") or "重复明细折叠 / 权重归一化"
        reasons.append(f"该策略源数据做过轻微修复：{repair_note}。")

    if official_count >= 30 and abs_diff >= 1:
        optimizations.append("增加按披露日生效、按下一交易日生效、按调仓确认日生效三套回放口径对比。")
        optimizations.append("在组合层引入现金拖尾、基金非披露日持有不动和交易费率拖累。")

    reasons = unique_keep_order(reasons)
    optimizations = unique_keep_order(optimizations)
    summary_text = (
        "官方样本过稀，当前差值更偏向快照校验。"
        if official_count <= 2
        else "官方样本充足，当前差值主要反映产品口径差。"
        if official_count >= 180
        else "当前差值由样本稀疏和产品口径共同驱动。"
    )

    return {
        "level": level,
        "direction": direction,
        "diffPct": round_or_none(diff_pct, 6),
        "absDiffPct": round_or_none(abs_diff, 6),
        "officialComparableCount": official_count,
        "officialStartDate": official_start,
        "officialEndDate": official_end,
        "summary": summary_text,
        "reasons": reasons,
        "optimizations": optimizations,
    }


def load_strategy_event_returns(
    conn: sqlite3.Connection,
    events: list[dict[str, Any]],
    detail_map: dict[str, list[dict[str, Any]]],
    event_quality: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, float]], list[str]]:
    fund_codes: set[str] = set()
    start_dates: list[str] = []
    end_dates: list[str] = []

    for event in events:
        event_id = str(event["调仓事件ID"])
        quality = event_quality.get(event_id, {})
        start_date = norm_text(event.get("调仓日期"))
        end_date = norm_text(quality.get("区间结束锚点日期") or quality.get("下次调仓日期"))
        if not start_date or not end_date or start_date >= end_date:
            continue
        start_dates.append(start_date)
        end_dates.append(end_date)
        for row in detail_map.get(event_id, []):
            fund_code = norm_text(row.get("基金代码"))
            if fund_code:
                fund_codes.add(fund_code)

    if not fund_codes or not start_dates or not end_dates:
        return {}, []

    codes = sorted(fund_codes)
    rows = fetch_dicts(
        conn,
        f"""
        SELECT "基金代码", "交易日期", "日收益率_百分比"
        FROM "基金日度净值"
        WHERE "基金代码" IN ({sql_in_placeholders(codes)})
          AND "交易日期" >= ?
          AND "交易日期" <= ?
        ORDER BY "交易日期", "基金代码"
        """,
        [*codes, min(start_dates), max(end_dates)],
    )

    returns_by_date: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        fund_code = norm_text(row.get("基金代码"))
        trade_date = norm_text(row.get("交易日期"))
        daily_return_pct = to_float(row.get("日收益率_百分比"))
        if not fund_code or not trade_date or daily_return_pct is None:
            continue
        returns_by_date[trade_date][fund_code] = daily_return_pct / 100.0

    available_dates = sorted(returns_by_date)
    return dict(returns_by_date), available_dates


def normalize_weight_map(detail_rows: list[dict[str, Any]], field_name: str) -> tuple[dict[str, float], float]:
    weights: dict[str, float] = defaultdict(float)
    for row in detail_rows:
        fund_code = norm_text(row.get("基金代码"))
        weight_pct = to_float(row.get(field_name))
        if not fund_code or weight_pct is None or weight_pct <= 0:
            continue
        weights[fund_code] += weight_pct / 100.0
    total = sum(weights.values())
    if total <= 0:
        return {}, 0.0
    return {fund_code: weight / total for fund_code, weight in weights.items()}, total * 100.0


def compress_chart_rows(rows: list[dict[str, Any]], max_points: int = 180) -> list[dict[str, Any]]:
    if len(rows) <= max_points:
        return rows
    last_index = len(rows) - 1
    step = last_index / max(max_points - 1, 1)
    keep_indexes = {0, last_index}
    for idx in range(1, max_points - 1):
        keep_indexes.add(int(round(idx * step)))
    return [rows[index] for index in sorted(keep_indexes)]


def build_event_comparison_chart(
    event: dict[str, Any],
    quality: dict[str, Any],
    detail_rows: list[dict[str, Any]],
    returns_by_date: dict[str, dict[str, float]],
    available_dates: list[str],
) -> dict[str, Any] | None:
    start_date = norm_text(event.get("调仓日期"))
    end_date = norm_text(quality.get("区间结束锚点日期") or quality.get("下次调仓日期"))
    if not start_date or not end_date or start_date >= end_date:
        return None

    before_weights, before_weight_sum_pct = normalize_weight_map(detail_rows, "调前权重_百分比")
    after_weights, after_weight_sum_pct = normalize_weight_map(detail_rows, "调后权重_百分比")
    if not before_weights or not after_weights:
        return None

    before_values = {fund_code: weight for fund_code, weight in before_weights.items()}
    after_values = {fund_code: weight for fund_code, weight in after_weights.items()}
    before_missing_points = 0
    after_missing_points = 0

    interval_dates = [trade_date for trade_date in available_dates if start_date < trade_date <= end_date]
    if end_date not in interval_dates and start_date < end_date:
        interval_dates.append(end_date)
        interval_dates.sort()

    rows = [
        {
            "date": start_date,
            "beforeReturnPct": 0.0,
            "afterReturnPct": 0.0,
            "excessPct": 0.0,
        }
    ]

    for trade_date in interval_dates:
        date_returns = returns_by_date.get(trade_date, {})

        for fund_code in before_values:
            fund_return = date_returns.get(fund_code)
            if fund_return is None:
                before_missing_points += 1
                fund_return = 0.0
            before_values[fund_code] *= 1.0 + fund_return

        for fund_code in after_values:
            fund_return = date_returns.get(fund_code)
            if fund_return is None:
                after_missing_points += 1
                fund_return = 0.0
            after_values[fund_code] *= 1.0 + fund_return

        before_nav = sum(before_values.values())
        after_nav = sum(after_values.values())
        rows.append(
            {
                "date": trade_date,
                "beforeReturnPct": round_or_none((before_nav - 1.0) * 100.0, 6),
                "afterReturnPct": round_or_none((after_nav - 1.0) * 100.0, 6),
                "excessPct": round_or_none((after_nav - before_nav) * 100.0, 6),
            }
        )

    if len(rows) < 2:
        return None

    compact_rows = compress_chart_rows(rows)
    last_row = rows[-1]
    return {
        "startDate": start_date,
        "endDate": end_date,
        "endType": quality.get("区间结束类型"),
        "assessmentStatus": quality.get("评估状态"),
        "pointCount": len(compact_rows),
        "rawPointCount": len(rows),
        "beforeFundCount": len(before_weights),
        "afterFundCount": len(after_weights),
        "beforeWeightSumPct": round_or_none(before_weight_sum_pct, 4),
        "afterWeightSumPct": round_or_none(after_weight_sum_pct, 4),
        "beforeReturnPct": round_or_none(to_float(last_row.get("beforeReturnPct")), 6),
        "afterReturnPct": round_or_none(to_float(last_row.get("afterReturnPct")), 6),
        "excessPct": round_or_none(to_float(last_row.get("excessPct")), 6),
        "beforeMissingReturnPoints": before_missing_points,
        "afterMissingReturnPoints": after_missing_points,
        "rows": compact_rows,
    }


def build_rebalance_payload(
    conn: sqlite3.Connection,
    events: list[dict[str, Any]],
    detail_map: dict[str, list[dict[str, Any]]],
    event_quality: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    returns_by_date, available_dates = load_strategy_event_returns(conn, events, detail_map, event_quality)
    serialized_events: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event["调仓事件ID"])
        quality = event_quality.get(event_id, {})
        detail_rows = detail_map.get(event_id, [])
        details = [
            {
                "fundCode": row.get("基金代码"),
                "fundName": row.get("基金名称"),
                "groupName": row.get("分组名称"),
                "beforeWeightPct": round_or_none(to_float(row.get("调前权重_百分比")), 4),
                "afterWeightPct": round_or_none(to_float(row.get("调后权重_百分比")), 4),
                "changeWeightPct": round_or_none(to_float(row.get("权重变化_百分比")), 4),
                "action": row.get("调仓动作"),
                "matchStatus": row.get("基金代码匹配状态"),
            }
            for row in detail_rows
        ]
        comparison_chart = build_event_comparison_chart(
            event,
            quality,
            detail_rows,
            returns_by_date,
            available_dates,
        )
        serialized_events.append(
            {
                "eventId": event_id,
                "date": event.get("调仓日期"),
                "disclosureDate": event.get("披露日期"),
                "title": event.get("调仓标题"),
                "reason": event.get("调仓原因"),
                "previousPositionDate": event.get("上次仓位日期"),
                "currentPositionDate": event.get("本次仓位日期"),
                "eventTime": event.get("事件时间"),
                "assessment": quality.get("结果评价"),
                "assessmentLevel": quality.get("评估层级"),
                "assessmentStatus": quality.get("评估状态"),
                "excessPct": round_or_none(to_float(quality.get("调仓超额_百分比")), 6),
                "beforeReturnPct": round_or_none(to_float(quality.get("调前仓位收益率_百分比")), 6),
                "afterReturnPct": round_or_none(to_float(quality.get("调后仓位收益率_百分比")), 6),
                "directionalExcessPct": round_or_none(to_float(quality.get("方向性超额_百分比")), 6),
                "detailCount": len(details),
                "intervalEndDate": quality.get("区间结束锚点日期") or quality.get("下次调仓日期"),
                "intervalEndType": quality.get("区间结束类型"),
                "comparisonChart": comparison_chart,
                "details": details,
            }
        )
    return {
        "eventCount": len(serialized_events),
        "events": serialized_events,
    }


def table_counts_by_channel(conn: sqlite3.Connection, table_name: str) -> dict[str, int]:
    try:
        rows = fetch_dicts(
            conn,
            f"""
            SELECT "渠道ID" AS channel_id, COUNT(*) AS row_count
            FROM "{table_name}"
            GROUP BY "渠道ID"
            """,
        )
    except sqlite3.OperationalError:
        return {}
    return {str(row["channel_id"]): int(row["row_count"] or 0) for row in rows}


def table_total_count(conn: sqlite3.Connection, table_name: str) -> int:
    try:
        row = fetch_dicts(conn, f'SELECT COUNT(*) AS row_count FROM "{table_name}"')[0]
    except sqlite3.OperationalError:
        return 0
    return int(row["row_count"] or 0)


def load_official_governance_summary() -> dict[str, Any]:
    report_path = PROJECT_ROOT / "outputs" / "official_strategy_governance" / "latest_governance_report.json"
    if not report_path.exists():
        return {
            "generatedAt": None,
            "channels": [],
            "warnings": [],
            "summaryText": "尚未发现官方投顾专项核对报告。",
        }

    report = json.loads(report_path.read_text(encoding="utf-8"))
    channels: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    total_pass = 0
    total_warn = 0
    total_fail = 0

    for channel in report.get("channels", []):
        summary = channel.get("summary") or {}
        channel_id = str(channel.get("channel_id") or summary.get("channel_id") or "")
        status_counts = channel.get("check_status_counts") or {}
        pass_count = int(status_counts.get("pass") or 0)
        warn_count = int(status_counts.get("warn") or 0)
        fail_count = int(status_counts.get("fail") or 0)
        total_pass += pass_count
        total_warn += warn_count
        total_fail += fail_count
        result_label = "通过" if not warn_count and not fail_count else ("有提示" if not fail_count else "有失败")

        channels.append(
            {
                "channelId": channel_id,
                "channelName": summary.get("channel_name") or channel_id,
                "passCount": pass_count,
                "warningCount": warn_count,
                "failCount": fail_count,
                "resultLabel": result_label,
                "capturedAt": summary.get("captured_at"),
                "holdingPenetration": "基金级精确权重" if summary.get("holding_penetration_status") == "fund_weight_exact_public" else "按渠道披露口径",
            }
        )

        for check in channel.get("checks", []):
            status = norm_text(check.get("status")) or "pass"
            if status == "pass":
                continue
            item = norm_text(check.get("item")) or "未命名检查"
            detail = GOVERNANCE_WARNING_DETAILS.get(item, "该检查存在提示项，需要结合原始留痕和入库结果复核。")
            warnings.append(
                {
                    "channelId": channel_id,
                    "channelName": summary.get("channel_name") or channel_id,
                    "category": zh_label(check.get("category"), GOVERNANCE_CATEGORY_LABELS),
                    "item": zh_label(item, GOVERNANCE_ITEM_LABELS),
                    "status": zh_label(status, GOVERNANCE_STATUS_LABELS),
                    "observed": check.get("observed"),
                    "expected": check.get("expected"),
                    "detail": f"{detail} 观测值 {check.get('observed')}，目标值 {check.get('expected')}。",
                }
            )

    return {
        "generatedAt": report.get("generated_at"),
        "channels": channels,
        "warnings": warnings,
        "summaryText": f"专项核对共通过 {total_pass} 项，提示 {total_warn} 项，失败 {total_fail} 项。",
    }


def add_channel_labels(rows: list[dict[str, Any]], channel_map: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
    for row in rows:
        channel_id = str(row.get(key) or row.get("渠道ID") or "")
        row["channelName"] = channel_map.get(channel_id, {}).get("渠道名称") or channel_id
    return rows


def build_data_overview(
    conn: sqlite3.Connection,
    channel_map: dict[str, dict[str, Any]],
    summary_items: list[dict[str, Any]],
    quality_payload: dict[str, Any],
    generated_at: str,
    latest_nav_date: str | None,
) -> dict[str, Any]:
    table_counts = {label: table_counts_by_channel(conn, table_name) for label, table_name in DATA_TABLES}
    table_totals = {label: table_total_count(conn, table_name) for label, table_name in DATA_TABLES}
    governance = load_official_governance_summary()
    governance_by_channel = {item["channelId"]: item for item in governance.get("channels", [])}

    quality_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    latest_dates: dict[str, str] = {}
    for item in summary_items:
        channel_id = str(item.get("channelId") or "")
        quality_counts[channel_id]["策略总数"] += 1
        audit_status = norm_text(item.get("currentProjectionAuditStatus"))
        reason_category = norm_text(item.get("currentProjectionReasonCategory"))
        if audit_status:
            quality_counts[channel_id]["最新持仓已稽核策略数"] += 1
        if audit_status == "通过":
            quality_counts[channel_id]["最新持仓推算通过策略数"] += 1
        if audit_status == "小额差异":
            quality_counts[channel_id]["最新持仓小额差异策略数"] += 1
        if audit_status in ("需复核", "结构不一致"):
            quality_counts[channel_id]["最新持仓需复核策略数"] += 1
        if reason_category == "当前持仓有明细但无基金权重":
            quality_counts[channel_id]["当前明细无基金权重策略数"] += 1
        if reason_category == "当前持仓未披露":
            quality_counts[channel_id]["完全无当前明细策略数"] += 1
        if item.get("simulationIncluded"):
            quality_counts[channel_id]["可回放策略数"] += 1
        if item.get("qualityGrade") == "完整":
            quality_counts[channel_id]["严格完整策略数"] += 1
        if item.get("qualityGrade") == "完整_已修复":
            quality_counts[channel_id]["修复后可用策略数"] += 1
        if item.get("qualityGrade") == "不可回放":
            quality_counts[channel_id]["不可回放策略数"] += 1
        nav_date = norm_text(item.get("navLatestDate"))
        if nav_date and nav_date > latest_dates.get(channel_id, ""):
            latest_dates[channel_id] = nav_date

    all_channel_ids = sorted(
        set(channel_map)
        | {str(item.get("channelId")) for item in summary_items if item.get("channelId")}
        | {channel_id for counts in table_counts.values() for channel_id in counts}
    )
    channel_rows: list[dict[str, Any]] = []
    for channel_id in all_channel_ids:
        channel = channel_map.get(channel_id, {})
        governance_row = governance_by_channel.get(channel_id)
        if governance_row:
            check_label = "专项核对通过" if governance_row["resultLabel"] == "通过" else f"专项核对{governance_row['resultLabel']}"
            warning_count = governance_row["warningCount"]
        else:
            check_label = "已纳入模拟质量评估"
            warning_count = 0
        channel_rows.append(
            {
                "channelId": channel_id,
                "channelName": channel.get("渠道名称") or channel_id,
                "channelType": zh_label(channel.get("渠道类型"), CHANNEL_TYPE_LABELS),
                "loginRequirement": zh_label(channel.get("登录要求"), LOGIN_REQUIREMENT_LABELS),
                "strategyCount": table_counts.get("策略主档", {}).get(channel_id, quality_counts[channel_id]["策略总数"]),
                "officialDailyRows": table_counts.get("官方日度业绩", {}).get(channel_id, 0),
                "currentHoldingRows": table_counts.get("当前基金持仓", {}).get(channel_id, 0),
                "holdingGroupRows": table_counts.get("当前分组持仓", {}).get(channel_id, 0),
                "rebalanceEventRows": table_counts.get("调仓事件", {}).get(channel_id, 0),
                "rebalanceFundRows": table_counts.get("调仓基金明细", {}).get(channel_id, 0),
                "simulationNavRows": table_counts.get("标准回放净值", {}).get(channel_id, 0),
                "currentProjectionAuditRows": table_counts.get("最新持仓推算稽核", {}).get(channel_id, 0),
                "inferredCurrentHoldingRows": table_counts.get("推算补齐持仓", {}).get(channel_id, 0),
                "currentProjectionAuditedCount": quality_counts[channel_id]["最新持仓已稽核策略数"],
                "currentProjectionPassCount": quality_counts[channel_id]["最新持仓推算通过策略数"],
                "currentProjectionMinorDiffCount": quality_counts[channel_id]["最新持仓小额差异策略数"],
                "currentProjectionReviewCount": quality_counts[channel_id]["最新持仓需复核策略数"],
                "currentHoldingNoFundWeightCount": quality_counts[channel_id]["当前明细无基金权重策略数"],
                "currentHoldingNoDisclosureCount": quality_counts[channel_id]["完全无当前明细策略数"],
                "simulationIncludedCount": quality_counts[channel_id]["可回放策略数"],
                "strictCleanCount": quality_counts[channel_id]["严格完整策略数"],
                "repairedCount": quality_counts[channel_id]["修复后可用策略数"],
                "invalidCount": quality_counts[channel_id]["不可回放策略数"],
                "latestNavDate": latest_dates.get(channel_id),
                "qualityCheckLabel": check_label,
                "warningCount": warning_count,
                "note": channel.get("备注") or "已纳入本地标准化分析库。",
            }
        )

    return {
        "title": "投顾策略分析系统数据总览",
        "description": "当前页面先展示已接入渠道、入库数据量、质量核对结果和统一口径字典，再进入策略清单。底层数据来自本地标准化 SQLite 分析库。",
        "generatedAt": generated_at,
        "latestNavDate": latest_nav_date,
        "channelCount": len(channel_rows),
        "strategyCount": len(summary_items),
        "simulationIncludedCount": quality_payload["overview"]["simulationIncludedCount"],
        "strictCleanCount": quality_payload["overview"]["strictCleanCount"],
        "repairedCount": quality_payload["overview"]["repairedCount"],
        "invalidCount": quality_payload["overview"]["invalidCount"],
        "currentProjectionAuditedCount": sum(1 for item in summary_items if item.get("currentProjectionAuditStatus")),
        "currentProjectionPassCount": sum(1 for item in summary_items if item.get("currentProjectionAuditStatus") == "通过"),
        "currentProjectionMinorDiffCount": sum(1 for item in summary_items if item.get("currentProjectionAuditStatus") == "小额差异"),
        "currentProjectionReviewCount": sum(1 for item in summary_items if item.get("currentProjectionAuditStatus") in ("需复核", "结构不一致")),
        "currentHoldingNoFundWeightCount": sum(1 for item in summary_items if item.get("currentProjectionReasonCategory") == "当前持仓有明细但无基金权重"),
        "currentHoldingNoDisclosureCount": sum(1 for item in summary_items if item.get("currentProjectionReasonCategory") == "当前持仓未披露"),
        "dataRows": [{"name": label, "count": table_totals[label]} for label, _ in DATA_TABLES],
        "channels": channel_rows,
        "governance": governance,
        "dictionaries": [
            {
                "name": "渠道类型",
                "values": [
                    "公募基金公司：基金公司自有应用或官方页面披露的投顾策略",
                    "第三方基金销售平台：代销平台披露的投顾策略",
                    "财富管理子公司：基金公司财富子公司披露的投顾策略",
                ],
            },
            {
                "name": "登录要求",
                "values": [
                    "无需登录：公开页面或接口可获取",
                    "部分公开：基础信息公开，部分明细依赖页面状态",
                    "需要登录：依赖已授权登录态缓存或用户侧会话",
                ],
            },
            {
                "name": "净值来源",
                "values": [
                    "标准回放净值：按基金级仓位、底层基金收益和投顾费扣减生成",
                    "官方披露净值：使用渠道披露的日度净值或累计收益序列",
                    "暂无可用净值：当前仅有策略主档或持仓信息",
                ],
            },
            {
                "name": "回放质量",
                "values": [
                    "完整：仓位、基金净值和权重闭合均满足回放",
                    "完整（已修复）：经过轻微、可解释、可重复修复后可回放",
                    "不可回放：存在缺基金净值、权重不闭合或无调仓区间等硬缺口",
                    "未模拟：尚未进入标准回放质量评估",
                ],
            },
            {
                "name": "持仓穿透",
                "values": [
                    "精确基金权重：可穿透到基金代码、基金名称和基金占比",
                    "分组权重：仅能按渠道披露的资产分组或策略分组展示占比",
                ],
            },
            {
                "name": "调仓动作",
                "values": [
                    "新买入：调前无权重且调后有权重",
                    "清仓：调前有权重且调后无权重",
                    "增持：调后权重大于调前权重",
                    "减持：调后权重小于调前权重",
                    "基本不变：调前调后权重差异很小",
                ],
            },
        ],
    }


def build_quality_payload(
    summary_items: list[dict[str, Any]],
    sim_quality_map: dict[str, dict[str, Any]],
    rebalance_quality_map: dict[str, dict[str, Any]],
    conn: sqlite3.Connection,
    channel_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    included = [item for item in summary_items if item["simulationIncluded"]]
    strict = [item for item in included if item["qualityGrade"] == "完整"]
    repaired = [item for item in included if item["qualityGrade"] == "完整_已修复"]
    invalid = [item for item in summary_items if item["qualityGrade"] == "不可回放"]
    unassessed = [item for item in summary_items if item["qualityGrade"] == "未模拟"]

    invalid_reason_rows = fetch_dicts(
        conn,
        """
        SELECT "首个问题类型" AS issue_type, COUNT(*) AS strategy_count
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ? AND "是否纳入模拟" = 0
        GROUP BY "首个问题类型"
        ORDER BY strategy_count DESC, issue_type
        """,
        [ALGORITHM_VERSION],
    )
    channel_count_map: dict[str, dict[str, Any]] = {}
    for item in summary_items:
        channel_id = str(item.get("channelId") or "")
        if channel_id not in channel_count_map:
            channel_count_map[channel_id] = {
                "渠道ID": channel_id,
                "strategy_count": 0,
                "strict_clean_count": 0,
                "repaired_count": 0,
                "included_count": 0,
            }
        channel_count_map[channel_id]["strategy_count"] += 1
        if item.get("qualityGrade") == "完整":
            channel_count_map[channel_id]["strict_clean_count"] += 1
        if item.get("qualityGrade") == "完整_已修复":
            channel_count_map[channel_id]["repaired_count"] += 1
        if item.get("simulationIncluded"):
            channel_count_map[channel_id]["included_count"] += 1
    channel_rows = [channel_count_map[channel_id] for channel_id in sorted(channel_count_map)]
    channel_deviation_rows = fetch_dicts(
        conn,
        """
        SELECT
            "渠道ID" AS channelId,
            "可比策略数" AS comparableCount,
            "官方样本充足策略数" AS sufficientOfficialCount,
            "推荐官方口径" AS recommendedBasis,
            "费后绝对偏差均值_百分点" AS netAbsDiffMeanPct,
            "费前绝对偏差均值_百分点" AS grossAbsDiffMeanPct,
            "最优口径绝对偏差均值_百分点" AS bestAbsDiffMeanPct,
            "费前相对费后平均改善_百分点" AS grossImprovementPct,
            "渠道算法判断" AS judgement,
            "下一步优化建议" AS suggestion
        FROM "渠道官方偏差分析"
        WHERE "算法版本" = ?
        ORDER BY "渠道ID"
        """,
        [ALGORITHM_VERSION],
    )
    algorithm_variant_rows = fetch_dicts(
        conn,
        """
        SELECT
            "渠道ID" AS channelId,
            "候选算法ID" AS candidateId,
            "候选算法名称" AS candidateName,
            "调仓生效口径" AS timingBasis,
            "费用口径" AS feeBasis,
            "可比策略数" AS comparableCount,
            "胜出策略数" AS winnerCount,
            "绝对偏差均值_百分点" AS absDiffMeanPct,
            "绝对偏差中位数_百分点" AS absDiffMedianPct,
            "绝对偏差P90_百分点" AS absDiffP90Pct,
            "较基准改善_百分点" AS baselineImprovementPct,
            "渠道算法判断" AS judgement,
            "下一步优化建议" AS suggestion
        FROM "渠道官方算法候选评估"
        WHERE "算法版本" = ? AND "是否渠道最优" = 1
        ORDER BY "渠道ID", "候选算法ID"
        """,
        [ALGORITHM_VERSION],
    )
    for row in channel_rows:
        channel_id = str(row.get("渠道ID") or "")
        row["渠道名称"] = channel_map.get(channel_id, {}).get("渠道名称") or channel_id
    add_channel_labels(channel_deviation_rows, channel_map, "channelId")
    add_channel_labels(algorithm_variant_rows, channel_map, "channelId")

    positive_candidates = []
    negative_candidates = []
    for strategy_id, row in rebalance_quality_map.items():
        valid_count = int(row.get("有效调仓事件数") or 0)
        avg_excess = to_float(row.get("平均调仓超额_百分比"))
        base = {
            "id": strategy_id,
            "name": row.get("策略名称"),
            "channelId": row.get("渠道ID"),
            "advisor": row.get("投顾机构"),
            "validEventCount": valid_count,
            "fullEventCount": int(row.get("全组合有效事件数") or 0),
            "winRatePct": round_or_none(to_float(row.get("胜率_有效事件_百分比")), 4),
            "averageExcessPct": round_or_none(avg_excess, 6),
            "historyRating": row.get("历史评价"),
        }
        if valid_count >= 5 and avg_excess is not None:
            positive_candidates.append(base)
            negative_candidates.append(base)

    positive_candidates.sort(key=lambda item: item["averageExcessPct"] or -999, reverse=True)
    negative_candidates.sort(key=lambda item: item["averageExcessPct"] or 999)

    return {
        "overview": {
            "strategyCount": len(summary_items),
            "assessedCount": len(summary_items) - len(unassessed),
            "simulationIncludedCount": len(included),
            "strictCleanCount": len(strict),
            "repairedCount": len(repaired),
            "invalidCount": len(invalid),
            "unassessedCount": len(unassessed),
        },
        "channels": channel_rows,
        "channelDeviation": channel_deviation_rows,
        "algorithmVariants": algorithm_variant_rows,
        "invalidReasons": invalid_reason_rows,
        "positiveRebalance": positive_candidates[:20],
        "negativeRebalance": negative_candidates[:20],
        "invalidStrategies": [
            {
                "id": item["id"],
                "name": item["name"],
                "channelId": item["channelId"],
                "qualityIssue": item["qualityIssue"],
                "rebalanceEventCount": item["rebalanceEventCount"],
                "holdingMixText": item["holdingMixText"],
            }
            for item in sorted(invalid, key=lambda entry: (entry["qualityIssue"] or "", entry["name"] or ""))
        ],
        "alignmentOutliers": [
            {
                "id": item["id"],
                "name": item["name"],
                "channelId": item["channelId"],
                "officialDiffPct": item["officialDiffPct"],
                "cumulativeReturnPct": item["cumulativeReturnPct"],
            }
            for item in sorted(
                [entry for entry in included if entry.get("officialDiffPct") is not None],
                key=lambda entry: abs(entry["officialDiffPct"]),
                reverse=True,
            )[:20]
        ],
    }


def main() -> None:
    args = parse_args()
    global ALGORITHM_VERSION
    ALGORITHM_VERSION = args.algorithm_version
    db_path = args.db_path
    site_dir = args.site_dir
    data_dir = site_dir / "data"
    detail_dir = data_dir / "details"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    channel_map = load_channel_map(conn)
    strategy_map = load_strategy_map(conn)
    simulation_quality_map = load_simulation_quality(conn)
    rebalance_quality_map = load_rebalance_quality(conn)
    current_projection_audit_map = load_current_projection_audit(conn)
    inferred_current_holdings_map = load_inferred_current_holdings(conn)
    disclosed_risk_map = load_disclosed_risk_map(conn)
    latest_holdings_map, latest_holding_groups_map = load_latest_holdings(conn)
    rebalance_events_map = load_rebalance_events(conn)
    rebalance_details_map = load_rebalance_details(conn)
    event_quality_map = load_event_quality(conn)
    nav_map = load_strategy_nav(conn)

    summary_items: list[dict[str, Any]] = []
    detail_dir.mkdir(parents=True, exist_ok=True)
    expected_detail_files: set[str] = set()

    for strategy_id, strategy in sorted(strategy_map.items(), key=lambda item: (str(item[1].get("渠道ID") or ""), str(item[1].get("策略名称") or ""))):
        simulation_quality = simulation_quality_map.get(strategy_id)
        rebalance_quality = rebalance_quality_map.get(strategy_id)
        current_projection_audit = current_projection_audit_map.get(strategy_id)
        disclosed_risk = disclosed_risk_map.get(strategy_id, {})
        nav_bundle = nav_map.get(strategy_id)
        holdings = latest_holdings_map.get(strategy_id, [])
        holding_groups = latest_holding_groups_map.get(strategy_id, [])
        summary_item = build_summary_item(
            strategy,
            channel_map,
            simulation_quality,
            rebalance_quality,
            current_projection_audit,
            nav_bundle,
            disclosed_risk,
            holdings,
            holding_groups,
        )
        summary_items.append(summary_item)

        nav_rows = ensure_drawdown(nav_bundle.rows) if nav_bundle else []
        detail_payload = {
            "id": strategy_id,
            "summary": summary_item,
            "strategy": {
                "id": strategy_id,
                "channelId": strategy.get("渠道ID"),
                "channelName": channel_map.get(strategy.get("渠道ID"), {}).get("渠道名称"),
                "channelStrategyId": strategy.get("渠道策略ID"),
                "name": strategy.get("策略名称"),
                "advisor": strategy.get("投顾机构"),
                "type": strategy.get("策略类型"),
                "risk": strategy.get("风险等级"),
                "inceptionDate": strategy.get("成立日期"),
                "holdingSuggestion": strategy.get("建议持有时长"),
                "minimumInvest": strategy.get("起投金额"),
                "serviceFee": strategy.get("投顾费率"),
                "benchmark": strategy.get("业绩基准"),
                "description": strategy.get("策略描述"),
                "tags": strategy.get("tags") or [],
            },
            "simulationQuality": simulation_quality,
            "rebalanceQuality": rebalance_quality,
            "currentProjectionAudit": current_projection_audit,
            "disclosedRisk": {
                code: {
                    "date": row.get("统计日期"),
                    "label": row.get("区间名称"),
                    "returnPct": round_or_none(to_float(row.get("官方收益率_百分比")), 6),
                    "maxDrawdownPct": round_or_none(to_float(row.get("官方最大回撤_百分比")), 6),
                    "volatilityPct": round_or_none(to_float(row.get("官方波动率_百分比")), 6),
                    "sharpe": round_or_none(to_float(row.get("官方夏普")), 6),
                    "benchmarkReturnPct": round_or_none(to_float(row.get("官方基准收益率_百分比")), 6),
                    "source": row.get("数据来源字段"),
                }
                for code, row in disclosed_risk.items()
            },
            "deviation": build_deviation_payload(summary_item, simulation_quality),
            "nav": {
                "source": nav_bundle.source if nav_bundle else "none",
                "rows": nav_rows,
                "intervals": summary_item["intervals"],
            },
            "holdings": build_holdings_payload(holdings, holding_groups),
            "inferredCurrentHoldings": [
                {
                    "fundCode": row.get("基金代码"),
                    "fundName": row.get("基金名称"),
                    "weightPct": round_or_none(to_float(row.get("推算基金权重_百分比")), 6),
                    "lastRebalanceWeightPct": round_or_none(to_float(row.get("最后调仓后权重_百分比")), 6),
                    "returnFactor": round_or_none(to_float(row.get("收益因子_复权")), 10),
                    "positionDate": row.get("推算持仓日期"),
                    "confidence": row.get("置信度"),
                    "source": row.get("推算来源"),
                }
                for row in inferred_current_holdings_map.get(strategy_id, [])
            ],
            "rebalance": build_rebalance_payload(
                conn,
                rebalance_events_map.get(strategy_id, []),
                rebalance_details_map,
                event_quality_map,
            ),
            "segments": fetch_dicts(
                conn,
                """
                SELECT
                    "区间序号",
                    "调仓日期",
                    "区间结束日期",
                    "区间结束类型",
                    "区间是否有效",
                    "是否纳入模拟",
                    "质量等级",
                    "问题类型",
                    "问题说明",
                    "修复说明",
                    "权重和_百分比",
                    "缺净值基金数",
                    "起始覆盖不足基金数",
                    "结束覆盖不足基金数",
                    "区间交易日数",
                    "缺失日收益填补点数",
                    "区间收益率_百分比"
                FROM "策略模拟净值区间"
                WHERE "算法版本" = ? AND "统一策略ID" = ?
                ORDER BY "区间序号"
                """,
                [ALGORITHM_VERSION, strategy_id],
            ),
            "dataCoverage": {
                "hasSimulation": int(summary_item["simulationIncluded"]),
                "hasOfficialDaily": int(nav_bundle.source == "official") if nav_bundle else 0,
                "holdingFundCount": len(holdings),
                "holdingGroupCount": len(holding_groups),
                "rebalanceEventCount": len(rebalance_events_map.get(strategy_id, [])),
            },
        }
        detail_filename = f"{sanitize_filename(strategy_id)}.js"
        expected_detail_files.add(detail_filename)
        json_js_assignment(
            detail_dir / detail_filename,
            f'window.__STRATEGY_CENTER_DATA__.details["{strategy_id}"]',
            detail_payload,
        )

    for path in detail_dir.glob("*.js"):
        if path.name not in expected_detail_files:
            path.unlink()

    summary_items.sort(
        key=lambda item: (
            int(item["simulationIncluded"]),
            1 if item["qualityGrade"] == "完整" else 0,
            to_float(item.get("cumulativeReturnPct")) or -9999.0,
        ),
        reverse=True,
    )

    quality_payload = build_quality_payload(summary_items, simulation_quality_map, rebalance_quality_map, conn, channel_map)
    latest_generated_at = max(
        (
            norm_text(row.get("生成时间"))
            for row in simulation_quality_map.values()
            if norm_text(row.get("生成时间"))
        ),
        default=datetime.now().isoformat(timespec="seconds"),
    )
    latest_nav_date = max(
        (
            item.get("navLatestDate")
            for item in summary_items
            if item.get("navLatestDate")
        ),
        default=None,
    )

    data_overview = build_data_overview(conn, channel_map, summary_items, quality_payload, latest_generated_at, latest_nav_date)

    summary_payload = {
        "generatedAt": latest_generated_at,
        "latestNavDate": latest_nav_date,
        "strategyCount": len(summary_items),
        "simulationIncludedCount": quality_payload["overview"]["simulationIncludedCount"],
        "strictCleanCount": quality_payload["overview"]["strictCleanCount"],
        "repairedCount": quality_payload["overview"]["repairedCount"],
        "invalidCount": quality_payload["overview"]["invalidCount"],
        "dataOverview": data_overview,
        "strategies": summary_items,
    }

    wrapper_prefix = "window.__STRATEGY_CENTER_DATA__ = window.__STRATEGY_CENTER_DATA__ || {};\n"
    write_text_if_changed(
        data_dir / SUMMARY_JS,
        wrapper_prefix + f"window.__STRATEGY_CENTER_DATA__.summary = {json.dumps(summary_payload, ensure_ascii=False, separators=(',', ':'))};\n",
    )
    write_text_if_changed(
        data_dir / QUALITY_JS,
        wrapper_prefix + f"window.__STRATEGY_CENTER_DATA__.quality = {json.dumps(quality_payload, ensure_ascii=False, separators=(',', ':'))};\n",
    )
    write_text_if_changed(site_dir / "index.html", STRATEGY_CENTER_INDEX_HTML)

    manifest = {
        "generatedAt": latest_generated_at,
        "latestNavDate": latest_nav_date,
        "summaryFile": f"data/{SUMMARY_JS}",
        "qualityFile": f"data/{QUALITY_JS}",
        "detailFiles": {item["id"]: item["detailFile"] for item in summary_items},
    }
    write_text_if_changed(site_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    conn.close()
    print(json.dumps({"siteDir": str(site_dir), **quality_payload["overview"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
