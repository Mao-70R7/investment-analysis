# -*- coding: utf-8 -*-
"""Generate Guangfa product operations and marketing insights from the mixed scatter pack."""

from __future__ import annotations

import argparse
import html
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
FORMAL_ROOT = PROJECT_ROOT / "site"
DEFAULT_SOURCE = FORMAL_ROOT / "basic_data" / "data" / "mixed_performance_scatter_pack.json"
DEFAULT_OUTPUT_DIR = FORMAL_ROOT / "reports" / "guangfa_product_ops_marketing_insights_20260630"
INTERVAL = "上半年"


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"none", "nan", "null", "--", "-"} else text


def esc(value: Any) -> str:
    return html.escape(clean(value))


def num(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def interval_metrics(row: dict[str, Any], interval: str = INTERVAL) -> dict[str, Any]:
    metrics = row.get("intervals", {}).get(interval, {})
    return metrics if isinstance(metrics, dict) else {}


def rate(row: dict[str, Any], field: str) -> float | None:
    return num(interval_metrics(row).get(field))


def fmt_pct(value: Any, digits: int = 2, *, signed: bool = False) -> str:
    number = num(value)
    if number is None:
        return "-"
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number * 100:.{digits}f}%"


def fmt_pp(value: Any, digits: int = 2, *, signed: bool = True) -> str:
    number = num(value)
    if number is None:
        return "-"
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number * 100:.{digits}f}pp"


def fmt_int(value: Any) -> str:
    number = num(value)
    if number is None:
        return "-"
    return f"{int(number):,}"


def pct_cell(value: Any, *, signed: bool = False) -> str:
    text = fmt_pct(value, signed=signed)
    cls = ""
    number = num(value)
    if number is not None:
        cls = " pos" if number > 0 else (" neg" if number < 0 else "")
    return f"<span class='pct{cls}'>{esc(text)}</span>"


def source_number(text: str) -> str:
    tip = "Source: local generated artifact; File: mixed_performance_scatter_pack.json; Dataset: rows filtered to benchmark-bucketed products with complete H1 return, drawdown, and volatility."
    return (
        f"<span class='source-tooltip' tabindex='0'>{esc(text)}"
        f"<span class='source-tooltip-content'>{esc(tip)}</span></span>"
    )


def product_link(row: dict[str, Any]) -> str:
    name = esc(row.get("name"))
    url = clean(row.get("detailUrl"))
    if not url:
        return name
    return f"<a href='{esc(url)}'>{name}</a>"


def has_h1_metrics(row: dict[str, Any]) -> bool:
    metrics = interval_metrics(row)
    return num(metrics.get("return")) is not None and num(metrics.get("maxDrawdown")) is not None and num(metrics.get("volatility")) is not None


