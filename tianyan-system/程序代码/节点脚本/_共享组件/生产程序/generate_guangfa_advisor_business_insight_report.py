from __future__ import annotations

import argparse
import base64
import html
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
import seaborn as sns


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_RESULT_ROOT = PROJECT_ROOT / "site"
DEFAULT_SOURCE = (
    DEFAULT_RESULT_ROOT
    / "reports"
    / "advisor_public_fund_mixed_performance_20260630"
    / "workbook_source.json"
)
DEFAULT_OUTPUT_DIR = (
    DEFAULT_RESULT_ROOT / "reports" / "guangfa_advisor_business_insights_20260630"
)


COL = {
    "rank": "排名",
    "ptype": "产品类型",
    "fund_main": "基金主类型",
    "fund_tag": "基金类型标签",
    "id": "产品ID",
    "code": "产品代码",
    "name": "产品名称",
    "org": "机构",
    "channel": "渠道",
    "manager": "管理人/经理",
    "is_gf": "是否广发",
    "bucket": "基准风险资产权重",
    "eq_weight": "基准权益权重",
    "unknown_weight": "基准未知权重",
    "benchmark": "业绩比较基准",
    "bucket_source": "基准风险资产权重来源",
    "ret_h1": "上半年收益率",
    "dd_h1": "上半年最大回撤",
    "vol_h1": "上半年年化波动率",
    "ret_1m": "近1月收益率",
    "ret_3m": "近3月收益率",
    "ret_1y": "近1年收益率",
    "detail": "详情链接",
}

STRATEGY = "投顾策略"
FUND = "公募基金"
YES = "是"
BUCKET_ORDER = [f"L{i}" for i in range(11)] + ["未分档"]
BUCKET_SORT = {bucket: idx for idx, bucket in enumerate(BUCKET_ORDER)}

TOKENS = {
    "surface": "#FCFCFD",
    "panel": "#FFFFFF",
    "ink": "#1F2430",
    "muted": "#6F768A",
    "grid": "#E6E8F0",
    "axis": "#D7DBE7",
}
COLORS = {
    "blue": {"base": "#A3BEFA", "mid": "#5477C4", "dark": "#2E4780"},
    "orange": {"base": "#F0986E", "mid": "#CC6F47", "dark": "#804126"},
    "olive": {"base": "#A3D576", "mid": "#71B436", "dark": "#386411"},
    "neutral": {"light": "#E2E5EA", "mid": "#7A828F", "dark": "#464C55"},
}


def pct_value(value: Any) -> float | None:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return None
    return float(number)


def fmt_pct(value: Any, digits: int = 1) -> str:
    number = pct_value(value)
    if number is None:
        return "-"
    return f"{number * 100:.{digits}f}%"


def fmt_pp(value: Any, digits: int = 1, signed: bool = True) -> str:
    number = pct_value(value)
    if number is None:
        return "-"
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number * 100:.{digits}f}pp"


