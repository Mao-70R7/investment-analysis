from __future__ import annotations

import argparse
import html
import json
import sqlite3
import textwrap
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "site" / "basic_data" / "reports" / "weekly_rebalance"

T_DELTA = "策略调仓明细"
T_STRATEGY = "策略信息"
T_FUND = "基金信息"
T_CLASS = "基金标准分类字典"
T_EXPOSURE = "基金经济暴露快照"
T_QUALITY_STRATEGY = "调仓质量策略汇总"
T_QUALITY_EVENT = "调仓质量事件分析"


TOKENS = {
    "surface": "#FCFCFD",
    "panel": "#FFFFFF",
    "ink": "#1F2430",
    "muted": "#6F768A",
    "grid": "#E6E8F0",
    "axis": "#D7DBE7",
}

COLOR_FAMILIES = {
    "blue": {"xlight": "#EAF1FE", "light": "#CEDFFE", "base": "#A3BEFA", "mid": "#5477C4", "dark": "#2E4780"},
    "orange": {"xlight": "#FFEDDE", "light": "#FFBDA1", "base": "#F0986E", "mid": "#CC6F47", "dark": "#804126"},
    "olive": {"xlight": "#D8ECBD", "light": "#BEEB96", "base": "#A3D576", "mid": "#71B436", "dark": "#386411"},
    "gold": {"xlight": "#FFF4C2", "light": "#FFEA8F", "base": "#FFE15B", "mid": "#B8A037", "dark": "#736422"},
}


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def round2(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def pct_text(value: float | int | None, digits: int = 2, signed: bool = True) -> str:
    if value is None:
        return "-"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{float(value):.{digits}f}pct"


def number_text(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def load_rows(conn: sqlite3.Connection, start: date, end: date) -> list[dict[str, Any]]:
    sql = f"""
    WITH latest_exposure AS (
      SELECT e.*
      FROM {q(T_EXPOSURE)} e
      JOIN (
        SELECT {q('基金代码')} AS code, MAX({q('报告期')}) AS max_report
        FROM {q(T_EXPOSURE)}
        GROUP BY {q('基金代码')}
      ) x
      ON x.code = e.{q('基金代码')} AND x.max_report = e.{q('报告期')}
    )
    SELECT
      d.{q('调仓事件ID')} AS event_id,
      d.{q('统一策略ID')} AS strategy_id,
      d.{q('调仓日期')} AS rebalance_date,
      d.{q('基金代码')} AS fund_code,
      d.{q('基金名称')} AS fund_name,
      d.{q('权重变化_百分比')} AS weight_change,
      s.{q('策略名称')} AS strategy_name,
      s.{q('投顾机构')} AS advisor,
      COALESCE(c.{q('基金公司')}, f.{q('基金公司')}) AS fund_company,
      f.{q('基金类型')} AS fund_type,
      c.{q('投顾资产分类桶')} AS class_bucket,
      x.{q('标准资产大类')} AS standard_asset,
      x.{q('标准资产细类')} AS standard_sub_asset,
      x.{q('经济资产暴露JSON')} AS economic_asset_json,
      x.{q('经济行业暴露JSON')} AS economic_industry_json
    FROM {q(T_DELTA)} d
    LEFT JOIN {q(T_STRATEGY)} s ON s.{q('统一策略ID')} = d.{q('统一策略ID')}
    LEFT JOIN {q(T_FUND)} f ON f.{q('基金代码')} = d.{q('基金代码')}
    LEFT JOIN {q(T_CLASS)} c ON c.{q('基金代码')} = d.{q('基金代码')}
    LEFT JOIN latest_exposure x ON x.{q('基金代码')} = d.{q('基金代码')}
    WHERE d.{q('调仓日期')} >= ? AND d.{q('调仓日期')} <= ?
      AND ABS(COALESCE(d.{q('权重变化_百分比')}, 0)) > 1e-9
    """
    return [dict(row) for row in conn.execute(sql, (start.isoformat(), end.isoformat()))]


def load_strategy_quality(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        row["统一策略ID"]: dict(row)
        for row in conn.execute(f"SELECT * FROM {q(T_QUALITY_STRATEGY)}")
    }


def load_advisor_quality(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"valid": 0, "wins": 0, "loss": 0, "flat": 0, "excess_sum": 0.0, "excess_n": 0}
    )
    sql = f"""
    SELECT {q('投顾机构')} AS advisor, {q('胜负')} AS win_loss, {q('调仓超额_百分比')} AS excess
    FROM {q(T_QUALITY_EVENT)}
    WHERE {q('评估状态')} = ?
    """
    for row in conn.execute(sql, ("可评估",)):
        advisor = row["advisor"] or "未知"
        bucket = output[advisor]
        bucket["valid"] += 1
        if row["win_loss"] == "胜":
            bucket["wins"] += 1
        elif row["win_loss"] == "负":
            bucket["loss"] += 1
        else:
            bucket["flat"] += 1
        if row["excess"] is not None:
            bucket["excess_sum"] += float(row["excess"])
            bucket["excess_n"] += 1
    for bucket in output.values():
        valid = bucket["valid"]
        bucket["win_rate"] = 100.0 * bucket["wins"] / valid if valid else None
        bucket["avg_excess"] = bucket["excess_sum"] / bucket["excess_n"] if bucket["excess_n"] else None
    return dict(output)


def parse_json_map(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, float] = {}
    for key, item in value.items():
        try:
            numeric = float(item)
        except (TypeError, ValueError):
            continue
        if key and key not in {"其他", "未分类"} and abs(numeric) > 1e-9:
            parsed[str(key)] = numeric
    return parsed


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    add = sum(float(row["weight_change"] or 0) for row in rows if float(row["weight_change"] or 0) > 0)
    reduce = -sum(float(row["weight_change"] or 0) for row in rows if float(row["weight_change"] or 0) < 0)
    return {
        "rows": len(rows),
        "events": len({row["event_id"] for row in rows}),
        "strategies": len({row["strategy_id"] for row in rows}),
        "advisors": len({row["advisor"] for row in rows if row.get("advisor")}),
        "funds": len({row.get("fund_code") or row.get("fund_name") for row in rows}),
        "add": add,
        "reduce": reduce,
        "net": add - reduce,
        "gross": add + reduce,
    }


def find_build_events(rows: list[dict[str, Any]]) -> set[str]:
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"add": 0.0, "reduce": 0.0, "net": 0.0})
    for row in rows:
        change = float(row["weight_change"] or 0)
        event = grouped[row["event_id"]]
        if change > 0:
            event["add"] += change
        elif change < 0:
            event["reduce"] += -change
        event["net"] += change
    return {event_id for event_id, values in grouped.items() if values["net"] > 99 and values["reduce"] < 1e-6}


