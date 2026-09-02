from __future__ import annotations

import csv
import html
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DATA_DIR = ROOT / "site" / "basic_data" / "data"
DB_PATH = ROOT / "data" / "analysis_zh_current.sqlite"
OUT_ROOT = ROOT / "outputs" / "ai_core_exposure"


CORE_PATTERNS = [
    ("直接AI", ["人工智能", "AI", "AIGC", "机器学习", "机器人"]),
    ("算力/云/数据", ["算力", "云计算", "大数据", "数据中心", "信创", "软件开发", "信息技术"]),
    ("芯片/半导体", ["半导体", "芯片", "集成电路"]),
    ("AI基础设施", ["通信设备", "5G通信", "5G", "光模块"]),
]

BROAD_ONLY_PATTERNS = [
    "科技",
    "TMT",
    "电子",
    "传媒",
    "互联网",
    "恒生科技",
    "纳斯达克",
    "创新",
]


def today_stamp() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def raw(value: Any) -> str:
    return "" if value is None else str(value)


def num(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def load_assigned_js(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    idx = text.find("=")
    if idx < 0:
        raise ValueError(f"Cannot parse assigned JS: {path}")
    payload = text[idx + 1 :].strip()
    if payload.endswith(";"):
        payload = payload[:-1]
    return json.loads(payload)


def parse_date(value: Any) -> date | None:
    text = raw(value)[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def pct(value: Any, digits: int = 2) -> str:
    number = num(value)
    return "" if number is None else f"{number:.{digits}f}%"


def classify_ai_core(text: str) -> tuple[bool, list[str]]:
    hits: list[str] = []
    haystack = text.upper() + " " + text
    for group, terms in CORE_PATTERNS:
        matched = [term for term in terms if term.upper() in haystack]
        if matched:
            hits.append(f"{group}:{'/'.join(matched[:4])}")
    return bool(hits), hits


def classify_broad_only(text: str) -> bool:
    haystack = text.upper() + " " + text
    return any(term.upper() in haystack for term in BROAD_ONLY_PATTERNS)


def build_fund_ai_map(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    fund_map: dict[str, dict[str, Any]] = {}

    def add(code: str, name: str, pieces: list[str]) -> None:
        code = raw(code).strip()
        if not code:
            return
        text = " ".join(raw(x) for x in [code, name, *pieces])
        is_core, hits = classify_ai_core(text)
        broad_only = classify_broad_only(text) and not is_core
        current = fund_map.get(code, {"code": code, "name": name, "text": "", "hits": [], "is_core": False, "broad_only": False})
        current["name"] = current.get("name") or name
        current["text"] = f"{current.get('text', '')} {text}".strip()
        current["is_core"] = bool(current.get("is_core")) or is_core
        current["broad_only"] = bool(current.get("broad_only")) or broad_only
        current["hits"] = sorted(set([*current.get("hits", []), *hits]))
        fund_map[code] = current

    for row in con.execute(
        """
        select 基金代码, 基金名称, 基金公司, 基金类型, 跟踪指数, 主题标签JSON
        from 基金信息
        """
    ):
        add(raw(row[0]), raw(row[1]), [raw(x) for x in row[2:]])

    for row in con.execute(
        """
        select 基金代码, 标准基金名称, 天天基金细分类, 天天基金大类, 天天基金二级分类,
               主题标签JSON, 标准资产大类, 标准资产细类, 市场地域标签, 主动被动标签, 投顾资产分类桶
        from 基金标准分类字典
        """
    ):
        add(raw(row[0]), raw(row[1]), [raw(x) for x in row[2:]])

    return fund_map


def ai_info_for_fund(code: str, name: str, fund_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    info = fund_map.get(raw(code).strip())
    if info:
        if info["is_core"] or info["broad_only"]:
            return info
    is_core, hits = classify_ai_core(f"{code} {name}")
    return {"code": code, "name": name, "is_core": is_core, "broad_only": classify_broad_only(name) and not is_core, "hits": hits}


def read_summary() -> dict[str, Any]:
    payload = load_assigned_js(DATA_DIR / "basic_summary.js")
    return payload.get("summary", payload)


def read_current_ai_exposure(fund_map: dict[str, dict[str, Any]]) -> tuple[dict[str, float], dict[str, list[dict[str, Any]]]]:
    ai_pack = load_assigned_js(DATA_DIR / "ai_semantic_index.js")
    fields = ai_pack.get("fields", [])
    idx = {name: i for i, name in enumerate(fields)}
    exposure: dict[str, float] = defaultdict(float)
    funds: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ai_pack.get("rows", []):
        sid = raw(row[idx["统一策略ID"]])
        code = raw(row[idx["基金代码"]])
        name = raw(row[idx["基金名称"]])
        weight = num(row[idx["权重"]], 0) or 0
        if not sid or weight <= 0:
            continue
        info = ai_info_for_fund(code, name, fund_map)
        if not info["is_core"]:
            continue
        exposure[sid] += weight
        funds[sid].append(
            {
                "code": code,
                "name": name,
                "weight": weight,
                "hits": "；".join(info.get("hits") or []),
            }
        )
    for sid in list(funds):
        funds[sid].sort(key=lambda item: item["weight"], reverse=True)
    return dict(exposure), funds


def strategy_rows(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {raw(row.get("统一策略ID")): row for row in summary.get("strategies", []) if raw(row.get("统一策略ID"))}


def event_exposures(
    con: sqlite3.Connection,
    fund_map: dict[str, dict[str, Any]],
    start: date,
    end: date,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    # Include events before the window so the opening state can be carried into the first day.
    lookback = start - timedelta(days=730)
    rows = con.execute(
        """
        select 调仓事件ID, 统一策略ID, 调仓日期, 基金代码, 基金名称, 调后权重_百分比
        from 策略调仓明细
        where 调仓日期 >= ? and 调仓日期 <= ?
        order by 统一策略ID, 调仓日期, 调仓事件ID
        """,
        (lookback.isoformat(), end.isoformat()),
    )
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event_id, sid, d, code, name, after_weight in rows:
        sid = raw(sid)
        dt = parse_date(d)
        if not sid or not dt:
            continue
        weight = num(after_weight, 0) or 0
        key = (sid, dt.isoformat(), raw(event_id))
        item = grouped.setdefault(key, {"sid": sid, "date": dt, "event_id": raw(event_id), "ai_weight": 0.0, "total_after": 0.0, "funds": []})
        if weight > 0:
            item["total_after"] += weight
        info = ai_info_for_fund(raw(code), raw(name), fund_map)
        if weight > 0 and info["is_core"]:
            item["ai_weight"] += weight
            item["funds"].append({"code": raw(code), "name": raw(name), "weight": weight, "hits": "；".join(info.get("hits") or [])})

    events_by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in grouped.values():
        item["ai_weight"] = round(item["ai_weight"], 6)
        item["total_after"] = round(item["total_after"], 6)
        item["funds"].sort(key=lambda x: x["weight"], reverse=True)
        events_by_strategy[item["sid"]].append(item)

    for sid in list(events_by_strategy):
        events_by_strategy[sid].sort(key=lambda x: (x["date"], x["event_id"]))

    snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sid, events in events_by_strategy.items():
        opening = None
        for item in events:
            if item["date"] <= start:
                opening = item
            elif item["date"] <= end:
                snapshots[sid].append(item)
        if opening:
            synthetic = {**opening, "date": start, "source": f"窗口起点沿用{opening['date'].isoformat()}调仓后仓位"}
            snapshots[sid].insert(0, synthetic)
        for item in snapshots[sid]:
            if item["funds"]:
                evidence[sid].append(item)
    return snapshots, evidence


def time_weighted_stats(snapshots: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    if not snapshots:
        return {"mean": 0.0, "peak": 0.0, "peak_date": "", "days": 0, "points": 0, "first_ai_date": ""}
    cleaned = []
    seen = set()
    for item in sorted(snapshots, key=lambda x: (x["date"], x.get("event_id", ""))):
        key = item["date"]
        if key in seen:
            # Keep the last event on the same day.
            cleaned[-1] = item
        else:
            cleaned.append(item)
            seen.add(key)
    total_days = 0
    weighted_sum = 0.0
    peak = -1.0
    peak_date = ""
    first_ai_date = ""
    for i, item in enumerate(cleaned):
        current_date = max(item["date"], start)
        next_date = cleaned[i + 1]["date"] if i + 1 < len(cleaned) else end
        next_date = min(next_date, end)
        days = max((next_date - current_date).days, 0)
        if i == len(cleaned) - 1 and current_date <= end:
            days = max((end - current_date).days + 1, 1)
        exposure = float(item.get("ai_weight") or 0)
        total_days += days
        weighted_sum += exposure * days
        if exposure > peak:
            peak = exposure
            peak_date = item["date"].isoformat()
        if exposure > 0 and not first_ai_date:
            first_ai_date = item["date"].isoformat()
    mean = weighted_sum / total_days if total_days else 0.0
    return {"mean": mean, "peak": max(0.0, peak), "peak_date": peak_date, "days": total_days, "points": len(cleaned), "first_ai_date": first_ai_date}


def load_strategy_nav(con: sqlite3.Connection, sids: list[str], start: date, end: date) -> dict[str, list[tuple[str, float]]]:
    if not sids:
        return {}
    out: dict[str, list[tuple[str, float]]] = {}
    for i in range(0, len(sids), 400):
        batch = sids[i : i + 400]
        placeholders = ",".join(["?"] * len(batch))
        rows = con.execute(
            f"""
            select 统一策略ID, 交易日期, 标准费后单位净值
            from 策略标准业绩净值
            where 统一策略ID in ({placeholders}) and 交易日期 >= ? and 交易日期 <= ?
            order by 统一策略ID, 交易日期
            """,
            [*batch, start.isoformat(), end.isoformat()],
        ).fetchall()
        for sid, d, nav in rows:
            value = num(nav)
            if value and value > 0:
                out.setdefault(raw(sid), []).append((raw(d), value))
    return out


def normalized_series(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    if not series:
        return []
    base = series[0][1]
    if not base:
        return []
    return [(d, v / base * 100) for d, v in series if v and v > 0]


def make_equal_weight_index(series_by_id: dict[str, list[tuple[str, float]]]) -> list[tuple[str, float]]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for series in series_by_id.values():
        for d, v in normalized_series(series):
            by_date[d].append(v)
    return [(d, sum(values) / len(values)) for d, values in sorted(by_date.items()) if values]


def load_fund_reference_index(con: sqlite3.Connection, fund_codes: list[str], start: date, end: date) -> list[tuple[str, float]]:
    series_by_fund: dict[str, list[tuple[str, float]]] = {}
    for i in range(0, len(fund_codes), 400):
        batch = fund_codes[i : i + 400]
        placeholders = ",".join(["?"] * len(batch))
        rows = con.execute(
            f"""
            select 基金代码, 交易日期, coalesce(累计净值, 单位净值)
            from 基金日度净值
            where 基金代码 in ({placeholders}) and 交易日期 >= ? and 交易日期 <= ?
            order by 基金代码, 交易日期
            """,
            [*batch, start.isoformat(), end.isoformat()],
        ).fetchall()
        for code, d, nav in rows:
            value = num(nav)
            if value and value > 0:
                series_by_fund.setdefault(raw(code), []).append((raw(d), value))
    return make_equal_weight_index(series_by_fund)


def load_tmt_index(con: sqlite3.Connection, start: date, end: date) -> list[tuple[str, float]]:
    rows = con.execute(
        """
        select 交易日期, 收盘点位
        from 指数日度行情
        where 指数代码 = '000998.SH' and 交易日期 >= ? and 交易日期 <= ?
        order by 交易日期
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return normalized_series([(raw(d), float(v)) for d, v in rows if num(v)])


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def build_charts(out_dir: Path, selected: list[dict[str, Any]], all_points: list[dict[str, Any]], trend_rows: list[dict[str, Any]]) -> dict[str, str]:
    import matplotlib.pyplot as plt
    import seaborn as sns

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Segoe UI", "Arial", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    sns.set_theme(style="whitegrid", font="Microsoft YaHei")

    colors = {
        "blue": "#5477C4",
        "gold": "#B8A037",
        "orange": "#CC6F47",
        "olive": "#71B436",
        "pink": "#BD569B",
        "muted": "#7A828F",
        "grid": "#E6E8F0",
        "ink": "#1F2430",
    }
    paths: dict[str, str] = {}

    if trend_rows:
        fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
        series_names = ["AI核心基金池等权参考", "入选策略等权净值", "中证TMT"]
        palette = {
            "AI核心基金池等权参考": colors["orange"],
            "入选策略等权净值": colors["blue"],
            "中证TMT": colors["muted"],
        }
        for name in series_names:
            xs = [datetime.strptime(r["日期"], "%Y-%m-%d") for r in trend_rows if r["系列"] == name]
            ys = [r["指数点位"] for r in trend_rows if r["系列"] == name]
            if not xs:
                continue
            ax.plot(xs, ys, label=name, color=palette[name], linewidth=2.2 if name != "中证TMT" else 1.7, linestyle="--" if name == "中证TMT" else "-")
        ax.axhline(100, color="#C5CAD3", linewidth=1, linestyle=":")
        fig.text(0.08, 0.96, "AI核心高暴露策略与参考指数走势", fontsize=15, fontweight="bold", color=colors["ink"], ha="left", va="top")
        fig.text(0.08, 0.925, "近一年归一到100；策略为入选组合等权，参考指数为本地AI核心基金池等权，中证TMT为本地官方指数", fontsize=10, color="#6F768A", ha="left", va="top")
        ax.set_ylabel("归一化点位")
        ax.set_xlabel("")
        ax.legend(loc="upper left", frameon=False, ncol=3)
        ax.grid(axis="y", color=colors["grid"], linewidth=0.8)
        ax.grid(axis="x", visible=False)
        fig.autofmt_xdate(rotation=0)
        fig.tight_layout(rect=[0, 0, 1, 0.9])
        p = out_dir / "ai_core_performance_trend.png"
        fig.savefig(p, bbox_inches="tight")
        plt.close(fig)
        paths["trend"] = p.name

    if all_points:
        fig, ax = plt.subplots(figsize=(11, 7), dpi=150)
        xs = [r["AI核心均值暴露"] for r in all_points]
        ys = [r["近1年收益"] for r in all_points]
        selected_flags = [r["是否入选"] for r in all_points]
        sizes = [max(35, min(240, r["AI核心峰值暴露"] * 2.2)) for r in all_points]
        point_colors = [colors["orange"] if flag else "#C5CAD3" for flag in selected_flags]
        edge_colors = [colors["ink"] if flag else "#FFFFFF" for flag in selected_flags]
        ax.scatter(xs, ys, s=sizes, c=point_colors, edgecolors=edge_colors, linewidths=0.6, alpha=0.82)
        ax.axvline(50, color=colors["orange"], linestyle="--", linewidth=1.2)
        ax.axhline(0, color="#C5CAD3", linestyle=":", linewidth=1)
        top_labels = sorted([r for r in all_points if r["是否入选"]], key=lambda x: x["近1年收益"], reverse=True)[:10]
        for r in top_labels:
            ax.text(r["AI核心均值暴露"] + 1, r["近1年收益"], r["策略名称"][:10], fontsize=8, color=colors["ink"])
        fig.text(0.08, 0.96, "策略点阵图：AI核心暴露与近1年收益", fontsize=15, fontweight="bold", color=colors["ink"], ha="left", va="top")
        fig.text(0.08, 0.925, "横轴为最近一年时间加权AI核心暴露均值；纵轴为近1年收益；点大小表示AI核心暴露峰值", fontsize=10, color="#6F768A", ha="left", va="top")
        ax.set_xlabel("AI核心暴露均值（%）")
        ax.set_ylabel("近1年收益（%）")
        ax.grid(axis="both", color=colors["grid"], linewidth=0.8)
        fig.tight_layout(rect=[0, 0, 1, 0.9])
        p = out_dir / "ai_core_strategy_scatter.png"
        fig.savefig(p, bbox_inches="tight")
        plt.close(fig)
        paths["scatter"] = p.name

    return paths


def html_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "<p class='empty'>无数据</p>"
    head = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
    body = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(raw(row.get(col, '')))}</td>" for col in columns)
        body.append(f"<tr>{cells}</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def write_report(
    out_dir: Path,
    selected: list[dict[str, Any]],
    chart_paths: dict[str, str],
    start: date,
    end: date,
    source_counts: dict[str, Any],
) -> None:
    selected_preview = selected[:]
    columns = [
        "策略名称",
        "投顾机构",
        "风险等级",
        "业务分类",
        "近1年收益",
        "最大回撤",
        "AI核心均值暴露",
        "AI核心峰值暴露",
        "当前AI核心暴露",
        "峰值日期",
        "主要AI核心基金",
    ]
    rows_for_html = []
    for row in selected_preview:
        rows_for_html.append(
            {
                **row,
                "近1年收益": pct(row.get("近1年收益")),
                "最大回撤": pct(row.get("最大回撤")),
                "AI核心均值暴露": pct(row.get("AI核心均值暴露")),
                "AI核心峰值暴露": pct(row.get("AI核心峰值暴露")),
                "当前AI核心暴露": pct(row.get("当前AI核心暴露")),
            }
        )

    trend_img = f"<img src='{html.escape(chart_paths.get('trend', ''))}' alt='AI核心高暴露策略与参考指数走势'>" if chart_paths.get("trend") else ""
    scatter_img = f"<img src='{html.escape(chart_paths.get('scatter', ''))}' alt='策略点阵图'>" if chart_paths.get("scatter") else ""

    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>AI核心暴露策略筛选报告</title>
  <style>
    body {{ margin: 0; background: #f6f7f9; color: #1f2430; font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 24px 48px; }}
    section {{ background: #fff; border: 1px solid #e2e5ea; border-radius: 10px; padding: 18px 20px; margin: 16px 0; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; }}
    p, li {{ line-height: 1.75; }}
    .muted {{ color: #667085; }}
    .kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }}
    .kpi {{ border: 1px solid #e2e5ea; border-radius: 8px; padding: 12px; background: #fbfcfd; }}
    .kpi b {{ display: block; font-size: 24px; margin-bottom: 4px; }}
    img {{ width: 100%; height: auto; display: block; border: 1px solid #e2e5ea; border-radius: 8px; background: #fff; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #e6e8f0; padding: 8px 9px; text-align: left; vertical-align: top; }}
    th {{ background: #f4f5f7; white-space: nowrap; }}
    td {{ min-width: 86px; }}
    .empty {{ color: #667085; }}
    code {{ background: #f4f5f7; border-radius: 4px; padding: 1px 4px; }}
  </style>
</head>
<body>
<main>
  <h1>AI核心暴露策略筛选报告</h1>
  <p class="muted">窗口：{start.isoformat()} 至 {end.isoformat()}；数据源：本地 SQLite 分析库、basic_data 当前持仓语义索引、调仓明细和基金净值。</p>

  <section>
    <h2>Executive Summary</h2>
    <ul>
      <li><b>严格阈值下入选 {len(selected)} 只策略。</b> 口径为最近一年 AI核心暴露时间加权均值 ≥50%，或调仓后/当前快照峰值 ≥50%。</li>
      <li><b>AI核心暴露不是“科技”宽口径。</b> 只纳入人工智能、算力/云/大数据、芯片/半导体、通信设备/5G等明确AI产业链关键词；科技、TMT、互联网、恒生科技、纳指等仅作泛科技，不单独计入核心。</li>
      <li><b>走势图使用本地可回溯参考。</b> AI主题参考线由入选策略持有过的AI核心基金净值等权合成；同时放入本地已有的中证TMT指数作为宽科技参照。</li>
    </ul>
  </section>

  <section>
    <h2>核心指标</h2>
    <div class="kpis">
      <div class="kpi"><b>{len(selected)}</b><span>严格入选策略</span></div>
      <div class="kpi"><b>{source_counts.get('all_candidate_points', 0)}</b><span>点阵图策略样本</span></div>
      <div class="kpi"><b>{source_counts.get('ai_core_funds', 0)}</b><span>AI核心基金代码</span></div>
      <div class="kpi"><b>{source_counts.get('trend_days', 0)}</b><span>走势日期点</span></div>
    </div>
  </section>

  <section>
    <h2>AI核心暴露技术逻辑</h2>
    <p><b>基金识别：</b>用基金代码对应的基金名称、跟踪指数、主题标签、标准分类字典和调仓/持仓披露名称拼接成证据文本；命中核心关键词才计入AI核心。</p>
    <p><b>关键词分层：</b>直接AI=人工智能/AI/AIGC/机器学习/机器人；算力软件=算力/云计算/大数据/数据中心/信创/软件开发/信息技术；硬件=半导体/芯片/集成电路；基础设施=通信设备/5G/光模块。仅“科技/TMT/电子/传媒/互联网/恒生科技/纳指/创新”不计入AI核心。</p>
    <p><b>暴露计算：</b>每个调仓后快照内，AI核心暴露=sum(AI核心基金调后权重)。最近一年均值按快照之间的持续天数做时间加权；峰值取窗口起点、窗口内调仓后快照和当前持仓快照的最大值。</p>
  </section>

  <section>
    <h2>业绩走势</h2>
    <p><b>入选策略等权净值明显用于观察组合整体是否跟上AI主题参考线。</b> 图中所有序列归一到100；AI核心基金池参考线是本地基金净值合成，不是官方指数，适合作为内部对标。</p>
    {trend_img}
  </section>

  <section>
    <h2>策略点阵图</h2>
    <p><b>右侧越远代表一年内AI核心暴露越高，纵轴越高代表近1年收益越高。</b> 橙色为严格入选策略；虚线为50%阈值。</p>
    {scatter_img}
  </section>

  <section>
    <h2>入选策略AI核心暴露说明</h2>
    {html_table(rows_for_html, columns)}
  </section>

  <section>
    <h2>口径 caveats</h2>
    <ul>
      <li>若某策略调仓明细不是完整组合快照，历史峰值可能偏低；当前持仓快照会作为补充。</li>
      <li>AI核心基金池参考线使用基金净值等权合成，不能等同于官方“AI指数”。本地指数库当前仅找到中证TMT等宽科技指数。</li>
      <li>暴露均值为时间加权仓位暴露，不等于收益贡献。下一步如要归因，应把AI核心基金日收益按每日持仓权重回放贡献。</li>
    </ul>
  </section>
</main>
</body>
</html>"""
    (out_dir / "report.html").write_text(body, encoding="utf-8")


def main() -> None:
    out_dir = OUT_ROOT / today_stamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = read_summary()
    overview = summary.get("overview", {})
    end = parse_date(overview.get("数据更新至")) or date.today()
    start = end - timedelta(days=365)

    con = sqlite3.connect(str(DB_PATH))
    fund_map = build_fund_ai_map(con)
    strategies = strategy_rows(summary)

    current_exposure, current_funds = read_current_ai_exposure(fund_map)
    snapshots, evidence = event_exposures(con, fund_map, start, end)

    all_points: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    ai_core_codes = set()
    for sid, strategy in strategies.items():
        year_return = num(strategy.get("近1年"))
        if year_return is None:
            continue
        stats = time_weighted_stats(snapshots.get(sid, []), start, end)
        peak = max(stats["peak"], current_exposure.get(sid, 0.0))
        if current_exposure.get(sid, 0.0) >= peak:
            stats["peak_date"] = raw(strategy.get("最新持仓日") or end.isoformat())
        mean = stats["mean"]
        is_selected = mean >= 50 or peak >= 50

        evidence_funds = {}
        for item in evidence.get(sid, []):
            for fund in item.get("funds", []):
                key = raw(fund["code"]) or raw(fund["name"])
                if not key:
                    continue
                existing = evidence_funds.get(key)
                if not existing or fund["weight"] > existing["weight"]:
                    evidence_funds[key] = fund
        for fund in current_funds.get(sid, []):
            key = raw(fund["code"]) or raw(fund["name"])
            existing = evidence_funds.get(key)
            if not existing or fund["weight"] > existing["weight"]:
                evidence_funds[key] = fund
        top_funds = sorted(evidence_funds.values(), key=lambda x: x["weight"], reverse=True)[:6]
        for fund in top_funds:
            if raw(fund.get("code")):
                ai_core_codes.add(raw(fund.get("code")))

        point = {
            "统一策略ID": sid,
            "策略名称": raw(strategy.get("策略名称")),
            "投顾机构": raw(strategy.get("投顾机构")),
            "渠道": raw(strategy.get("渠道")),
            "风险等级": raw(strategy.get("风险等级")),
            "业务分类": raw(strategy.get("业务分类")),
            "研报产品类型": raw(strategy.get("研报产品类型")),
            "近1年收益": year_return,
            "近6月收益": num(strategy.get("近6月"), 0) or 0,
            "最大回撤": num(strategy.get("最大回撤"), 0) or 0,
            "夏普比率": num(strategy.get("夏普比率"), 0) or 0,
            "AI核心均值暴露": mean,
            "AI核心峰值暴露": peak,
            "当前AI核心暴露": current_exposure.get(sid, 0.0),
            "暴露覆盖天数": stats["days"],
            "暴露快照数": stats["points"],
            "首次AI核心暴露日期": stats["first_ai_date"],
            "峰值日期": stats["peak_date"],
            "主要AI核心基金": "；".join(f"{fund['name']}({fund['code']}) {fund['weight']:.2f}% [{fund.get('hits','')}]" for fund in top_funds),
            "是否入选": is_selected,
        }
        all_points.append(point)
        if is_selected:
            selected.append(point)

    selected.sort(key=lambda r: (r["AI核心均值暴露"], r["AI核心峰值暴露"], r["近1年收益"]), reverse=True)
    all_points.sort(key=lambda r: (r["是否入选"], r["AI核心均值暴露"], r["AI核心峰值暴露"]), reverse=True)

    selected_ids = [row["统一策略ID"] for row in selected]
    strategy_nav = load_strategy_nav(con, selected_ids, start, end)
    selected_index = make_equal_weight_index(strategy_nav)
    fund_index = load_fund_reference_index(con, sorted(ai_core_codes), start, end)
    tmt_index = load_tmt_index(con, start, end)

    trend_rows: list[dict[str, Any]] = []
    for series_name, series in [
        ("入选策略等权净值", selected_index),
        ("AI核心基金池等权参考", fund_index),
        ("中证TMT", tmt_index),
    ]:
        for d, value in series:
            trend_rows.append({"日期": d, "系列": series_name, "指数点位": value})

    chart_paths = build_charts(out_dir, selected, all_points, trend_rows)

    csv_rows = []
    for row in selected:
        csv_rows.append({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in row.items() if k != "是否入选"})
    write_csv(out_dir / "selected_ai_core_exposure_strategies.csv", csv_rows)
    write_csv(out_dir / "ai_core_exposure_scatter_points.csv", [{k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in row.items()} for row in all_points])
    write_csv(out_dir / "ai_core_reference_trend.csv", trend_rows)

    source_counts = {
        "all_candidate_points": len(all_points),
        "ai_core_funds": len(ai_core_codes),
        "trend_days": len({row["日期"] for row in trend_rows}),
    }
    write_report(out_dir, selected, chart_paths, start, end, source_counts)

    summary_out = {
        "output_dir": str(out_dir),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "selected_count": len(selected),
        "all_points": len(all_points),
        "ai_core_fund_count": len(ai_core_codes),
        "charts": chart_paths,
        "top_selected": selected[:10],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary_out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