def fmt_int(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{int(value):,}"


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def bucket_key(value: Any) -> int:
    return BUCKET_SORT.get(clean_text(value), 999)


def html_escape(value: Any) -> str:
    return html.escape(clean_text(value))


def table_html(rows: list[dict[str, Any]], columns: list[tuple[str, str]], *, cls: str = "") -> str:
    if not rows:
        return "<p class='empty'>暂无数据。</p>"
    head = "".join(f"<th>{html.escape(label)}</th>" for _, label in columns)
    body_rows = []
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key, "")
            class_name = "num" if isinstance(value, (int, float)) and not isinstance(value, bool) else ""
            if key.endswith("_pct"):
                value = fmt_pct(value)
                class_name = "num"
            elif key.endswith("_pp"):
                value = fmt_pp(value)
                class_name = "num"
            elif key.endswith("_n") or key in {"rank", "peer_count", "product_count"}:
                value = fmt_int(value)
                class_name = "num"
            cells.append(f"<td class='{class_name}'>{html_escape(value)}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table class='{cls}'><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def use_chart_theme() -> None:
    sns.set_theme(
        style="whitegrid",
        rc={
            "figure.facecolor": TOKENS["surface"],
            "figure.edgecolor": "none",
            "savefig.facecolor": "none",
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
            "font.sans-serif": [
                "Microsoft YaHei",
                "SimHei",
                "Noto Sans CJK SC",
                "Arial Unicode MS",
                "DejaVu Sans",
                "sans-serif",
            ],
        },
    )


def add_chart_header(fig: plt.Figure, ax: plt.Axes, title: str, subtitle: str) -> None:
    ax.set_title("")
    fig.subplots_adjust(top=0.82)
    left = ax.get_position().x0
    fig.text(left, 0.97, title, ha="left", va="top", fontsize=14, fontweight="bold", color=TOKENS["ink"])
    fig.text(left, 0.925, subtitle, ha="left", va="top", fontsize=10, color=TOKENS["muted"])
    sns.despine(ax=ax)


def data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def load_rows(source: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    if not rows:
        raise RuntimeError(f"source has no rows: {source}")
    df = pd.DataFrame(rows)
    required = set(COL.values())
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(f"source missing columns: {missing}")
    for key in [
        "rank",
        "eq_weight",
        "unknown_weight",
        "ret_h1",
        "dd_h1",
        "vol_h1",
        "ret_1m",
        "ret_3m",
        "ret_1y",
    ]:
        df[key] = pd.to_numeric(df[COL[key]], errors="coerce")
    return df, payload.get("meta") or {}


def add_percentiles(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["bucket_ret_rank"] = pd.NA
    df["bucket_ret_percentile"] = pd.NA
    for (ptype, bucket), part in df[df["ret_h1"].notna()].groupby([COL["ptype"], COL["bucket"]], dropna=False):
        values = part["ret_h1"]
        n = len(values)
        ranks = values.rank(method="min", ascending=False)
        percentiles = values.rank(method="max", pct=True)
        df.loc[part.index, "bucket_ret_rank"] = ranks.astype(int)
        df.loc[part.index, "bucket_ret_percentile"] = percentiles
        df.loc[part.index, "bucket_peer_count"] = n
    df["calmar_h1"] = df["ret_h1"] / df["dd_h1"].abs()
    df.loc[df["dd_h1"].abs() < 0.00001, "calmar_h1"] = pd.NA
    return df


def median_compare(
    df: pd.DataFrame,
    *,
    product_type: str,
    focal_label: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scoped = df[(df[COL["ptype"]] == product_type) & df["ret_h1"].notna()]
    for bucket, part in scoped.groupby(COL["bucket"], dropna=False):
        gf = part[part[COL["is_gf"]] == YES]
        peers = part[part[COL["is_gf"]] != YES]
        if gf.empty:
            continue
        rows.append(
            {
                "bucket": clean_text(bucket),
                "gf_n": int(len(gf)),
                "peer_n": int(len(peers)),
                "gf_median_pct": float(gf["ret_h1"].median()),
                "peer_median_pct": float(peers["ret_h1"].median()) if not peers.empty else None,
                "gap_pp": float(gf["ret_h1"].median() - peers["ret_h1"].median()) if not peers.empty else None,
                "gf_dd_pct": float(gf["dd_h1"].median()) if gf["dd_h1"].notna().any() else None,
                "peer_dd_pct": float(peers["dd_h1"].median()) if peers["dd_h1"].notna().any() else None,
                "gf_vol_pct": float(gf["vol_h1"].median()) if gf["vol_h1"].notna().any() else None,
                "peer_vol_pct": float(peers["vol_h1"].median()) if peers["vol_h1"].notna().any() else None,
                "scope": focal_label,
            }
        )
    return sorted(rows, key=lambda row: bucket_key(row["bucket"]))


def compact_product_row(row: pd.Series) -> dict[str, Any]:
    return {
        "name": clean_text(row.get(COL["name"])),
        "code": clean_text(row.get(COL["code"])),
        "id": clean_text(row.get(COL["id"])),
        "channel": clean_text(row.get(COL["channel"])),
        "org": clean_text(row.get(COL["org"])),
        "bucket": clean_text(row.get(COL["bucket"])),
        "fund_main": clean_text(row.get(COL["fund_main"])),
        "fund_tag": clean_text(row.get(COL["fund_tag"])),
        "ret_h1_pct": pct_value(row.get("ret_h1")),
        "dd_h1_pct": pct_value(row.get("dd_h1")),
        "vol_h1_pct": pct_value(row.get("vol_h1")),
        "ret_3m_pct": pct_value(row.get("ret_3m")),
        "ret_1y_pct": pct_value(row.get("ret_1y")),
        "rank": int(row.get("bucket_ret_rank")) if pd.notna(row.get("bucket_ret_rank")) else None,
        "peer_count": int(row.get("bucket_peer_count")) if pd.notna(row.get("bucket_peer_count")) else None,
        "percentile_pct": pct_value(row.get("bucket_ret_percentile")),
    }


def top_rows(df: pd.DataFrame, mask: pd.Series, *, limit: int, sort_cols: list[str], asc: list[bool]) -> list[dict[str, Any]]:
    part = df[mask].copy()
    if part.empty:
        return []
    return [compact_product_row(row) for _, row in part.sort_values(sort_cols, ascending=asc).head(limit).iterrows()]


def chart_gap(rows: list[dict[str, Any]], path: Path, title: str, subtitle: str) -> None:
    plot_df = pd.DataFrame(rows)
    plot_df = plot_df[plot_df["gap_pp"].notna()].copy()
    plot_df["bucket_order"] = plot_df["bucket"].map(bucket_key)
    plot_df = plot_df.sort_values("bucket_order")
    fig, ax = plt.subplots(figsize=(10.4, 5.6), dpi=160)
    colors = [COLORS["olive"]["base"] if v >= 0 else COLORS["orange"]["base"] for v in plot_df["gap_pp"]]
    edge_colors = [COLORS["olive"]["dark"] if v >= 0 else COLORS["orange"]["dark"] for v in plot_df["gap_pp"]]
    bars = ax.barh(plot_df["bucket"], plot_df["gap_pp"] * 100, color=colors, edgecolor=edge_colors, linewidth=1.0)
    ax.axvline(0, color=TOKENS["ink"], linewidth=1.0)
    ax.set_xlabel("广发中位收益 - 非广发同档中位收益（百分点）")
    ax.set_ylabel("基准风险资产权重")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}pp"))
    max_abs = max(1.0, float((plot_df["gap_pp"].abs() * 100).max()))
    ax.set_xlim(-max_abs * 1.28, max_abs * 1.28)
    for bar, value in zip(bars, plot_df["gap_pp"] * 100):
        x = value + (0.25 if value >= 0 else -0.25)
        ha = "left" if value >= 0 else "right"
        ax.text(x, bar.get_y() + bar.get_height() / 2, f"{value:+.1f}pp", va="center", ha=ha, fontsize=8, color=TOKENS["ink"])
    add_chart_header(fig, ax, title, subtitle)
    fig.tight_layout(rect=[0, 0, 1, 0.86])
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def chart_strategy_count(df: pd.DataFrame, path: Path) -> None:
    gf_strategy = df[(df[COL["ptype"]] == STRATEGY) & (df[COL["is_gf"]] == YES)].copy()
    counts = (
        gf_strategy.groupby(COL["bucket"], dropna=False)
        .agg(product_count=(COL["name"], "size"), median_ret=("ret_h1", "median"))
        .reset_index()
    )
    counts["bucket"] = counts[COL["bucket"]].map(clean_text)
    counts["bucket_order"] = counts["bucket"].map(bucket_key)
    counts = counts.sort_values("bucket_order")
    fig, ax = plt.subplots(figsize=(10.4, 4.9), dpi=160)
    sns.barplot(data=counts, x="bucket", y="product_count", ax=ax, color=COLORS["blue"]["base"], edgecolor=COLORS["blue"]["dark"], linewidth=1)
    ax.set_xlabel("基准风险资产权重")
    ax.set_ylabel("广发投顾策略数")
    for patch, value in zip(ax.patches, counts["product_count"]):
        ax.text(patch.get_x() + patch.get_width() / 2, patch.get_height() + 0.4, f"{int(value)}", ha="center", va="bottom", fontsize=8)
    add_chart_header(fig, ax, "广发投顾货架在中高权益区间存在断点", "截至 2026-06-30；L7 无广发投顾样本，L8 仅 1 条，客户从均衡到高权益之间缺少过渡层。")
    fig.tight_layout(rect=[0, 0, 1, 0.86])
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def build_analysis(df: pd.DataFrame, meta: dict[str, Any]) -> dict[str, Any]:
    df = add_percentiles(df)
    strategy_mask = df[COL["ptype"]] == STRATEGY
    fund_mask = df[COL["ptype"]] == FUND
    gf_mask = df[COL["is_gf"]] == YES
    gf_strategy = df[strategy_mask & gf_mask].copy()
    gf_fund = df[fund_mask & gf_mask].copy()

    strategy_compare = median_compare(df, product_type=STRATEGY, focal_label="投顾策略")
    fund_compare = median_compare(df, product_type=FUND, focal_label="公募基金")

    high_bucket = df[COL["bucket"]].isin(["L8", "L9", "L10"])
    low_bucket = df[COL["bucket"]].isin(["L0", "L1", "L2"])
    attack = top_rows(
        df,
        strategy_mask & gf_mask & high_bucket & df["ret_h1"].notna(),
        limit=12,
        sort_cols=["ret_h1"],
        asc=[False],
    )
    steady = top_rows(
        df,
        strategy_mask & gf_mask & low_bucket & df["ret_h1"].notna(),
        limit=12,
        sort_cols=["bucket_ret_percentile", "ret_h1"],
        asc=[False, False],
    )
    watch = top_rows(
        df,
        strategy_mask
        & gf_mask
        & df["ret_h1"].notna()
        & ((df["ret_h1"] < 0) | (df["bucket_ret_percentile"] <= 0.25) | (df["dd_h1"] <= -0.15)),
        limit=12,
        sort_cols=["bucket_ret_percentile", "ret_h1"],
        asc=[True, True],
    )
    fund_whitelist = top_rows(
        df,
        fund_mask
        & gf_mask
        & df["ret_h1"].notna()
        & (df["bucket_ret_percentile"] >= 0.90)
        & (df["ret_h1"] > 0),
        limit=18,
        sort_cols=["bucket_ret_percentile", "ret_h1"],
        asc=[False, False],
    )
    fund_watch = top_rows(
        df,
        fund_mask
        & gf_mask
        & df["ret_h1"].notna()
        & ((df["bucket_ret_percentile"] <= 0.10) | (df["ret_h1"] < -0.10) | (df["dd_h1"] <= -0.20)),
        limit=18,
        sort_cols=["ret_h1"],
        asc=[True],
    )

    duplicates = (
        gf_strategy.groupby(COL["name"])
        .filter(lambda part: part[COL["channel"]].nunique() > 1)
        .sort_values([COL["name"], COL["channel"]])
    )
    duplicate_rows = [compact_product_row(row) for _, row in duplicates.iterrows()]

    bucket_ladder = (
        gf_strategy.groupby(COL["bucket"], dropna=False)
        .agg(
            product_count=(COL["name"], "size"),
            ret_n=("ret_h1", "count"),
            median_ret_pct=("ret_h1", "median"),
            median_dd_pct=("dd_h1", "median"),
            median_vol_pct=("vol_h1", "median"),
        )
        .reset_index()
    )
    bucket_ladder["bucket"] = bucket_ladder[COL["bucket"]].map(clean_text)
    bucket_ladder = bucket_ladder.sort_values("bucket", key=lambda s: s.map(bucket_key))
    ladder_rows = [
        {
            "bucket": row["bucket"],
            "product_count": int(row["product_count"]),
            "ret_n": int(row["ret_n"]),
            "median_ret_pct": pct_value(row["median_ret_pct"]),
            "median_dd_pct": pct_value(row["median_dd_pct"]),
            "median_vol_pct": pct_value(row["median_vol_pct"]),
        }
        for _, row in bucket_ladder.iterrows()
    ]

    missing_bucket_rows = gf_strategy[gf_strategy[COL["bucket"]].eq("未分档")]
    l7_count = int((gf_strategy[COL["bucket"]] == "L7").sum())
    l8_count = int((gf_strategy[COL["bucket"]] == "L8").sum())

    summary = {
        "as_of_date": meta.get("asOfDate", "2026-06-30"),
        "source_generated_at": meta.get("generatedAt", ""),
        "source_strategy_count": int(meta.get("strategyRowCount") or strategy_mask.sum()),
        "source_fund_count": int(meta.get("publicFundRowCount") or fund_mask.sum()),
        "gf_strategy_count": int(len(gf_strategy)),
        "gf_strategy_ret_count": int(gf_strategy["ret_h1"].notna().sum()),
        "gf_fund_count": int(len(gf_fund)),
        "gf_fund_ret_count": int(gf_fund["ret_h1"].notna().sum()),
        "gf_strategy_channel_counts": gf_strategy[COL["channel"]].value_counts().to_dict(),
        "gf_strategy_missing_bucket_count": int(len(missing_bucket_rows)),
        "gf_strategy_l7_count": l7_count,
        "gf_strategy_l8_count": l8_count,
        "strategy_compare": strategy_compare,
        "fund_compare": fund_compare,
        "attack_strategies": attack,
        "steady_strategies": steady,
        "strategy_watchlist": watch,
        "fund_whitelist": fund_whitelist,
        "fund_watchlist": fund_watch,
        "duplicate_strategy_rows": duplicate_rows,
        "gf_strategy_ladder": ladder_rows,
    }
    return {"df": df, "summary": summary}


def render_report(summary: dict[str, Any], charts: dict[str, str], output_dir: Path) -> str:
    as_of = clean_text(summary["as_of_date"])
    title = f"广发基金业务机会报告（截至 {as_of}）"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    strategy_compare_focus = [
        row
        for row in summary["strategy_compare"]
        if row["bucket"] in {"L0", "L1", "L2", "L5", "L6", "L8", "L9", "L10", "未分档"}
    ]
    fund_compare_focus = [
        row
        for row in summary["fund_compare"]
        if row["bucket"] in {"L0", "L2", "L5", "L6", "L8", "L9", "L10"}
    ]

    css = """
:root {
  color-scheme: light;
  --ink: #1f2430;
  --muted: #687083;
  --line: #e4e8f0;
  --panel: #ffffff;
  --surface: #f7f8fb;
  --blue: #5477c4;
  --blue-soft: #eaf1fe;
  --gold-soft: #fff4c2;
  --orange: #cc6f47;
  --orange-soft: #ffedde;
  --olive: #71b436;
  --olive-soft: #d8ecbd;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
  color: var(--ink);
  background: var(--surface);
  line-height: 1.62;
}
main { max-width: 1180px; margin: 0 auto; padding: 32px 24px 56px; }
header { padding: 18px 0 22px; border-bottom: 1px solid var(--line); }
h1 { font-size: 30px; line-height: 1.2; margin: 0 0 10px; letter-spacing: 0; }
h2 { font-size: 22px; margin: 34px 0 12px; line-height: 1.35; }
h3 { font-size: 17px; margin: 22px 0 8px; }
p { margin: 8px 0 12px; }
.meta { color: var(--muted); font-size: 13px; }
.summary {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 18px 20px;
  margin-top: 20px;
}
.summary h2 { margin-top: 0; }
.summary ul { margin: 0; padding-left: 20px; }
.summary li { margin: 9px 0; }
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 18px 0;
}
.metric {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
}
.metric b { display: block; font-size: 24px; line-height: 1.2; }
.metric span { color: var(--muted); font-size: 12px; }
.chart {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  margin: 14px 0 18px;
}
.chart img { width: 100%; display: block; }
.note {
  color: var(--muted);
  font-size: 13px;
  border-left: 3px solid var(--line);
  padding-left: 10px;
}
.callout {
  border-radius: 8px;
  padding: 14px 16px;
  background: var(--blue-soft);
  border: 1px solid #cedffe;
  margin: 14px 0;
}
.warn {
  background: var(--orange-soft);
  border-color: #ffbda1;
}
.ok {
  background: var(--olive-soft);
  border-color: #beeb96;
}
table {
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  margin: 12px 0 20px;
  font-size: 13px;
}
th, td { border-bottom: 1px solid var(--line); padding: 8px 9px; text-align: left; vertical-align: top; }
th { background: #f1f4f8; color: #333b4d; font-weight: 700; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.small { font-size: 12px; color: var(--muted); }
.actions li { margin-bottom: 8px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 860px) {
  main { padding: 22px 14px 40px; }
  .metric-grid, .two-col { grid-template-columns: 1fr; }
  table { display: block; overflow-x: auto; white-space: nowrap; }
}
"""

    strategy_compare_table = table_html(
        strategy_compare_focus,
        [
            ("bucket", "分档"),
            ("gf_n", "广发策略数"),
            ("peer_n", "非广发同档数"),
            ("gf_median_pct", "广发中位收益"),
            ("peer_median_pct", "非广发中位收益"),
            ("gap_pp", "收益差"),
            ("gf_dd_pct", "广发中位回撤"),
            ("gf_vol_pct", "广发中位波动"),
        ],
    )
    fund_compare_table = table_html(
        fund_compare_focus,
        [
            ("bucket", "分档"),
            ("gf_n", "广发基金数"),
            ("peer_n", "非广发同档数"),
            ("gf_median_pct", "广发中位收益"),
            ("peer_median_pct", "非广发中位收益"),
            ("gap_pp", "收益差"),
            ("gf_dd_pct", "广发中位回撤"),
            ("gf_vol_pct", "广发中位波动"),
        ],
    )
    product_columns = [
        ("name", "产品"),
        ("channel", "渠道"),
        ("bucket", "分档"),
        ("ret_h1_pct", "上半年收益"),
        ("dd_h1_pct", "最大回撤"),
        ("vol_h1_pct", "年化波动"),
        ("ret_1y_pct", "近1年收益"),
        ("rank", "同档排名"),
        ("peer_count", "同档样本"),
    ]
    fund_columns = [
        ("code", "代码"),
        ("name", "基金"),
        ("fund_main", "主类型"),
        ("bucket", "分档"),
        ("ret_h1_pct", "上半年收益"),
        ("dd_h1_pct", "最大回撤"),
        ("vol_h1_pct", "年化波动"),
        ("rank", "同档排名"),
        ("peer_count", "同档样本"),
    ]

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>{css}</style>
</head>
<body>
<main>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="meta">数据源：投顾策略 + 全量公募基金混排源；收益、回撤、波动均截至 {html.escape(as_of)}。生成时间：{html.escape(generated_at)}。</div>
  </header>

  <section class="summary">
    <h2>Executive Summary</h2>
    <ul>
      <li><b>高权益策略是当前最强的进攻卖点，但必须按风险分档销售。</b>广发投顾在 L10 同档中位收益为 {fmt_pct(next(row for row in summary["strategy_compare"] if row["bucket"] == "L10")["gf_median_pct"])}，较非广发同档高 {fmt_pp(next(row for row in summary["strategy_compare"] if row["bucket"] == "L10")["gap_pp"])}；L9 中位收益为 {fmt_pct(next(row for row in summary["strategy_compare"] if row["bucket"] == "L9")["gf_median_pct"])}，较非广发同档高 {fmt_pp(next(row for row in summary["strategy_compare"] if row["bucket"] == "L9")["gap_pp"])}。这能转化成“科技/成长/积极养老”的进攻型货架，但不适合面向低风险客户泛化宣传。</li>
      <li><b>稳健端不是没有亮点，而是需要集中打旗舰。</b>“稳健优选组合”在 L1 同档 {fmt_int(summary["steady_strategies"][0]["peer_count"])} 个投顾策略中排名第 {fmt_int(summary["steady_strategies"][0]["rank"])}，上半年收益 {fmt_pct(summary["steady_strategies"][0]["ret_h1_pct"])}、回撤 {fmt_pct(summary["steady_strategies"][0]["dd_h1_pct"])}；L2 的“辉享全天候稳健增长”“稳健向前跑”也排在同档前列。低风险营销应该围绕这些确定性样本，而不是平均化讲广发投顾。</li>
      <li><b>广发自家公募基金货架分化明显，不能把“内部基金”直接等同于优势。</b>广发公募基金在 L8、L10 的中位收益分别落后非广发同档 {fmt_pp(abs(next(row for row in summary["fund_compare"] if row["bucket"] == "L8")["gap_pp"]), signed=False)}、{fmt_pp(abs(next(row for row in summary["fund_compare"] if row["bucket"] == "L10")["gap_pp"]), signed=False)}，但半导体、科技、全球精选等单品位于同档前列。投顾组合应建立“内部基金白名单 + 观察名单”，用同档排名筛选底层，不应用公司标签兜底。</li>
      <li><b>货架结构存在中高权益断点和数据治理问题。</b>广发投顾 L7 暂无产品、L8 仅 {fmt_int(summary["gf_strategy_l8_count"])} 条，客户从 L5/L6 过渡到 L9/L10 时缺少缓冲层；另有 {fmt_int(summary["gf_strategy_missing_bucket_count"])} 条广发投顾仍未分档，全球多元类产品需要补齐可解释基准后再进入正式比较和营销话术。</li>
    </ul>
  </section>

  <section class="metric-grid">
    <div class="metric"><b>{fmt_int(summary["gf_strategy_count"])}</b><span>广发投顾策略</span></div>
    <div class="metric"><b>{fmt_int(summary["gf_fund_count"])}</b><span>广发公募基金样本</span></div>
    <div class="metric"><b>{fmt_int(summary["source_strategy_count"])}</b><span>全市场投顾策略口径</span></div>
    <div class="metric"><b>{fmt_int(summary["source_fund_count"])}</b><span>全量公募基金口径</span></div>
  </section>

  <section>
    <h2>1. 进攻线：高权益有明显相对优势，但要把风险讲清楚</h2>
    <p><b>业务含义：</b>L9/L10 是广发当前最适合做“主动进攻能力”表达的区间。这里的机会不是简单说收益高，而是同一基准风险资产权重内，广发投顾相对竞品表现靠前，说明可用于高风险客户的二次配置、权益回暖期的主题转化、以及已有稳健客户的小比例卫星仓位升级。</p>
    <div class="chart"><img src="{charts["strategy_gap"]}" alt="广发投顾同档中位收益差"></div>
    <h3>可直接进入营销货架的进攻型样本</h3>
    {table_html(summary["attack_strategies"], product_columns)}
    <div class="callout">
      <b>动作建议：</b>把“带你投科技”“广发积极养老”“奔跑吧牛基”“积极优选组合”做成高权益进攻线，但页面和投放话术统一绑定 L9/L10 分档、近半年回撤和波动，避免把主题反弹包装成低波稳健收益。
    </div>
  </section>

  <section>
    <h2>2. 稳健线：用旗舰带动低风险转化，不要平均化卖货架</h2>
    <p><b>业务含义：</b>低权益端的收益空间本来有限，客户更关心“比货币/纯债多拿一点，但回撤不能失控”。数据里真正有销售价值的是少数同档领先样本：L1 的“稳健优选组合”排名第一，L2 的“辉享全天候稳健增长”“稳健向前跑”排名靠前。这类产品适合作为银行、直销和 App 低风险客户的第一层转化入口。</p>
    <h3>低权益可作为转化锚点的样本</h3>
    {table_html(summary["steady_strategies"], product_columns)}
    <div class="callout ok">
      <b>动作建议：</b>低风险营销页默认展示“同分档排名 + 最大回撤 + 近 3 月延续性”，不要跨档拿高权益产品收益做对比。对“稳健优选组合”可以单独做“低权益增强”案例页，承接现金管理、纯债替代、定投启蒙三类场景。
    </div>
  </section>

  <section>
    <h2>3. 供给侧：广发基金不是整体优势，必须做白名单和观察名单</h2>
    <p><b>业务含义：</b>广发自家基金里有非常强的半导体、科技、全球精选等单品，但同档中位数并不全面领先。特别是 L8/L10 的广发基金中位收益落后非广发同档，说明如果投顾底层为了内部协同而盲目提高广发基金占比，可能损害组合竞争力。更合理的机制是把同档前列单品纳入“可增配池”，把拖累明显的价值、港股、消费、商品类样本纳入“解释/降权/替代评估池”。</p>
    <div class="chart"><img src="{charts["fund_gap"]}" alt="广发基金同档中位收益差"></div>
    <h3>内部基金白名单候选：能用于主题卫星或组合底层增强</h3>
    {table_html(summary["fund_whitelist"], fund_columns)}
    <h3>内部基金观察名单：不宜用公司标签直接带入组合</h3>
    {table_html(summary["fund_watchlist"], fund_columns)}
    <div class="callout warn">
      <b>动作建议：</b>投顾投研会每周维护“广发基金白名单/观察名单”。入选白名单至少要满足同基准风险资产权重前 25%、近 3 月不显著掉队、回撤不明显劣于同档；观察名单进入组合前必须给出持仓逻辑和替代基金比较。
    </div>
  </section>

  <section>
    <h2>4. 货架结构：从稳健到进攻之间缺少缓冲产品</h2>
    <p><b>业务含义：</b>当前广发投顾覆盖了低权益和高权益，但 L7 缺位、L8 只有一条，导致客户风险升级路径过陡：从 L5/L6 的均衡配置直接跳到 L9/L10 主题或高权益，容易在回撤期造成体验落差。这个结构问题会影响长期留存，不只是新增销售问题。</p>
    <div class="chart"><img src="{charts["strategy_count"]}" alt="广发投顾分档货架数量"></div>
    <h3>广发投顾分档货架</h3>
    {table_html(summary["gf_strategy_ladder"], [("bucket", "分档"), ("product_count", "策略数"), ("ret_n", "收益有效数"), ("median_ret_pct", "中位收益"), ("median_dd_pct", "中位回撤"), ("median_vol_pct", "中位波动")])}
    <div class="callout">
      <b>动作建议：</b>补一个 L7/L8 “核心成长/均衡进取”层，不一定马上新发产品，可以先从现有“全球多元进取”“股债动态配置”“积极优选”中选择可降波或可约束回撤的版本，形成从 L5/L6 到 L9/L10 的中间台阶。
    </div>
  </section>

  <section>
    <h2>5. 渠道经营：同名产品要统一归因，天天强项要反哺自有渠道</h2>
    <p><b>业务含义：</b>广发自有渠道有 {fmt_int(summary["gf_strategy_channel_counts"].get("广发基金", 0))} 条策略，天天渠道有 {fmt_int(summary["gf_strategy_channel_counts"].get("天天基金/投顾", 0))} 条策略。部分同名产品在两个渠道表现接近，但天天渠道有“带你投科技”等强主题样本，自有渠道缺少同样清晰的科技进攻入口。渠道上不应只看产品名，还要看同名策略的分档、组合差异和收益风险差异。</p>
    <h3>同名策略跨渠道样本</h3>
    {table_html(summary["duplicate_strategy_rows"], product_columns)}
    <div class="callout">
      <b>动作建议：</b>把天天渠道验证过的强主题能力沉淀为自有渠道内容和组合线索；同名策略出现分档差异或未分档时，先修正基准和持仓口径，再用于销售归因。
    </div>
  </section>

  <section>
    <h2>6. 风险名单：这些产品需要投研解释或销售降噪</h2>
    <p><b>业务含义：</b>负收益、同档后 25%、或回撤明显较大的广发策略，不一定都要下架，但需要从营销前台移到解释或观察层。尤其“广发全球商品甄选”虽然权益权重为 0，但商品回撤较大，不应和货币/短债一起放在低风险货架里展示。</p>
    {table_html(summary["strategy_watchlist"], product_columns)}
    <div class="callout warn">
      <b>动作建议：</b>对“优选香港组合”“广发带你投红利”“带你投消费”“广发价值30甄选”“广发全球商品甄选”等样本建立月度复盘：判断是资产周期、风格暴露、选基问题还是基准口径问题。未复盘前，销售端不应把它们放在默认推荐位。
    </div>
  </section>

  <section>
    <h2>推荐后续动作</h2>
    <ol class="actions">
      <li><b>两周内重做投顾货架。</b>按 L0-L2 稳健增强、L3-L6 均衡配置、L7-L8 核心成长、L9-L10 主题进攻四层组织页面和销售素材，每层只放 2-4 个代表产品，并展示同档排名、上半年回撤、近 3 月延续性。</li>
      <li><b>建立“广发基金白名单/观察名单”机制。</b>白名单不是看是否广发，而是看同基准风险资产权重排名、回撤、近 3 月趋势；观察名单进入组合前必须说明替代理由。</li>
      <li><b>把“稳健优选组合”作为低风险转化旗舰。</b>围绕现金管理升级、纯债替代、低波增强三类场景做内容，目标不是宣传高收益，而是解释为什么同档内回撤和收益配比更有竞争力。</li>
      <li><b>把科技/成长线做成高风险客户的再配置入口。</b>“带你投科技”“广发积极养老”“奔跑吧牛基”等只面向已通过风险匹配的客户，用回撤阈值、持有期提示和分批配置降低售后波动压力。</li>
      <li><b>补齐中高权益缓冲层。</b>优先设计或改造 L7/L8 核心成长产品，用来承接从均衡配置升级到高权益主题之前的客户，不要让客户直接从 L5/L6 跳到 L9/L10。</li>
      <li><b>先修数据再讲全球多元。</b>未分档的全球多元类策略不能进入正式排名话术；补齐基准风险资产权重后，再判断其应该进入稳健、均衡还是进取货架。</li>
    </ol>
  </section>

  <section>
    <h2>进一步需要补的数据</h2>
    <p>本报告只基于产品业绩、回撤、波动、基准风险资产权重和渠道口径。要把动作转成经营目标，还需要接入各策略 AUM、申购赎回、客户风险等级、客户持有期、营销触达与转化数据。没有这些数据时，本报告可以指导“卖什么、怎么摆货架、哪些先复盘”，但不能直接给出收入贡献或转化率预测。</p>
  </section>

  <section>
    <h2>口径和限制</h2>
    <p class="note">比较口径为同一基准风险资产权重内的上半年收益率，回撤和波动用于风险解释。全量公募基金只对已有本地净值的基金计算业绩；缺净值基金不会进入收益排名。报告不构成投资建议，营销使用时必须保留适当性、风险等级和过往业绩不代表未来的提示。</p>
  </section>
</main>
</body>
</html>
"""
    return html_doc


def write_outputs(source: Path, output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = output_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    df, meta = load_rows(source)
    analysis = build_analysis(df, meta)
    df = analysis["df"]
    summary = analysis["summary"]
    use_chart_theme()
    strategy_gap_png = asset_dir / "strategy_gap_by_bucket.png"
    fund_gap_png = asset_dir / "fund_gap_by_bucket.png"
    strategy_count_png = asset_dir / "strategy_count_by_bucket.png"
    chart_gap(
        summary["strategy_compare"],
        strategy_gap_png,
        "广发投顾在高权益分档的相对优势最明显",
        "同基准风险资产权重内，比较广发投顾与非广发投顾的上半年中位收益差。",
    )
    chart_gap(
        summary["fund_compare"],
        fund_gap_png,
        "广发公募基金供给侧分化明显",
        "同基准风险资产权重内，比较广发基金与非广发基金的上半年中位收益差。",
    )
    chart_strategy_count(df, strategy_count_png)
    charts = {
        "strategy_gap": data_url(strategy_gap_png),
        "fund_gap": data_url(fund_gap_png),
        "strategy_count": data_url(strategy_count_png),
    }
    report_html = render_report(summary, charts, output_dir)
    report_path = output_dir / "report.html"
    report_path.write_text(report_html, encoding="utf-8")
    summary_path = output_dir / "analysis_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "source": str(source),
                "report": str(report_path),
                "charts": {
                    "strategy_gap": str(strategy_gap_png),
                    "fund_gap": str(fund_gap_png),
                    "strategy_count": str(strategy_count_png),
                },
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "report": str(report_path),
        "summary": str(summary_path),
        "strategy_gap": str(strategy_gap_png),
        "fund_gap": str(fund_gap_png),
        "strategy_count": str(strategy_count_png),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Guangfa advisor business insight report.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outputs = write_outputs(args.source.resolve(), args.output_dir.resolve())
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