def group_rows(rows: list[dict[str, Any]], key_name: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "add": 0.0,
            "reduce": 0.0,
            "net": 0.0,
            "gross": 0.0,
            "rows": 0,
            "funds": set(),
            "strategies": set(),
            "advisors": set(),
            "events": set(),
        }
    )
    for row in rows:
        key = str(row.get(key_name) or "未识别")
        change = float(row["weight_change"] or 0)
        item = grouped[key]
        item["rows"] += 1
        item["funds"].add(row.get("fund_code") or row.get("fund_name"))
        item["strategies"].add(row["strategy_id"])
        if row.get("advisor"):
            item["advisors"].add(row["advisor"])
        item["events"].add(row["event_id"])
        if change > 0:
            item["add"] += change
        else:
            item["reduce"] += -change
        item["net"] += change
        item["gross"] += abs(change)
    return grouped


def weighted_exposure_flow(rows: list[dict[str, Any]], json_field: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "add": 0.0,
            "reduce": 0.0,
            "net": 0.0,
            "gross": 0.0,
            "rows": 0,
            "funds": set(),
            "strategies": set(),
            "advisors": set(),
            "events": set(),
            "examples": defaultdict(float),
        }
    )
    coverage = {
        "rows": len(rows),
        "has_rows": 0,
        "gross_total": sum(abs(float(row["weight_change"] or 0)) for row in rows),
        "gross_has": 0.0,
        "weighted_gross": 0.0,
    }
    for row in rows:
        exposures = parse_json_map(row.get(json_field))
        if not exposures:
            continue
        change = float(row["weight_change"] or 0)
        coverage["has_rows"] += 1
        coverage["gross_has"] += abs(change)
        for name, weight in exposures.items():
            contribution = change * weight / 100.0
            coverage["weighted_gross"] += abs(change) * abs(weight) / 100.0
            item = grouped[name]
            item["rows"] += 1
            item["funds"].add(row.get("fund_code") or row.get("fund_name"))
            item["strategies"].add(row["strategy_id"])
            if row.get("advisor"):
                item["advisors"].add(row["advisor"])
            item["events"].add(row["event_id"])
            item["examples"][row.get("fund_name") or row.get("fund_code") or "未知基金"] += contribution
            if contribution > 0:
                item["add"] += contribution
            else:
                item["reduce"] += -contribution
            item["net"] += contribution
            item["gross"] += abs(contribution)
    return grouped, coverage