def build_peer_context(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    usable: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not has_h1_metrics(row):
            continue
        pool = clean(row.get("formalPeerPool"))
        if not pool:
            continue
        item = dict(row)
        item["_index"] = index
        item["_ret"] = rate(item, "return")
        item["_dd"] = rate(item, "maxDrawdown")
        item["_vol"] = rate(item, "volatility")
        usable.append(item)

    by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in usable:
        by_pool[clean(row.get("formalPeerPool"))].append(row)

    pool_stats: dict[str, dict[str, Any]] = {}
    enriched: list[dict[str, Any]] = []
    for pool, part in by_pool.items():
        part = [row for row in part if row["_ret"] is not None]
        part.sort(key=lambda row: (-(row["_ret"] or -999), clean(row.get("name")), clean(row.get("id"))))
        if not part:
            continue
        returns = [row["_ret"] for row in part if row["_ret"] is not None]
        drawdowns = [row["_dd"] for row in part if row["_dd"] is not None]
        volatilities = [row["_vol"] for row in part if row["_vol"] is not None]
        n = len(part)
        ret_median = median(returns) if returns else None
        dd_median = median(drawdowns) if drawdowns else None
        vol_median = median(volatilities) if volatilities else None
        gf_count = sum(1 for row in part if row.get("isGuangfa"))
        bucket = clean(part[0].get("bucket"))
        track = clean(part[0].get("comparisonTrack"))
        for rank, row in enumerate(part, start=1):
            row["_peer_count"] = n
            row["_peer_rank"] = rank
            row["_top_quartile"] = rank <= max(1, math.ceil(n * 0.25))
            row["_above_median"] = ret_median is not None and (row["_ret"] or -999) >= ret_median
            row["_risk_better_than_median"] = dd_median is not None and row["_dd"] is not None and row["_dd"] >= dd_median
            row["_vol_better_than_median"] = vol_median is not None and row["_vol"] is not None and row["_vol"] <= vol_median
            row["_ret_gap"] = (row["_ret"] - ret_median) if ret_median is not None and row["_ret"] is not None else None
            row["_dd_gap"] = (row["_dd"] - dd_median) if dd_median is not None and row["_dd"] is not None else None
            row["_vol_gap"] = (vol_median - row["_vol"]) if vol_median is not None and row["_vol"] is not None else None
            enriched.append(row)
        pool_stats[pool] = {
            "pool": pool,
            "bucket": bucket,
            "track": track,
            "count": n,
            "gf_count": gf_count,
            "gf_share": gf_count / n if n else 0,
            "median_return": ret_median,
            "median_drawdown": dd_median,
            "median_volatility": vol_median,
            "market_top_return": part[0]["_ret"],
            "market_top_product": clean(part[0].get("name")),
        }
    return enriched, pool_stats


def compact_product(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": clean(row.get("name")),
        "code": clean(row.get("code") or row.get("id")),
        "productType": clean(row.get("productType")),
        "fundMainType": clean(row.get("fundMainType")),
        "institution": clean(row.get("institution")),
        "pool": clean(row.get("formalPeerPool")),
        "bucket": clean(row.get("bucket")),
        "track": clean(row.get("comparisonTrack")),
        "ret": row.get("_ret"),
        "dd": row.get("_dd"),
        "vol": row.get("_vol"),
        "rank": row.get("_peer_rank"),
        "peer_count": row.get("_peer_count"),
        "ret_gap": row.get("_ret_gap"),
        "dd_gap": row.get("_dd_gap"),
        "vol_gap": row.get("_vol_gap"),
        "detailUrl": clean(row.get("detailUrl")),
        "benchmark": clean(row.get("benchmark")),
    }


def action_label(row: dict[str, Any]) -> str:
    track = clean(row.get("track"))
    bucket = clean(row.get("bucket"))
    ptype = clean(row.get("productType"))
    if track in {"货币主导", "债券主导"} and bucket in {"L0", "L1", "L2"}:
        return "稳健货架主推，可承接低波动和现金管理需求"
    if track == "纯权益" or bucket in {"L8", "L9", "L10"}:
        return "权益进攻型素材，适合分档清晰的高风险客群"
    if track == "商品主导":
        return "卫星资产表达，营销需同步披露波动和场景边界"
    if ptype == "投顾策略":
        return "投顾组合案例，可结合调仓和持仓页做解释型营销"
    return "同类表现占优，可进入渠道重点观察名单"


def top_products(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if row.get("isGuangfa")
        and row.get("_peer_count", 0) >= 20
        and row.get("_top_quartile")
        and row.get("_risk_better_than_median")
        and (row.get("_ret") or 0) > 0
    ]
    candidates.sort(
        key=lambda row: (
            row["_peer_rank"],
            -(row.get("_ret_gap") or 0),
            row.get("_dd") or -999,
            clean(row.get("name")),
        )
    )
    output = [compact_product(row) for row in candidates[:limit]]
    for row in output:
        row["action"] = action_label(row)
    return output


def defensive_products(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if row.get("isGuangfa")
        and row.get("_peer_count", 0) >= 20
        and clean(row.get("comparisonTrack")) in {"债券主导", "货币主导"}
        and clean(row.get("bucket")) in {"L0", "L1", "L2"}
        and row.get("_above_median")
        and row.get("_risk_better_than_median")
    ]
    candidates.sort(key=lambda row: (row["_peer_rank"], -(row.get("_ret") or -999), clean(row.get("name"))))
    output = [compact_product(row) for row in candidates[:limit]]
    for row in output:
        row["action"] = "稳健和低波动人群优先，强调同池收益和回撤共同占优"
    return output


def watch_products(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if row.get("isGuangfa")
        and row.get("_peer_count", 0) >= 20
        and (
            (row.get("_peer_rank") or 0) > math.ceil(row.get("_peer_count", 0) * 0.75)
            or (not row.get("_above_median") and not row.get("_risk_better_than_median"))
        )
    ]
    candidates.sort(key=lambda row: (-(row.get("_peer_rank") or 0), row.get("_ret") or 999, clean(row.get("name"))))
    output = [compact_product(row) for row in candidates[:limit]]
    for row in output:
        row["action"] = "暂停泛化主推，先核对策略定位、基准和渠道话术"
    return output


def pool_opportunities(pool_stats: dict[str, dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    rows = [
        row
        for row in pool_stats.values()
        if row["count"] >= 50 and (row["gf_count"] <= 3 or row["gf_share"] < 0.02)
    ]
    rows.sort(key=lambda row: (-row["count"], row["gf_count"], row["pool"]))
    return rows[:limit]


def track_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("isGuangfa") and row.get("_peer_count", 0) >= 5:
            grouped[clean(row.get("comparisonTrack")) or "未形成"].append(row)
    output = []
    for track, part in grouped.items():
        output.append(
            {
                "track": track,
                "gf_count": len(part),
                "top_quartile_count": sum(1 for row in part if row.get("_top_quartile")),
                "above_median_count": sum(1 for row in part if row.get("_above_median")),
                "risk_control_count": sum(1 for row in part if row.get("_risk_better_than_median")),
                "median_return": median([row["_ret"] for row in part if row.get("_ret") is not None]),
            }
        )
    output.sort(key=lambda row: (-row["gf_count"], row["track"]))
    return output


def bucket_track_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("isGuangfa") and row.get("_peer_count", 0) >= 5:
            grouped[(clean(row.get("bucket")), clean(row.get("comparisonTrack")))].append(row)
    output = []
    for (bucket, track), part in grouped.items():
        top = sum(1 for row in part if row.get("_top_quartile"))
        risk = sum(1 for row in part if row.get("_risk_better_than_median"))
        output.append(
            {
                "bucket": bucket,
                "track": track,
                "gf_count": len(part),
                "top_rate": top / len(part) if part else None,
                "risk_rate": risk / len(part) if part else None,
                "median_return": median([row["_ret"] for row in part if row.get("_ret") is not None]),
            }
        )
    output.sort(key=lambda row: (-row["gf_count"], row["bucket"], row["track"]))
    return output


def horizontal_bar_svg(rows: list[dict[str, Any]], *, label_key: str, value_key: str, width: int = 860, height_per: int = 34) -> str:
    if not rows:
        return "<p class='empty'>暂无可绘制数据。</p>"
    height = max(120, 40 + height_per * len(rows))
    left = 170
    right = 110
    top = 20
    bar_h = 18
    max_value = max(float(row.get(value_key) or 0) for row in rows) or 1
    parts = [f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='bar chart'>"]
    for index, row in enumerate(rows):
        y = top + index * height_per
        value = float(row.get(value_key) or 0)
        bar_w = (width - left - right) * value / max_value
        label = clean(row.get(label_key))
        parts.append(f"<text x='8' y='{y + 14}' class='svg-label'>{esc(label)}</text>")
        parts.append(f"<rect x='{left}' y='{y}' width='{width - left - right}' height='{bar_h}' rx='5' class='svg-bg'></rect>")
        parts.append(f"<rect x='{left}' y='{y}' width='{bar_w:.1f}' height='{bar_h}' rx='5' class='svg-bar'></rect>")
        suffix = row.get("_label") or fmt_int(value)
        parts.append(f"<text x='{left + bar_w + 8:.1f}' y='{y + 14}' class='svg-value'>{esc(suffix)}</text>")
    parts.append("</svg>")
    return "".join(parts)


def table(rows: list[dict[str, Any]], columns: list[tuple[str, str, str]]) -> str:
    if not rows:
        return "<p class='empty'>暂无符合条件的产品。</p>"
    head = "".join(f"<th>{esc(label)}</th>" for _, label, _ in columns)
    body = []
    for row in rows:
        cells = []
        for key, _, kind in columns:
            value = row.get(key)
            if kind == "product":
                value = product_link(row)
            elif kind == "pct":
                value = pct_cell(value, signed=key in {"ret", "ret_gap", "dd_gap", "vol_gap"})
            elif kind == "rank":
                value = f"{fmt_int(row.get('rank'))}/{fmt_int(row.get('peer_count'))}"
            elif kind == "int":
                value = fmt_int(value)
            elif kind == "share":
                value = fmt_pct(value, 1)
            else:
                value = esc(value)
            cells.append(f"<td class='{esc(kind)}'>{value}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def build_report(source: Path, output_dir: Path) -> dict[str, Any]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    raw_rows = payload.get("rows") or []
    rows, pools = build_peer_context(raw_rows)
    gf_rows = [row for row in rows if row.get("isGuangfa") and row.get("_peer_count", 0) >= 5]
    gf_funds = [row for row in gf_rows if clean(row.get("productType")) == "公募基金"]
    gf_strategies = [row for row in gf_rows if clean(row.get("productType")) == "投顾策略"]
    track_rows = track_summary(rows)
    bucket_rows = bucket_track_summary(rows)
    top = top_products(rows, limit=16)
    defensive = defensive_products(rows, limit=12)
    watch = watch_products(rows, limit=16)
    gaps = pool_opportunities(pools, limit=12)

    source_meta = payload.get("meta") or {}
    h1_count = len(rows)
    gf_top_count = sum(1 for row in gf_rows if row.get("_top_quartile"))
    gf_risk_control_count = sum(1 for row in gf_rows if row.get("_risk_better_than_median"))
    gf_above_median_count = sum(1 for row in gf_rows if row.get("_above_median"))
    track_chart_rows = []
    for row in track_rows:
        top_rate = row["top_quartile_count"] / row["gf_count"] if row["gf_count"] else 0
        chart_row = dict(row)
        chart_row["top_rate"] = top_rate
        chart_row["_label"] = f"{fmt_pct(top_rate, 1)} / {fmt_int(row['gf_count'])}个"
        track_chart_rows.append(chart_row)
    gap_chart_rows = []
    for row in gaps[:8]:
        chart_row = dict(row)
        chart_row["_label"] = f"{fmt_int(row['count'])}个，广发{fmt_int(row['gf_count'])}个"
        gap_chart_rows.append(chart_row)

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    as_of = clean(source_meta.get("asOfDate")) or "2026-06-30"
    css = """
:root { color-scheme: light dark; --bg:#f7f8fb; --panel:#fff; --ink:#1f2937; --muted:#667085; --line:#dbe4ee; --red:#c62828; --green:#12805c; --blue:#285ca8; --soft:#f1f5f9; --warn:#9a5b00; }
@media (prefers-color-scheme: dark) { :root { --bg:#111827; --panel:#1f2937; --ink:#f3f4f6; --muted:#b7c0ce; --line:#374151; --soft:#263244; } }
* { box-sizing:border-box; }
body { margin:0; font-family:"Microsoft YaHei","Segoe UI",Arial,sans-serif; background:var(--bg); color:var(--ink); line-height:1.62; }
main { max-width:1180px; margin:0 auto; padding:30px 24px 56px; }
h1 { font-size:30px; line-height:1.2; margin:0 0 10px; letter-spacing:0; }
h2 { font-size:21px; line-height:1.35; margin:30px 0 12px; }
h3 { font-size:16px; margin:18px 0 8px; }
p { margin:8px 0 12px; }
a { color:inherit; text-decoration:underline; text-decoration-color:#b8c4d6; }
.meta { color:var(--muted); font-size:13px; }
.summary, .card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px 18px; margin:14px 0; }
.summary h2 { margin-top:0; }
.summary li { margin:8px 0; }
.kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:18px 0; }
.kpi { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:13px 14px; min-width:0; }
.kpi span { display:block; color:var(--muted); font-size:12px; }
.kpi strong { display:block; margin-top:5px; font-size:24px; line-height:1.25; overflow-wrap:anywhere; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start; }
.table-wrap { overflow:auto; border:1px solid var(--line); border-radius:8px; background:var(--panel); }
table { width:100%; border-collapse:collapse; min-width:900px; font-size:12px; }
th,td { padding:8px 9px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; white-space:nowrap; }
th { background:var(--soft); font-weight:800; }
td.product { min-width:220px; max-width:330px; white-space:normal; font-weight:700; }
td.text { max-width:300px; white-space:normal; }
.pct.pos { color:var(--green); font-weight:800; }
.pct.neg { color:var(--red); font-weight:800; }
.pill { display:inline-flex; align-items:center; border:1px solid var(--line); background:var(--soft); border-radius:999px; padding:3px 8px; color:var(--muted); font-size:12px; margin:2px 4px 2px 0; }
.svg-label { fill:var(--ink); font-size:12px; font-weight:700; }
.svg-value { fill:var(--muted); font-size:12px; }
.svg-bg { fill:var(--soft); }
.svg-bar { fill:#dc2626; }
.note { color:var(--muted); font-size:12px; }
.empty { color:var(--muted); }
.source-tooltip { position:relative; border-bottom:1px dotted currentColor; cursor:help; }
.source-tooltip-content { display:block; position:absolute; left:0; top:1.7em; min-width:260px; max-width:360px; z-index:30; background:#111827; color:#fff; border-radius:8px; padding:8px 10px; font-size:12px; line-height:1.45; box-shadow:0 12px 28px rgba(0,0,0,.22); visibility:hidden; opacity:0; pointer-events:none; transition:opacity .12s ease; }
.source-tooltip:hover > .source-tooltip-content, .source-tooltip:focus > .source-tooltip-content { visibility:visible; opacity:1; }
@media (max-width:900px) { main { padding:22px 14px 42px; } .kpis,.grid2 { grid-template-columns:1fr; } h1 { font-size:24px; } }
"""

    top_columns = [
        ("name", "产品", "product"),
        ("code", "代码", "text"),
        ("productType", "类型", "text"),
        ("pool", "正式可比池", "text"),
        ("rank", "同池排名", "rank"),
        ("ret", "上半年收益", "pct"),
        ("dd", "最大回撤", "pct"),
        ("vol", "年化波动", "pct"),
        ("ret_gap", "收益领先中位", "pct"),
        ("action", "运营动作", "text"),
    ]
    watch_columns = [
        ("name", "产品", "product"),
        ("code", "代码", "text"),
        ("productType", "类型", "text"),
        ("pool", "正式可比池", "text"),
        ("rank", "同池排名", "rank"),
        ("ret", "上半年收益", "pct"),
        ("dd", "最大回撤", "pct"),
        ("ret_gap", "收益低于中位", "pct"),
        ("action", "建议", "text"),
    ]
    gap_rows = [
        {
            "pool": row["pool"],
            "bucket": row["bucket"],
            "track": row["track"],
            "count": row["count"],
            "gf_count": row["gf_count"],
            "gf_share": row["gf_share"],
            "median_return": row["median_return"],
            "market_top_product": row["market_top_product"],
        }
        for row in gaps
    ]
    gap_columns = [
        ("pool", "正式可比池", "text"),
        ("count", "市场样本", "int"),
        ("gf_count", "广发样本", "int"),
        ("gf_share", "广发占比", "share"),
        ("median_return", "市场中位收益", "pct"),
        ("market_top_product", "市场头部产品", "text"),
    ]
    bucket_columns = [
        ("bucket", "L档", "text"),
        ("track", "轨道", "text"),
        ("gf_count", "广发样本", "int"),
        ("top_rate", "前25%占比", "share"),
        ("risk_rate", "回撤优于中位占比", "share"),
        ("median_return", "广发中位收益", "pct"),
    ]

    report_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <link rel="icon" href="data:,">
  <title>广发基金产品运营和营销洞察</title>
  <style>{css}</style>
</head>
<body>
<main>
  <header>
    <h1>广发基金产品运营和营销洞察</h1>
    <div class="meta">截至 {esc(as_of)}，生成时间 {esc(generated_at)}。口径：当前混排点阵数据，按正式可比池比较。</div>
  </header>

  <section class="summary" data-contract-section="executive-summary">
    <h2>Executive Summary</h2>
    <ul>
      <li><strong>可用于业务判断的样本已经足够大。</strong>当前页面数据包 {source_number(fmt_int(source_meta.get('includedRowCount')))} 条，上半年收益、回撤、波动完整且进入正式可比池的样本 {source_number(fmt_int(h1_count))} 条，其中广发产品 {source_number(fmt_int(len(gf_rows)))} 条。</li>
      <li><strong>广发产品不能只按 L档做营销结论。</strong>L档只表达权益权重，同一 L0 内仍有货币、债券、商品和另类产品；本报告只用“基准风险资产权重 + 非权益比较轨道”的正式可比池判断中位数、前25%和风险控制。</li>
      <li><strong>运营上应把产品分成三类动作。</strong>前25%且回撤优于同池中位的产品进入渠道重点名单；低权益债券/货币轨道里收益和回撤同时占优的产品用于稳健客群；收益或回撤双弱的产品先做定位和话术复核。</li>
      <li><strong>营销机会不等于全市场收益榜。</strong>广发当前同类前25%产品 {source_number(fmt_int(gf_top_count))} 个，回撤优于同池中位 {source_number(fmt_int(gf_risk_control_count))} 个；真正能做主推素材的是“同池排名靠前 + 风险不劣于同类”的交集。</li>
    </ul>
  </section>

  <section class="kpis" data-contract-section="key-findings">
    <div class="kpi"><span>广发正式可比产品</span><strong>{source_number(fmt_int(len(gf_rows)))}</strong></div>
    <div class="kpi"><span>广发公募基金</span><strong>{source_number(fmt_int(len(gf_funds)))}</strong></div>
    <div class="kpi"><span>广发投顾策略</span><strong>{source_number(fmt_int(len(gf_strategies)))}</strong></div>
    <div class="kpi"><span>收益不低于同池中位</span><strong>{source_number(fmt_int(gf_above_median_count))}</strong></div>
  </section>

  <section class="card">
    <h2>先看轨道，不要把 L0 当成低风险一类</h2>
    <p>下面的图按非权益比较轨道汇总广发产品在正式可比池内进入前25%的比例。它适合回答“哪个资产轨道更容易形成可营销素材”，不适合替代单产品审核。</p>
    {horizontal_bar_svg(track_chart_rows, label_key='track', value_key='top_rate')}
    <p class="note">每个产品先进入 L档，再按非权益资产主导轨道形成正式可比池。前25%按同一正式可比池的上半年收益排名计算。</p>
  </section>

  <section class="card">
    <h2>可直接进入渠道重点观察的产品</h2>
    <p>这些产品同时满足三个条件：广发产品、正式可比池样本不少于20个、上半年收益进入同池前25%，且最大回撤不差于同池中位。营销上可以优先做同类比较话术和详情页素材。</p>
    {table(top, top_columns)}
  </section>

  <section class="grid2">
    <section class="card">
      <h2>稳健客群的可用产品</h2>
      <p>低权益债券/货币轨道中，收益和回撤同时优于同池中位的产品，更适合承接低波动、现金替代、稳健配置等运营场景。</p>
      {table(defensive, top_columns)}
    </section>
    <section class="card">
      <h2>需要先治理再营销的产品</h2>
      <p>这些产品不是简单“不能卖”，而是当前同池收益或回撤表现不支持泛化主推。先核对基准、策略定位、渠道话术和是否存在清盘/转型/异常净值问题。</p>
      {table(watch, watch_columns)}
    </section>
  </section>

  <section class="card">
    <h2>高样本但广发露出不足的同类池</h2>
    <p>这些正式可比池市场样本较多，但广发样本少或占比低。业务含义是：如果公司已有产品，优先补渠道露出和内容解释；如果没有可承接产品，再评估是否需要产品线补位。</p>
    {horizontal_bar_svg(gap_chart_rows, label_key='pool', value_key='count')}
    {table(gap_rows, gap_columns)}
  </section>

  <section class="card">
    <h2>分档和轨道下的广发货架结构</h2>
    <p>运营排期建议按“L档 + 轨道”组织，而不是只按基金类型或只按权益比例。每个池内优先挑收益、回撤、波动三者都不弱的产品进入素材池。</p>
    {table(bucket_rows[:30], bucket_columns)}
  </section>

  <section class="card" data-contract-section="recommended-next-steps">
    <h2>建议的后续动作</h2>
    <ol>
      <li><strong>建立月度“同池可营销名单”。</strong>每月固定输出前25%且回撤不弱的广发产品，按 L档和轨道分发给渠道，不用全市场绝对收益榜替代。</li>
      <li><strong>给低权益轨道单独做稳健话术。</strong>L0 里货币、债券、商品必须拆开，稳健营销只使用 L0+货币主导或 L0/L1/L2+债券主导，不把商品主题放进低风险叙事。</li>
      <li><strong>对弱势产品做运营治理清单。</strong>收益后25%或回撤弱于同池中位的产品，先核对基准、净值、成立时间和清盘/转型状态，再决定是否下架、降权或改话术。</li>
      <li><strong>把详情页作为销售解释入口。</strong>点阵页点击产品名可进入基金或策略详情页，详情页应展示基准向量、净值区间、同池排名和回撤波动，用证据支撑渠道沟通。</li>
    </ol>
  </section>

  <section class="card" data-contract-section="caveats-and-assumptions">
    <h2>口径和限制</h2>
    <p>本报告只使用当前混排点阵数据包，不重新联网爬取。收益、回撤和波动使用截至 {esc(as_of)} 的上半年区间；未分档、未知权重、缺收益/回撤/波动的产品不参与正式比较。</p>
    <p>“同池前25%”是同一正式可比池内的收益排名，不代表适当性评级；营销使用前仍需结合产品风险等级、持有人画像、费率、规模、成立时间和合规材料。</p>
  </section>
</main>
</body>
</html>
"""
    report_path = output_dir / "report.html"
    summary_path = output_dir / "analysis_summary.json"
    report_path.write_text(report_html, encoding="utf-8")
    summary = {
        "source": str(source),
        "outputReport": str(report_path),
        "asOfDate": as_of,
        "generatedAt": generated_at,
        "pageIncludedRows": source_meta.get("includedRowCount"),
        "h1ComparableRows": h1_count,
        "guangfaComparableRows": len(gf_rows),
        "guangfaFundRows": len(gf_funds),
        "guangfaStrategyRows": len(gf_strategies),
        "guangfaTopQuartileRows": gf_top_count,
        "guangfaAboveMedianRows": gf_above_median_count,
        "guangfaRiskControlledRows": gf_risk_control_count,
        "trackSummary": track_rows,
        "bucketTrackSummary": bucket_rows,
        "topProducts": top,
        "defensiveProducts": defensive,
        "watchProducts": watch,
        "poolOpportunities": gap_rows,
        "sourceCounts": source_meta.get("counts", {}),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_report(args.source, args.output_dir)
    print(json.dumps({key: summary[key] for key in [
        "outputReport",
        "asOfDate",
        "h1ComparableRows",
        "guangfaComparableRows",
        "guangfaTopQuartileRows",
        "guangfaRiskControlledRows",
    ]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