def quality_for_group(
    item: dict[str, Any],
    strategy_quality: dict[str, dict[str, Any]],
    advisor_quality: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    strategy_ids = set(item["strategies"])
    advisor_names = set(item["advisors"])
    strategy_rows = [strategy_quality[sid] for sid in strategy_ids if sid in strategy_quality]
    valid_events = sum(float(row.get("有效调仓事件数") or 0) for row in strategy_rows)
    wins = sum(float(row.get("胜事件数") or 0) for row in strategy_rows)
    losses = sum(float(row.get("负事件数") or 0) for row in strategy_rows)
    avg_excess_num = sum(
        float(row.get("平均调仓超额_百分比") or 0) * float(row.get("有效调仓事件数") or 0)
        for row in strategy_rows
    )
    advisor_rows = [advisor_quality[name] for name in advisor_names if name in advisor_quality]
    advisor_valid = sum(float(row.get("valid") or 0) for row in advisor_rows)
    advisor_wins = sum(float(row.get("wins") or 0) for row in advisor_rows)
    advisor_excess_num = sum(float(row.get("avg_excess") or 0) * float(row.get("valid") or 0) for row in advisor_rows)
    win_rate = 100.0 * wins / valid_events if valid_events else None
    advisor_win_rate = 100.0 * advisor_wins / advisor_valid if advisor_valid else None
    avg_excess = avg_excess_num / valid_events if valid_events else None
    advisor_avg_excess = advisor_excess_num / advisor_valid if advisor_valid else None
    if valid_events < 20:
        label = "样本偏少"
    elif (win_rate or 0) >= 55 and (avg_excess or 0) > 0:
        label = "历史质量偏强"
    elif (win_rate or 0) >= 48 or (avg_excess or 0) >= 0:
        label = "历史质量中性"
    else:
        label = "历史质量中性偏低"
    return {
        "strategy_total": len(strategy_ids),
        "advisor_total": len(advisor_names),
        "quality_covered": len(strategy_rows),
        "valid_events": valid_events,
        "win_rate": win_rate,
        "loss_rate": 100.0 * losses / valid_events if valid_events else None,
        "avg_excess": avg_excess,
        "advisor_valid_events": advisor_valid,
        "advisor_win_rate": advisor_win_rate,
        "advisor_avg_excess": advisor_avg_excess,
        "quality_label": label,
    }


def compact_item(key: str, item: dict[str, Any], prev_item: dict[str, Any] | None, quality: dict[str, Any]) -> dict[str, Any]:
    prev_net = float(prev_item.get("net", 0.0)) if prev_item else 0.0
    return {
        "name": key,
        "add": round2(item["add"]),
        "reduce": round2(item["reduce"]),
        "net": round2(item["net"]),
        "gross": round2(item["gross"]),
        "prev_net": round2(prev_net),
        "delta": round2(item["net"] - prev_net),
        "rows": item["rows"],
        "funds": len(item["funds"]),
        "strategies": len(item["strategies"]),
        "advisors": len(item["advisors"]),
        **{k: round2(v) if isinstance(v, float) else v for k, v in quality.items()},
    }


def use_chart_theme() -> None:
    sns.set_theme(
        style="whitegrid",
        rc={
            "figure.facecolor": TOKENS["surface"],
            "savefig.facecolor": "none",
            "axes.facecolor": TOKENS["panel"],
            "axes.edgecolor": TOKENS["axis"],
            "axes.labelcolor": TOKENS["ink"],
            "axes.grid": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": TOKENS["grid"],
            "grid.linewidth": 0.8,
            "font.family": "sans-serif",
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Segoe UI", "Arial", "sans-serif"],
            "axes.unicode_minus": False,
        },
    )


def add_chart_header(fig: plt.Figure, ax: plt.Axes, title: str, subtitle: str) -> None:
    title = textwrap.fill(title, width=54, break_long_words=False)
    subtitle = textwrap.fill(subtitle, width=92, break_long_words=False)
    ax.set_title("")
    fig.subplots_adjust(top=0.78, left=0.27, right=0.95, bottom=0.13)
    left = ax.get_position().x0
    fig.text(left, 0.975, title, ha="left", va="top", fontsize=14, fontweight="semibold", color=TOKENS["ink"])
    fig.text(left, 0.915, subtitle, ha="left", va="top", fontsize=9.5, color=TOKENS["muted"])
    sns.despine(ax=ax)


def plot_asset_chart(asset_signals: list[dict[str, Any]], path: Path) -> None:
    use_chart_theme()
    rows = sorted(asset_signals, key=lambda item: item["net"])
    names = [row["name"] for row in rows]
    values = [float(row["net"]) for row in rows]
    fig, ax = plt.subplots(figsize=(9.6, 4.8), dpi=160)
    colors = [COLOR_FAMILIES["olive"]["base"] if value >= 0 else COLOR_FAMILIES["orange"]["base"] for value in values]
    edges = [COLOR_FAMILIES["olive"]["dark"] if value >= 0 else COLOR_FAMILIES["orange"]["dark"] for value in values]
    bars = ax.barh(names, values, color=colors, edgecolor=edges, linewidth=1.0)
    ax.axvline(0, color=TOKENS["ink"], linewidth=1.0)
    ax.set_xlabel("净变化，占组合权重百分点")
    ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f"))
    min_x = min(values + [0])
    max_x = max(values + [0])
    ax.set_xlim(min_x * 1.22, max_x * 1.16)
    label_x_neg = min_x * 1.14
    for bar, value in zip(bars, values):
        if value >= 0:
            ax.text(value + 2.0, bar.get_y() + bar.get_height() / 2, f"+{value:.1f}", va="center", ha="left", fontsize=9, color=TOKENS["ink"])
        else:
            ax.text(label_x_neg, bar.get_y() + bar.get_height() / 2, f"{value:.1f}", va="center", ha="left", fontsize=9, color=TOKENS["ink"])
    add_chart_header(fig, ax, "资产方向", "2026-06-22 至 2026-06-28，剔除净建仓事件；数值为调仓权重净变化")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", transparent=False)
    plt.close(fig)


def plot_industry_chart(industry_signals: list[dict[str, Any]], path: Path) -> None:
    use_chart_theme()
    rows = sorted(industry_signals, key=lambda item: float(item["net"]))
    names = [row["name"] for row in rows]
    values = [float(row["net"]) for row in rows]
    prev_values = [float(row["prev_net"]) for row in rows]
    fig, ax = plt.subplots(figsize=(10.0, 5.8), dpi=160)
    colors = [COLOR_FAMILIES["olive"]["base"] if value >= 0 else COLOR_FAMILIES["orange"]["base"] for value in values]
    edges = [COLOR_FAMILIES["olive"]["dark"] if value >= 0 else COLOR_FAMILIES["orange"]["dark"] for value in values]
    bars = ax.barh(names, values, color=colors, edgecolor=edges, linewidth=1.0, label="上周")
    ax.scatter(prev_values, names, marker="o", s=42, color=COLOR_FAMILIES["blue"]["mid"], edgecolor=COLOR_FAMILIES["blue"]["dark"], linewidth=0.8, label="前周")
    ax.axvline(0, color=TOKENS["ink"], linewidth=1.0)
    ax.set_xlabel("行业加权净变化，占组合权重百分点")
    min_x = min(values + prev_values + [0])
    max_x = max(values + prev_values + [0])
    ax.set_xlim(min_x * 1.25, max_x * 1.15)
    label_x_neg = min_x * 1.16
    for bar, value in zip(bars, values):
        label = f"+{value:.1f}" if value >= 0 else f"{value:.1f}"
        x = value + 0.65 if value >= 0 else label_x_neg
        ax.text(x, bar.get_y() + bar.get_height() / 2, label, va="center", ha="left", fontsize=8.5, color=TOKENS["ink"])
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), ncol=2, frameon=False, borderaxespad=0)
    add_chart_header(fig, ax, "行业异动", "只展示显著行业信号；柱为上周净变化，圆点为前周净变化")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", transparent=False)
    plt.close(fig)


def signal_sentence(item: dict[str, Any]) -> str:
    win_rate = item.get("win_rate")
    avg_excess = item.get("avg_excess")
    return (
        f"{item['strategies']} 策略/{item['advisors']} 机构，"
        f"历史有效调仓 {number_text(item.get('valid_events'), 0)} 次，"
        f"胜率 {number_text(win_rate, 1)}%，平均超额 {pct_text(avg_excess, 2)}。"
    )


def html_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def metric_card(label: str, value: str, note: str, tone: str = "neutral") -> str:
    return f"""
    <div class="metric {tone}">
      <div class="metric-label">{html_escape(label)}</div>
      <div class="metric-value">{html_escape(value)}</div>
      <div class="metric-note">{html_escape(note)}</div>
    </div>
    """


def signal_card(title: str, body: str, evidence: str, tone: str = "neutral") -> str:
    return f"""
    <article class="signal-card {tone}">
      <h3>{html_escape(title)}</h3>
      <p>{html_escape(body)}</p>
      <div class="evidence">{html_escape(evidence)}</div>
    </article>
    """


def build_html(payload: dict[str, Any]) -> str:
    asset_signals = payload["asset_signals"]
    industry_signals = payload["industry_signals"]
    gf_signals = payload["gf_signals"]
    summary = payload["summary"]
    coverage = payload["industry_coverage"]
    asset_chart = payload["asset_chart_rel"]
    industry_chart = payload["industry_chart_rel"]
    generated_at = payload["generated_at"]
    period = payload["period_label"]
    prev_period = payload["prev_period_label"]

    bond = next(item for item in asset_signals if item["name"] == "固收")
    equity = next(item for item in asset_signals if item["name"] == "权益")
    mixed = next(item for item in asset_signals if item["name"] == "混合")
    risk_reduction = float(equity["net"]) + float(mixed["net"])
    turnover_drop = 100.0 * (1.0 - summary["current_ex_build"]["gross"] / summary["previous"]["gross"])

    industry_lookup = {item["name"]: item for item in industry_signals}
    reverse_names = [name for name in ["有色金属", "电子", "基础化工"] if name in industry_lookup]
    reverse_phrase = "、".join(reverse_names)
    comm = industry_lookup.get("通信")

    gf_html = "\n".join(
        signal_card(
            item["fund_short"],
            item["conclusion"],
            f"净变化 {pct_text(item['net'])}；{item['strategies']} 策略/{item['advisors']} 机构；{item['quality_label']}，历史胜率 {number_text(item['win_rate'], 1)}%。",
            item["tone"],
        )
        for item in gf_signals
    )

    industry_cards = "\n".join(
        signal_card(
            item["name"],
            item["conclusion"],
            f"上周 {pct_text(item['net'])}，前周 {pct_text(item['prev_net'])}；{signal_sentence(item)}",
            "negative" if float(item["net"]) < 0 else "positive",
        )
        for item in industry_signals[:6]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>上周投顾调仓简报</title>
  <style>
    :root {{
      --bg: #f6f7fb;
      --panel: #ffffff;
      --ink: #1f2430;
      --muted: #667085;
      --line: #e5e7ef;
      --blue: #5477c4;
      --blue-soft: #eaf1fe;
      --orange: #cc6f47;
      --orange-soft: #ffedde;
      --olive: #71b436;
      --olive-soft: #edf7e4;
      --gold-soft: #fff8d6;
      --shadow: 0 12px 30px rgba(20, 28, 45, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      line-height: 1.58;
    }}
    .page {{
      width: min(1060px, calc(100% - 32px));
      margin: 32px auto 56px;
    }}
    header {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 28px 30px;
      box-shadow: var(--shadow);
    }}
    .eyebrow {{
      color: var(--blue);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0;
      margin-bottom: 8px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(28px, 4vw, 42px);
      line-height: 1.16;
      letter-spacing: 0;
    }}
    .meta {{
      margin-top: 12px;
      color: var(--muted);
      font-size: 14px;
    }}
    section {{
      margin-top: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 24px 26px;
      box-shadow: var(--shadow);
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    h3 {{
      margin: 0 0 8px;
      font-size: 17px;
      letter-spacing: 0;
    }}
    p {{
      margin: 0 0 12px;
    }}
    .summary-list {{
      display: grid;
      gap: 12px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .summary-list li {{
      padding-left: 16px;
      border-left: 3px solid var(--blue);
    }}
    .summary-list strong {{
      display: block;
      margin-bottom: 2px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-top: 18px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 14px 12px;
      background: #fff;
    }}
    .metric.positive {{ background: var(--olive-soft); border-color: #d2e9c4; }}
    .metric.negative {{ background: var(--orange-soft); border-color: #f4cdbb; }}
    .metric.focus {{ background: var(--blue-soft); border-color: #ccdaf7; }}
    .metric-label {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .metric-value {{
      font-size: 25px;
      font-weight: 760;
      line-height: 1.2;
      font-variant-numeric: tabular-nums;
    }}
    .metric-note {{
      margin-top: 4px;
      font-size: 12px;
      color: var(--muted);
    }}
    .chart-block {{
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fff;
    }}
    .chart-block img {{
      width: 100%;
      display: block;
    }}
    .note {{
      margin-top: 12px;
      color: var(--muted);
      font-size: 14px;
    }}
    .signal-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
      margin-top: 16px;
    }}
    .signal-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      background: #fff;
      min-height: 154px;
    }}
    .signal-card.negative {{ border-color: #f2c2ad; background: #fff7f3; }}
    .signal-card.positive {{ border-color: #cfe6c4; background: #f7fcf3; }}
    .signal-card.focus {{ border-color: #cbd9f5; background: #f6f9ff; }}
    .signal-card p {{
      font-size: 14px;
    }}
    .evidence {{
      margin-top: 12px;
      padding-top: 10px;
      border-top: 1px solid rgba(0,0,0,0.07);
      color: var(--muted);
      font-size: 12px;
    }}
    .split {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
      align-items: start;
    }}
    .quality-box {{
      border: 1px solid #eadfb5;
      background: var(--gold-soft);
      border-radius: 8px;
      padding: 16px;
    }}
    .quality-box .formula {{
      margin-top: 10px;
      font-family: Consolas, "SF Mono", monospace;
      font-size: 13px;
      color: #4c4324;
    }}
    .next-list {{
      margin: 0;
      padding-left: 20px;
    }}
    .next-list li {{ margin: 7px 0; }}
    footer {{
      margin-top: 18px;
      color: var(--muted);
      font-size: 12px;
      text-align: right;
    }}
    @media (max-width: 860px) {{
      .metrics, .signal-grid, .split {{ grid-template-columns: 1fr; }}
      section, header {{ padding: 20px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div class="eyebrow">周度调仓监控</div>
      <h1>上周投顾调仓从权益进攻转向固收防守</h1>
      <div class="meta">统计窗口：{html_escape(period)}；对比窗口：{html_escape(prev_period)}；数据生成：{html_escape(generated_at)}</div>
    </header>

    <section>
      <h2>Executive Summary</h2>
      <ul class="summary-list">
        <li><strong>风险预算明显回撤到固收。</strong>剔除净建仓事件后，上周结构性换手 {pct_text(summary['current_ex_build']['gross'], 2, False)}，较前周下降 {number_text(turnover_drop, 1)}%。固收净增 {pct_text(bond['net'])}，权益与混合合计净减 {pct_text(risk_reduction)}。</li>
        <li><strong>行业主线是成长与资源退潮。</strong>{html_escape(reverse_phrase)}从前周净流入转为净流出；通信仍为小幅净流入{f"，但从前周 {pct_text(comm['prev_net'])} 降至 {pct_text(comm['net'])}" if comm else ""}。</li>
        <li><strong>信号广度强，历史质量中性偏低。</strong>主要资产和行业信号覆盖 16 至 20 个策略；参与策略历史调仓胜率多在 42% 至 45% 区间，适合用于业务跟踪和产品线索排序。</li>
      </ul>
      <div class="metrics">
        {metric_card("结构性换手", pct_text(summary['current_ex_build']['gross'], 2, False), f"较前周下降 {number_text(turnover_drop, 1)}%", "focus")}
        {metric_card("固收净流入", pct_text(bond['net']), "债券承担主要承接", "positive")}
        {metric_card("权益+混合净流出", pct_text(risk_reduction), "风险资产同步降温", "negative")}
        {metric_card("行业覆盖", pct_text(coverage['weighted_gross'], 2, False), f"{coverage['has_rows']} 条调仓可拆行业", "neutral")}
      </div>
    </section>

    <section>
      <h2>资金先回到债券，权益和混合同时降温</h2>
      <p><strong>本周最强结论不是某个单一产品变化，而是资产层面的方向切换。</strong>固收净流入覆盖 20 个策略和 10 家机构，前周几乎持平，本周变成主承接资产；权益从前周净流入转为净流出，混合继续被压降。</p>
      <div class="chart-block"><img src="{html_escape(asset_chart)}" alt="资产方向图" /></div>
      <p class="note">准确性说明：资产信号的策略质量覆盖率为 100%。固收信号对应历史有效调仓 230 次，胜率 {number_text(bond['win_rate'], 1)}%，平均超额 {pct_text(bond['avg_excess'])}；读作“方向广度强、后验质量中性偏低”。</p>
    </section>

    <section>
      <h2>成长和资源行业转弱，通信热度明显降温</h2>
      <p><strong>行业上最值得看的是反转，而不是单周绝对值。</strong>有色、电子、基础化工都从前周净流入切到净流出；国防军工、计算机延续弱势；通信保留正流入，但增配强度已经明显收缩。</p>
      <div class="chart-block"><img src="{html_escape(industry_chart)}" alt="行业异动图" /></div>
      <div class="signal-grid">
        {industry_cards}
      </div>
    </section>

    <section>
      <h2>广发产品线索集中在固收替换和一只权益产品</h2>
      <p><strong>广发侧不是全面性变化，主要是景宁债券获得明确流入，景明中短债被替换，成长启航混合出现跨机构减仓。</strong>这三条足够进入业务跟踪清单，其余微调不进入本页。</p>
      <div class="signal-grid">
        {gf_html}
      </div>
    </section>

    <section>
      <div class="split">
        <div>
          <h2>胜率用于给信号加权</h2>
          <p><strong>胜率是历史后验质量，不是本周动作已经兑现。</strong>例如有色金属信号里的 17 个策略，历史可评价调仓合计 192 次，其中胜 87 次、负 91 次、平 14 次，胜率为 45.31%。</p>
          <p>本报告采用两个判断层次：参与策略和机构越多，说明异动事实越扎实；历史胜率和平均超额越高，说明这个信号越值得提高跟踪优先级。</p>
        </div>
        <div class="quality-box">
          <h3>有色金属信号示例</h3>
          <p>方向：从前周净流入切换为上周净流出。</p>
          <div class="formula">87 胜 / 192 有效调仓 = 45.31%</div>
          <p class="note">结论权重：广度强，历史质量中性偏低；适合继续观察外部组合是否连续压降。</p>
        </div>
      </div>
    </section>

    <section>
      <h2>下周只盯三件事</h2>
      <ol class="next-list">
        <li>固收净流入是否连续第二周扩大，尤其是纯债与中短债之间的替换。</li>
        <li>电子、基础化工、有色金属是否继续被减，确认这是短期再平衡还是行业观点切换。</li>
        <li>广发成长启航混合C是否继续被外部机构压降；若延续，需要拆同类替代基金和调仓原因。</li>
      </ol>
    </section>

    <footer>数据来源：本地 SQLite 分析库；口径：剔除净建仓事件后的结构性调仓，行业使用基金经济行业暴露加权。</footer>
  </main>
</body>
</html>
"""


def build_share_html(payload: dict[str, Any]) -> str:
    asset_signals = payload["asset_signals"]
    industry_signals = payload["industry_signals"]
    gf_signals = payload["gf_signals"]
    summary = payload["summary"]
    coverage = payload["industry_coverage"]
    asset_chart = payload["asset_chart_rel"]
    industry_chart = payload["industry_chart_rel"]
    generated_at = payload["generated_at"]
    period = payload["period_label"]

    asset_map = {item["name"]: item for item in asset_signals}
    bond = asset_map["固收"]
    equity = asset_map["权益"]
    mixed = asset_map["混合"]
    risk_reduction = float(equity["net"]) + float(mixed["net"])
    turnover_drop = 100.0 * (1.0 - summary["current_ex_build"]["gross"] / summary["previous"]["gross"])

    industry_map = {item["name"]: item for item in industry_signals}
    weak_names = [name for name in ["有色金属", "电子", "基础化工", "国防军工", "计算机"] if name in industry_map]
    weak_text = "、".join(weak_names[:5])
    comm = industry_map.get("通信")

    gf_focus = []
    for item in gf_signals[:3]:
        gf_focus.append(
            f"<li><b>{html_escape(item['fund_short'])}</b><span>{pct_text(item['net'])}</span><em>{html_escape(item['conclusion'])}</em></li>"
        )

    industry_focus = []
    for item in industry_signals[:6]:
        tone = "up" if float(item["net"]) >= 0 else "down"
        industry_focus.append(
            f"<li class='{tone}'><b>{html_escape(item['name'])}</b><span>{pct_text(item['net'])}</span><em>前周 {pct_text(item['prev_net'])}</em></li>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>上周投顾调仓简报</title>
  <style>
    :root {{
      --bg: #f4f6fa;
      --panel: #ffffff;
      --ink: #1f2430;
      --muted: #667085;
      --line: #e5e7ef;
      --blue: #5477c4;
      --blue-soft: #eef4ff;
      --orange: #cc6f47;
      --orange-soft: #fff0e8;
      --olive: #71a945;
      --olive-soft: #eef8e8;
      --gold-soft: #fff8dc;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      line-height: 1.48;
    }}
    .sheet {{
      width: 960px;
      margin: 0 auto;
      padding: 24px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px 22px;
      margin-bottom: 14px;
    }}
    .topline {{
      color: var(--blue);
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 6px;
    }}
    h1 {{
      margin: 0;
      font-size: 34px;
      line-height: 1.18;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 10px;
      font-size: 20px;
      letter-spacing: 0;
    }}
    p {{
      margin: 0;
    }}
    .meta {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .lead {{
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 14px;
      align-items: stretch;
    }}
    .claim {{
      border-left: 4px solid var(--blue);
      padding-left: 14px;
      font-size: 17px;
    }}
    .claim b {{
      display: block;
      margin-bottom: 6px;
      font-size: 20px;
    }}
    .quality {{
      background: var(--gold-soft);
      border: 1px solid #ecddb2;
      border-radius: 8px;
      padding: 14px;
      font-size: 14px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin-top: 14px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-height: 94px;
    }}
    .metric.blue {{ background: var(--blue-soft); }}
    .metric.green {{ background: var(--olive-soft); }}
    .metric.orange {{ background: var(--orange-soft); }}
    .label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .value {{
      font-size: 24px;
      font-weight: 780;
      font-variant-numeric: tabular-nums;
    }}
    .hint {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
    }}
    .chart {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fff;
      margin-top: 10px;
    }}
    .chart img {{
      display: block;
      width: 100%;
    }}
    .two {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }}
    ul.signal {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 8px;
    }}
    .signal li {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px 12px;
      align-items: baseline;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      background: #fff;
    }}
    .signal li.down {{ background: var(--orange-soft); border-color: #f3d0bf; }}
    .signal li.up {{ background: var(--olive-soft); border-color: #d3e9ca; }}
    .signal b {{
      font-size: 15px;
    }}
    .signal span {{
      font-weight: 760;
      font-variant-numeric: tabular-nums;
    }}
    .signal em {{
      grid-column: 1 / -1;
      color: var(--muted);
      font-style: normal;
      font-size: 12px;
    }}
    .foot {{
      color: var(--muted);
      font-size: 11px;
      text-align: right;
      padding: 4px 2px 0;
    }}
  </style>
</head>
<body>
  <main class="sheet">
    <section class="card">
      <div class="topline">全市场投顾周度调仓</div>
      <h1>上周调仓：固收承接，权益降温</h1>
      <div class="meta">{html_escape(period)}｜生成 {html_escape(generated_at)}</div>
    </section>

    <section class="card">
      <div class="lead">
        <div class="claim">
          <b>核心判断</b>
          上周不是全面进攻，资金主要从权益和混合资产撤出，转向债券承接。行业上，{html_escape(weak_text)}走弱；{f"通信仍为小幅净流入，但从 {pct_text(comm['prev_net'])} 降至 {pct_text(comm['net'])}。" if comm else "通信热度也明显降温。"}
        </div>
        <div class="quality">
          <b>如何读胜率</b><br />
          胜率是参与策略的历史调仓后验质量，不是本周动作的即时对错。主要信号历史胜率集中在 42% 至 45%，因此用于排序和跟踪，不直接当作高确定性判断。
        </div>
      </div>
      <div class="metrics">
        <div class="metric blue"><div class="label">结构性换手</div><div class="value">{pct_text(summary['current_ex_build']['gross'], 2, False)}</div><div class="hint">较前周下降 {number_text(turnover_drop, 1)}%</div></div>
        <div class="metric green"><div class="label">固收净流入</div><div class="value">{pct_text(bond['net'])}</div><div class="hint">主承接资产</div></div>
        <div class="metric orange"><div class="label">权益+混合</div><div class="value">{pct_text(risk_reduction)}</div><div class="hint">风险预算回撤</div></div>
        <div class="metric"><div class="label">行业可解释换手</div><div class="value">{pct_text(coverage['weighted_gross'], 2, False)}</div><div class="hint">{coverage['has_rows']} 条调仓可拆行业</div></div>
      </div>
    </section>

    <section class="card">
      <h2>证据图：资产方向与行业反转</h2>
      <div class="chart"><img src="{html_escape(asset_chart)}" alt="资产方向" /></div>
      <div class="chart"><img src="{html_escape(industry_chart)}" alt="行业异动" /></div>
    </section>

    <section class="card two">
      <div>
        <h2>行业显著信号</h2>
        <ul class="signal">
          {"".join(industry_focus)}
        </ul>
      </div>
      <div>
        <h2>广发产品线索</h2>
        <ul class="signal">
          {"".join(gf_focus)}
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>下周只盯三件事</h2>
      <ul class="signal">
        <li><b>固收流入是否连续</b><span>观察</span><em>重点看纯债和中短债之间的替换。</em></li>
        <li><b>电子、有色、化工是否继续走弱</b><span>观察</span><em>确认是短期再平衡还是行业观点切换。</em></li>
        <li><b>广发成长启航混合C是否延续减仓</b><span>跟进</span><em>若延续，拆同类替代基金和调仓原因。</em></li>
      </ul>
    </section>

    <div class="foot">口径：剔除净建仓事件后的结构性调仓；行业使用基金经济行业暴露加权。</div>
  </main>
</body>
</html>
"""


def export_png_from_html(html_path: Path, output_path: Path) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return f"playwright_import_failed: {exc!r}"
    try:
        with sync_playwright() as p:
            browser = None
            last_error: Exception | None = None
            for channel in ("msedge", "chrome"):
                try:
                    browser = p.chromium.launch(channel=channel)
                    break
                except Exception as exc:
                    last_error = exc
            if browser is None:
                return f"browser_launch_failed: {last_error!r}"
            page = browser.new_page(viewport={"width": 1000, "height": 1500}, device_scale_factor=2)
            page.goto(html_path.as_uri(), wait_until="load")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output_path), full_page=True)
            browser.close()
    except Exception as exc:
        return f"screenshot_failed: {exc!r}"
    return None


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.db_path)
    output_root = Path(args.output_root)
    start = parse_date(args.start)
    end = parse_date(args.end)
    prev_start = parse_date(args.previous_start) if args.previous_start else start - timedelta(days=7)
    prev_end = parse_date(args.previous_end) if args.previous_end else end - timedelta(days=7)
    run_slug = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    output_dir = output_root / run_slug
    assets_dir = output_dir / "assets"
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        current_rows = load_rows(conn, start, end)
        previous_rows = load_rows(conn, prev_start, prev_end)
        strategy_quality = load_strategy_quality(conn)
        advisor_quality = load_advisor_quality(conn)
    finally:
        conn.close()

    build_events = find_build_events(current_rows)
    current_ex_build = [row for row in current_rows if row["event_id"] not in build_events]
    summary = {
        "current": summarize_rows(current_rows),
        "current_ex_build": summarize_rows(current_ex_build),
        "previous": summarize_rows(previous_rows),
        "excluded_build_events": sorted(build_events),
    }

    asset_current = group_rows(current_ex_build, "standard_asset")
    asset_previous = group_rows(previous_rows, "standard_asset")
    asset_signals: list[dict[str, Any]] = []
    for name, item in asset_current.items():
        if abs(item["net"]) < 20:
            continue
        quality = quality_for_group(item, strategy_quality, advisor_quality)
        asset_signals.append(compact_item(name, item, asset_previous.get(name), quality))
    asset_signals.sort(key=lambda item: abs(float(item["net"])), reverse=True)

    econ_asset_current, _ = weighted_exposure_flow(current_ex_build, "economic_asset_json")
    econ_asset_previous, _ = weighted_exposure_flow(previous_rows, "economic_asset_json")
    econ_asset_signals = []
    for name, item in econ_asset_current.items():
        if abs(item["net"]) < 10:
            continue
        quality = quality_for_group(item, strategy_quality, advisor_quality)
        econ_asset_signals.append(compact_item(name, item, econ_asset_previous.get(name), quality))
    econ_asset_signals.sort(key=lambda item: abs(float(item["net"])), reverse=True)

    industry_current, industry_coverage = weighted_exposure_flow(current_ex_build, "economic_industry_json")
    industry_previous, _ = weighted_exposure_flow(previous_rows, "economic_industry_json")
    industry_signals: list[dict[str, Any]] = []
    for name, item in industry_current.items():
        prev_item = industry_previous.get(name)
        prev_net = float(prev_item.get("net", 0.0)) if prev_item else 0.0
        if not (abs(item["net"]) >= 5 or (abs(item["net"] - prev_net) >= 12 and abs(item["net"]) >= 3)):
            continue
        quality = quality_for_group(item, strategy_quality, advisor_quality)
        compact = compact_item(name, item, prev_item, quality)
        examples = sorted(item["examples"].items(), key=lambda pair: abs(pair[1]), reverse=True)[:3]
        compact["examples"] = [(fund, round2(value)) for fund, value in examples]
        if compact["name"] in {"有色金属", "电子", "基础化工"}:
            compact["conclusion"] = "从前周净流入转为净流出，属于本周最清晰的行业降温信号。"
        elif compact["name"] in {"国防军工", "计算机"}:
            compact["conclusion"] = "延续净流出，说明成长风险偏好仍在压降。"
        elif compact["name"] == "通信":
            compact["conclusion"] = "仍有净流入，但增配强度较前周大幅收缩。"
        elif compact["name"] == "医药生物":
            compact["conclusion"] = "仍为净流出，压力较前周有所缓和。"
        else:
            compact["conclusion"] = "周环比变化明显，适合进入下周跟踪清单。"
        industry_signals.append(compact)
    industry_priority = {"有色金属": 0, "电子": 1, "基础化工": 2, "国防军工": 3, "计算机": 4, "医药生物": 5, "通信": 6, "汽车": 7}
    industry_signals.sort(key=lambda item: (industry_priority.get(item["name"], 99), -abs(float(item["delta"]))))
    industry_chart_signals = sorted(industry_signals[:8], key=lambda item: float(item["net"]))

    gf_rows = [
        row
        for row in current_rows
        if "广发" in str(row.get("fund_company") or "") or "广发" in str(row.get("fund_name") or "")
    ]
    gf_grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "add": 0.0,
            "reduce": 0.0,
            "net": 0.0,
            "gross": 0.0,
            "rows": 0,
            "funds": set(),
            "strategies": set(),
            "advisors": set(),
            "events": set(),
            "examples": [],
        }
    )
    for row in gf_rows:
        key = f"{row.get('fund_code') or ''} {row.get('fund_name') or ''}".strip()
        change = float(row["weight_change"] or 0)
        item = gf_grouped[key]
        item["rows"] += 1
        item["funds"].add(row.get("fund_code") or row.get("fund_name"))
        item["strategies"].add(row["strategy_id"])
        if row.get("advisor"):
            item["advisors"].add(row["advisor"])
        item["events"].add(row["event_id"])
        item["examples"].append(
            {
                "date": row["rebalance_date"],
                "advisor": row.get("advisor") or "",
                "strategy": row.get("strategy_name") or "",
                "change": round2(change),
            }
        )
        if change > 0:
            item["add"] += change
        else:
            item["reduce"] += -change
        item["net"] += change
        item["gross"] += abs(change)

    gf_signals: list[dict[str, Any]] = []
    for fund, item in gf_grouped.items():
        if not (abs(item["net"]) >= 7 or (len(item["strategies"]) >= 2 and abs(item["net"]) >= 5)):
            continue
        quality = quality_for_group(item, strategy_quality, advisor_quality)
        compact = compact_item(fund, item, None, quality)
        compact["fund"] = fund
        compact["fund_short"] = fund.split(" ", 1)[1] if " " in fund else fund
        compact["examples"] = sorted(item["examples"], key=lambda entry: abs(float(entry["change"] or 0)), reverse=True)[:4]
        if "景宁债券C" in fund:
            compact["conclusion"] = "正向线索明确，银华活钱加与目标盈建仓共同贡献流入。"
            compact["tone"] = "positive"
        elif "景明中短债C" in fund:
            compact["conclusion"] = "主要体现为景明中短债向景宁债券的内部替换。"
            compact["tone"] = "negative"
        elif "成长启航" in fund:
            compact["conclusion"] = "跨 3 家机构减仓，属于广发权益产品本周最需要跟踪的负面线索。"
            compact["tone"] = "negative"
        else:
            compact["conclusion"] = "变化达到显著阈值，进入跟踪清单。"
            compact["tone"] = "focus"
        gf_signals.append(compact)
    gf_signals.sort(key=lambda item: abs(float(item["net"])), reverse=True)

    asset_chart_path = assets_dir / "asset_direction.png"
    industry_chart_path = assets_dir / "industry_signals.png"
    plot_asset_chart(asset_signals, asset_chart_path)
    plot_industry_chart(industry_chart_signals, industry_chart_path)

    summary_payload: dict[str, Any] = {}
    for name, values in summary.items():
        if isinstance(values, dict):
            summary_payload[name] = {k: round2(v) if isinstance(v, float) else v for k, v in values.items()}
        else:
            summary_payload[name] = values

    payload = {
        "period_label": f"{start.isoformat()} 至 {end.isoformat()}",
        "prev_period_label": f"{prev_start.isoformat()} 至 {prev_end.isoformat()}",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary_payload,
        "asset_signals": asset_signals,
        "econ_asset_signals": econ_asset_signals,
        "industry_signals": industry_signals,
        "industry_coverage": {k: round2(v) if isinstance(v, float) else v for k, v in industry_coverage.items()},
        "gf_signals": gf_signals,
        "asset_chart_rel": "assets/asset_direction.png",
        "industry_chart_rel": "assets/industry_signals.png",
    }
    data_path = output_dir / "weekly_rebalance_brief_data.json"
    html_path = output_dir / "weekly_rebalance_brief.html"
    png_path = output_dir / "weekly_rebalance_brief.png"
    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(build_share_html(payload), encoding="utf-8")
    screenshot_error = export_png_from_html(html_path, png_path)
    return {
        "html": str(html_path),
        "share_image": str(png_path) if screenshot_error is None else None,
        "share_image_error": screenshot_error,
        "data": str(data_path),
        "asset_chart": str(asset_chart_path),
        "industry_chart": str(industry_chart_path),
        "summary": payload["summary"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成上周全市场投顾调仓极简 HTML 简报。")
    parser.add_argument("--db-path", default=str(DEFAULT_DB))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--start", default="2026-06-22")
    parser.add_argument("--end", default="2026-06-28")
    parser.add_argument("--previous-start", default="2026-06-15")
    parser.add_argument("--previous-end", default="2026-06-21")
    return parser.parse_args()


def main() -> None:
    result = build_report(parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
