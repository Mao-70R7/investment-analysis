from __future__ import annotations

import argparse
import gzip
import html
import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


SUMMARY_MARKER = "window.__BASIC_DATA__.summary = "
STOPPED_PATTERN = re.compile(
    r"已停止|已终止|已下架|已清盘|期满|已止盈|非对客或已结束|stopped",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成隔离的机构列表页面预览，不修改正式发布集。")
    parser.add_argument("--strategy-pack", type=Path, required=True, help="strategy_list_pack.js(.gz) 或 basic_summary_core.js")
    parser.add_argument("--output", type=Path, required=True, help="预览 HTML 输出路径")
    return parser.parse_args()


def load_summary(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig") as handle:
        text = handle.read()
    if SUMMARY_MARKER not in text:
        raise ValueError(f"未找到策略汇总赋值标记: {path}")
    payload = text.split(SUMMARY_MARKER, 1)[1].strip().rstrip(";")
    summary = json.loads(payload)
    if not isinstance(summary.get("strategies"), list):
        raise ValueError("策略包缺少 summary.strategies 数组")
    return summary


def is_stopped(row: dict[str, Any]) -> bool:
    if int(row.get("是否历史接口留档") or 0) == 1:
        return True
    if int(row.get("是否已停止") or 0) == 1:
        return True
    status = " ".join(
        str(row.get(field) or "")
        for field in ("策略治理状态", "运作状态", "天天展示状态")
    )
    return bool(STOPPED_PATTERN.search(status))


def benchmark_complete(row: dict[str, Any]) -> bool:
    benchmark_text = str(row.get("业绩基准") or row.get("业绩基准说明") or "").strip()
    return bool(benchmark_text) and str(row.get("基准可用状态") or "").strip() == "文本+曲线"


def parse_data_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def performance_complete(row: dict[str, Any], latest_data_date: date) -> bool:
    performance_date = parse_data_date(row.get("最新业绩日期") or row.get("收益数据截至"))
    if performance_date is None:
        return False
    lag_days = (latest_data_date - performance_date).days
    quality = str(row.get("质检情况") or "")
    return 0 <= lag_days <= 5 and "官方披露业绩:完整" in quality


def position_complete(row: dict[str, Any]) -> bool:
    quality = str(row.get("质检情况") or "")
    return (
        "策略历史调仓数据:完整" in quality
        and bool(str(row.get("最新持仓日") or "").strip())
        and int(row.get("持仓基金数") or 0) > 0
    )


def empty_metrics(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "current": 0,
        "performanceComplete": 0,
        "dataComplete": 0,
        "stopped": 0,
        "missingBenchmark": 0,
        "missingPosition": 0,
        "performanceMissing": 0,
        "total": 0,
    }


def summarize(rows: list[dict[str, Any]], field: str, latest_data_date: date) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: empty_metrics(""))
    for row in rows:
        name = str(row.get(field) or "未披露").strip() or "未披露"
        item = grouped[name]
        item["name"] = name
        item["total"] += 1
        if is_stopped(row):
            item["stopped"] += 1
            continue

        if performance_complete(row, latest_data_date):
            benchmark_ok = benchmark_complete(row)
            position_ok = position_complete(row)
            item["performanceComplete"] += 1
            item["dataComplete"] += int(benchmark_ok and position_ok)
            item["missingBenchmark"] += int(not benchmark_ok)
            item["missingPosition"] += int(not position_ok)
        else:
            item["performanceMissing"] += 1

    result = list(grouped.values())
    for item in result:
        running = item["total"] - item["stopped"]
        item["current"] = running
        if item["performanceComplete"] + item["performanceMissing"] != running:
            raise AssertionError(f"运行中策略的业绩完整/缺失未对账: {field}={item['name']}")
        if item["current"] + item["stopped"] != item["total"]:
            raise AssertionError(f"当前策略与历史策略未对账: {field}={item['name']}")
        if item["performanceComplete"] + item["stopped"] + item["performanceMissing"] != item["total"]:
            raise AssertionError(f"策略总数未对账: {field}={item['name']}")
        if item["dataComplete"] > item["performanceComplete"]:
            raise AssertionError(f"数据完整数超出业绩完整范围: {field}={item['name']}")
        if item["missingBenchmark"] > item["performanceComplete"] or item["missingPosition"] > item["performanceComplete"]:
            raise AssertionError(f"基准/仓位缺失数超出业绩完整范围: {field}={item['name']}")
    return result


def totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "current",
        "performanceComplete",
        "dataComplete",
        "stopped",
        "missingBenchmark",
        "missingPosition",
        "performanceMissing",
        "total",
    )
    return {key: sum(int(row[key]) for row in rows) for key in keys}


def fmt(value: Any) -> str:
    return f"{int(value):,}"


def table_html(
    rows: list[dict[str, Any]],
    *,
    name_header: str,
    pin_guangfa: bool,
    system_total: int,
) -> str:
    max_total = max((int(row["total"]) for row in rows), default=1) or 1
    body: list[str] = []
    for index, row in enumerate(rows, start=1):
        pinned = pin_guangfa and row["name"] == "广发基金"
        current_share = (row["current"] / row["total"] * 100) if row["total"] else 0
        system_share = (row["total"] / system_total * 100) if system_total else 0
        bar_width = max(2.5, row["total"] / max_total * 100) if row["total"] else 0
        row_classes: list[str] = []
        if row["name"] == "广发基金":
            row_classes.append("is-guangfa")
        if row["performanceMissing"]:
            row_classes.append("has-performance-gap")
        class_attr = f' class="{" ".join(row_classes)}"' if row_classes else ""
        rank_text = "GF" if pinned else str(index)
        rank_class = "rank gf" if pinned else "rank"
        body.append(
            f"""
            <tr{class_attr}>
              <td class="name-cell"><span class="{rank_class}">{rank_text}</span><span><strong>{html.escape(row['name'])}</strong>{'<small>广发重点机构</small>' if pinned else ''}</span></td>
              <td class="current-cell"><strong>{fmt(row['current'])}</strong><small>占本组 {current_share:.1f}%</small></td>
              <td class="performance-cell"><strong>{fmt(row['performanceComplete'])}</strong><small>待补业绩 {fmt(row['performanceMissing'])}</small></td>
              <td class="complete-cell"><strong>{fmt(row['dataComplete'])}</strong><small>无基准 {fmt(row['missingBenchmark'])} · 无仓位 {fmt(row['missingPosition'])}</small></td>
              <td class="stopped-cell"><strong>{fmt(row['stopped'])}</strong><small>历史留档或已结束</small></td>
              <td class="total-cell"><strong>{fmt(row['total'])}</strong><span class="bar"><i style="width:{bar_width:.2f}%"></i></span><small>占系统 {system_share:.1f}%</small></td>
            </tr>"""
        )
    colgroup = "<colgroup><col style=\"width:28%\"><col style=\"width:14%\"><col style=\"width:14%\"><col style=\"width:16%\"><col style=\"width:14%\"><col style=\"width:14%\"></colgroup>"
    header = f"""
          <thead>
            <tr>
              <th>{html.escape(name_header)}</th>
              <th>当前策略<span>未终止</span></th>
              <th>可评价业绩<span>连续官方曲线</span></th>
              <th>数据完整<span>业绩＋基准＋仓位</span></th>
              <th>历史／已终止</th>
              <th>系统策略总数</th>
            </tr>
          </thead>"""
    return f"""
      <div class="table-wrap" tabindex="0">
        <table>{colgroup}{header}<tbody>{''.join(body)}</tbody></table>
      </div>"""


def summary_line(rows: list[dict[str, Any]], label: str) -> str:
    total = totals(rows)
    return f"""
      <div class="summary-line">
        <div><strong>{fmt(len(rows))} 个{html.escape(label)}</strong><small>按系统策略总数排序；每条策略在本视角只计一次</small></div>
        <div class="summary-totals"><span>当前 <b>{fmt(total['current'])}</b></span><span>历史／已终止 <b>{fmt(total['stopped'])}</b></span><span>合计 <b>{fmt(total['total'])}</b></span></div>
      </div>"""


def render(summary: dict[str, Any], source_path: Path) -> str:
    strategies = summary["strategies"]
    overview = summary.get("overview") or {}
    latest_data_date = parse_data_date(overview.get("数据更新至"))
    if latest_data_date is None:
        raise ValueError("overview.数据更新至 缺失或不是 YYYY-MM-DD 日期")
    channels = sorted(
        summarize(strategies, "渠道", latest_data_date),
        key=lambda row: (-row["total"], -row["current"], row["name"]),
    )
    managers = sorted(
        summarize(strategies, "投顾机构", latest_data_date),
        key=lambda row: (0 if row["name"] == "广发基金" else 1, -row["total"], -row["current"], row["name"]),
    )
    channel_totals = totals(channels)
    manager_totals = totals(managers)
    if channel_totals != manager_totals:
        raise AssertionError("销售渠道与投顾管理人汇总未对账")
    if channel_totals["total"] != len(strategies):
        raise AssertionError("机构汇总策略总数与策略包未对账")

    audit = {
        "source": str(source_path),
        "strategyCount": len(strategies),
        "channelCount": len(channels),
        "managerCount": len(managers),
        "latestDataDate": latest_data_date.isoformat(),
        "performanceFreshnessDays": 5,
        "totals": channel_totals,
        "pinnedManager": managers[0]["name"] if managers else "",
        "definition": {
            "current": "未命中停止、终止、下架、清盘、期满、止盈或历史留档规则的策略",
            "performanceComplete": "未终止、官方披露业绩质检完整，且最新业绩日距全库最新数据日为0至5个自然日",
            "dataComplete": "业绩完整范围内，同时具备基准文本与曲线、完整历史仓位",
            "missingBenchmark": "业绩完整范围内，基准文本缺失或基准可用状态不是文本+曲线",
            "missingPosition": "业绩完整范围内，历史调仓质检不完整，或最新持仓日/持仓基金数缺失",
            "performanceMissing": "未终止但不满足业绩完整条件，无可用于当前评价的连续官方披露曲线",
            "stopped": "命中停止、终止、下架、清盘、期满、止盈或历史留档治理规则",
        },
    }
    audit_json = json.dumps(audit, ensure_ascii=False).replace("</", "<\\/")
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    refresh_time = overview.get("数据刷新时间") or overview.get("生成时间") or "未披露"
    data_through = overview.get("数据更新至") or "未披露"
    current_rate = channel_totals["current"] / channel_totals["total"] * 100 if channel_totals["total"] else 0
    stopped_rate = channel_totals["stopped"] / channel_totals["total"] * 100 if channel_totals["total"] else 0

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>策略机构总览（预览）</title>
  <link rel="icon" href="data:,">
  <style>
    :root{{--ink:#102a43;--muted:#5c6f80;--line:#cad7df;--surface:#fff;--green:#087457;--green-deep:#075943;--green-soft:#e4f5ed;--amber:#a54b00;--amber-soft:#fff0d9;--purple:#6840a0;--purple-soft:#f1eafb;--red:#a82b24;--red-soft:#fff0ed;--slate:#405466;--slate-soft:#eef3f7;--paper:#f5f7f9;}}
    *{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(180deg,#e9f1f3 0,#f6f8fa 360px);color:var(--ink);font-family:"Microsoft YaHei UI","PingFang SC",system-ui,sans-serif;}}
    .page{{max-width:1780px;margin:auto;padding:24px;}} .hero{{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:24px 28px;border:1px solid #c8d8df;border-radius:14px;background:linear-gradient(130deg,#edf8f5,#fff 56%,#eef2fa);box-shadow:0 14px 34px rgba(19,45,61,.1)}}
    .eyebrow{{margin:0 0 5px;color:#287165;font-size:12px;font-weight:800;letter-spacing:.14em}} h1{{margin:0;font-size:32px;letter-spacing:-.03em}} .hero p.desc{{max-width:820px;margin:8px 0 0;color:var(--muted);line-height:1.65}}
    .sync{{min-width:250px;padding:12px 15px;border:1px solid #b9cbd4;border-left:4px solid var(--red);border-radius:9px;background:#fff}} .sync span,.sync small{{display:block;color:var(--muted);font-size:11px}} .sync strong{{display:block;margin:3px 0;color:var(--red);font-size:16px;font-weight:900}}
    .preview-flag{{display:inline-flex;margin-top:11px;padding:5px 9px;border:1px solid #d4e2e6;border-radius:999px;background:#fff;color:#416476;font-size:11px;font-weight:700}}
    .status-board{{display:grid;grid-template-columns:minmax(360px,1.75fr) repeat(3,minmax(180px,1fr));gap:10px;margin:14px 0}} .metric-card{{min-height:112px;padding:16px 18px;border:1px solid var(--line);border-top-width:4px;border-radius:11px;background:#fff;box-shadow:0 7px 18px rgba(27,51,64,.07)}} .metric-card>span,.metric-main span{{display:block;color:var(--muted);font-size:12px;font-weight:700}} .metric-card>strong,.metric-main strong{{display:block;margin-top:4px;font-size:30px;line-height:1;font-variant-numeric:tabular-nums}} .metric-card small{{display:block;margin-top:12px;color:var(--muted);font-size:10px}} .performance-card{{display:grid;grid-template-columns:minmax(120px,.7fr) minmax(300px,1.3fr);align-items:center;gap:18px;border-top-color:var(--green);background:linear-gradient(135deg,#fff,#effaf5)}} .metric-main strong{{color:var(--green-deep);font-size:36px}} .metric-breakdown{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));overflow:hidden;border:1px solid #b8dcca;border-radius:9px;background:#fff}} .metric-breakdown span{{padding:12px 10px;color:#385b4d;font-size:11px;text-align:center}} .metric-breakdown span+span{{border-left:1px solid #d0e5dc}} .metric-breakdown b{{display:block;margin-bottom:3px;color:var(--green-deep);font-size:19px}} .metric-breakdown .is-benchmark{{background:var(--amber-soft);color:#7d4a14}} .metric-breakdown .is-benchmark b{{color:var(--amber)}} .metric-breakdown .is-position{{background:var(--purple-soft);color:#5d4778}} .metric-breakdown .is-position b{{color:var(--purple)}} .stopped-card{{border-top-color:#607386;background:linear-gradient(145deg,#fff,#f0f4f7)}} .stopped-card strong{{color:#34495b}} .missing-card{{border-top-color:var(--red);background:linear-gradient(145deg,#fff,#fff0ed)}} .missing-card strong{{color:var(--red)}} .total-card{{border-top-color:#173b57;background:linear-gradient(145deg,#fff,#edf3f7)}} .total-card strong{{color:#173b57}}
    .method-details{{margin-bottom:14px;overflow:hidden;border:1px solid #d2e2e4;border-radius:11px;background:#f2f8f8;color:#536371}} .method-details summary{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 15px;cursor:pointer;color:#245f68;font-size:13px;font-weight:800;list-style:none}} .method-details summary::-webkit-details-marker{{display:none}} .method-details summary::after{{content:"展开";padding:3px 8px;border:1px solid #c5dadd;border-radius:999px;background:#fff;color:#60727c;font-size:10px;font-weight:700}} .method-details[open] summary::after{{content:"收起"}} .method-details summary:hover{{background:#eaf5f5}} .method-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:0 14px 12px}} .method-grid article{{padding:11px 12px;border:1px solid #dbe7e8;border-radius:9px;background:#fff}} .method-grid strong{{display:block;margin-bottom:4px;color:#215e67;font-size:12px}} .method-grid p{{margin:0;font-size:11px;line-height:1.7}} .method-foot{{margin:0;padding:0 15px 13px;color:#7c5a30;font-size:10px;line-height:1.6}}
    .tab-shell{{min-width:0}} .tablist{{display:flex;gap:7px;margin-bottom:10px;padding:5px;border:1px solid #c8d5dc;border-radius:10px;background:#e4ebef}} .tab-btn{{display:flex;align-items:center;justify-content:center;gap:8px;min-height:44px;padding:8px 20px;border:1px solid transparent;border-radius:7px;background:transparent;color:#5b6e7c;font-family:inherit;font-size:13px;font-weight:800;cursor:pointer}} .tab-btn span{{display:inline-grid;place-items:center;min-width:25px;height:22px;padding:0 7px;border-radius:999px;background:rgba(255,255,255,.8);color:#55717b;font-size:10px}} .tab-btn:hover{{background:rgba(255,255,255,.7)}} .tab-btn.is-active{{border-color:#9bc7b7;background:#fff;color:var(--green-deep);box-shadow:0 3px 10px rgba(20,73,60,.12)}} .tab-btn.is-active span{{background:#d9efe6;color:var(--green-deep)}} .tabpanel[hidden]{{display:none!important}} .card{{min-width:0;overflow:hidden;border:1px solid #cbd7de;border-radius:12px;background:var(--surface);box-shadow:0 10px 26px rgba(25,48,62,.08)}} .card-head{{display:flex;align-items:center;gap:11px;padding:15px 18px 13px;border-bottom:1px solid #dbe4e8;background:linear-gradient(135deg,#e9f6f2,#fff)}} .card.manager .card-head{{background:linear-gradient(135deg,#edf0fa,#fff)}}
    .icon{{display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:#246e76;color:#fff;font-weight:900;box-shadow:0 5px 14px rgba(36,110,118,.23)}} .manager .icon{{background:#465e93}} .card-head p{{margin:0;color:var(--muted);font-size:11px}} .card-head h2{{margin:2px 0 0;font-size:20px}}
    .summary-line{{display:flex;flex-wrap:wrap;align-items:center;gap:7px;padding:10px 15px;border-bottom:1px solid #d9e2e7;background:#f7fafb;color:#536473;font-size:11px}} .summary-line strong{{margin-right:auto;color:#17344a;font-size:13px}} .summary-line span{{padding:4px 8px;border:1px solid #d4dfe5;border-radius:999px;background:#fff;font-weight:700}}
    .table-wrap{{max-height:590px;overflow:auto;outline:none;scrollbar-color:#9eafb9 transparent}} .table-wrap:focus-visible{{box-shadow:inset 0 0 0 3px rgba(8,116,87,.22)}} .table-content,table{{width:100%;min-width:960px}} table{{border-spacing:0;border-collapse:separate;table-layout:fixed;font-variant-numeric:tabular-nums}} th,td{{padding:9px 10px;text-align:right}} th{{height:38px;border-bottom:1px solid #bdccd5;background:#e6edf1;color:#294357;font-size:11px;font-weight:900}} thead th{{position:sticky;z-index:5}} thead .group-row th{{top:0}} thead .sub-row th{{top:38px}} .manager thead th{{background:#e8ecf5}} th:first-child{{text-align:left}} .performance-group{{text-align:center;background:#cce9dc!important;color:#075943}} .performance-total-head{{background:#dff2e8!important;color:#075943}} .data-complete-head{{background:#cfeadc!important;color:#075943}} .missing-benchmark-head{{background:#ffe6bd!important;color:#874000}} .missing-position-head{{background:#e8dcf6!important;color:#563185}} .performance-gap-head{{background:#ffd9d4!important;color:#8f211d;text-align:center}} .performance-gap-head span{{display:block;margin-top:2px;font-size:8px;font-weight:700}} td{{height:62px;border-bottom:1px solid #dfe7eb;background:#fff;color:#465b6b;font-size:13px;font-weight:800}} tbody tr:hover td{{filter:brightness(.975)}} tbody tr.has-performance-gap td:first-child{{box-shadow:inset 4px 0 0 #d44a40}} tbody tr.is-guangfa td:first-child{{background:#e7f4ee}} tbody tr.is-guangfa td:first-child strong{{color:#075943}}
    thead .group-row th:first-child{{left:0;z-index:8}} tbody td:first-child{{position:sticky;left:0;z-index:2}} .name-cell{{display:flex;align-items:center;gap:8px;text-align:left}} .name-cell>span:last-child{{min-width:0}} .name-cell strong{{display:block;overflow:hidden;text-overflow:ellipsis;color:#1f3142;white-space:nowrap}} .name-cell small{{display:block;margin-top:2px;color:#39715d;font-size:9px}} .rank{{display:grid;place-items:center;flex:0 0 27px;width:27px;height:27px;border-radius:7px;background:#e6edf1;color:#405768;font-size:10px}} .rank.gf{{background:#075943;color:#fff}}
    .valid-cell strong{{display:block;color:var(--green-deep);font-size:18px}} .valid-cell small{{display:block;margin-top:2px;color:#62786f;font-size:9px}} .bar{{display:block;height:5px;margin-top:4px;overflow:hidden;border-radius:999px;background:#cfe2da}} .bar i{{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#087457,#48a383)}} .performance-total{{background:#f1faf6}} .data-complete{{background:#e8f6ef;color:#075943}} .missing-benchmark{{background:#fff5e5;color:#a04b00}} .missing-position{{background:#f3edfa;color:#6840a0}} .stopped-value{{background:#f0f3f6;color:#465968}} .performance-gap{{color:#a82b24;background:#fff0ed;font-weight:900}} .total-cell{{color:#102f47;background:#edf3f7;font-weight:900}}
    .manager-head{{position:sticky;top:0;z-index:6;background:#fff}} .manager-head th{{position:static}} .manager-head .group-row th:first-child{{position:sticky;left:0;z-index:8}} .manager-body{{min-width:0}} .pinned-manager-row{{position:sticky;top:76px;z-index:5;display:grid;grid-template-columns:25fr 12fr 12fr 9fr 9fr 10fr 13fr 10fr;align-items:stretch;min-width:960px;height:62px;color:#314e43;font-size:13px;font-weight:800;box-shadow:0 2px 0 #79b69d,0 9px 18px rgba(19,73,54,.16)}} .pinned-manager-row>div{{display:flex;align-items:center;justify-content:flex-end;padding:9px 10px;background:#dff1e8}} .pinned-manager-row .name-cell{{position:sticky;left:0;z-index:2;justify-content:flex-start;background:#cfe9dc}} .pinned-manager-row .valid-cell{{display:block;text-align:right;background:#d8f0e4}} .pinned-manager-row .data-complete{{background:#cfeadc}} .pinned-manager-row .missing-benchmark{{background:#ffe8c6}} .pinned-manager-row .missing-position{{background:#e9def6}} .pinned-manager-row .stopped-value{{background:#e6ecef}} .pinned-manager-row .performance-gap{{color:#8f211d;background:#ffdcd7}} .pinned-manager-row .total-cell{{color:#102f47;background:#dce7ee;font-weight:900}}
    .foot{{margin:12px 2px 0;color:#7a8792;font-size:10px}}
    @media(max-width:1220px){{.table-wrap{{max-height:520px}}}}
    @media(max-width:1100px){{.status-board{{grid-template-columns:repeat(3,minmax(0,1fr))}}.performance-card{{grid-column:1/-1}}}}
    @media(max-width:720px){{.page{{padding:12px}}.hero{{align-items:stretch;flex-direction:column;padding:18px}}h1{{font-size:26px}}.sync{{min-width:0}}.status-board{{grid-template-columns:repeat(2,minmax(0,1fr))}}.performance-card{{grid-column:1/-1;grid-template-columns:1fr;gap:12px}}.metric-card{{min-height:102px;padding:14px}}.metric-breakdown{{grid-template-columns:1fr}}.metric-breakdown span+span{{border-top:1px solid #d0e5dc;border-left:0}}.total-card{{grid-column:1/-1}}.method-grid{{grid-template-columns:1fr}}.tablist{{display:grid;grid-template-columns:1fr 1fr}}.tab-btn{{padding:8px 7px}}.summary-line strong{{width:100%;margin-right:0}}.card{{border-radius:9px}}.table-wrap{{max-height:470px}}}}

    /* v2：策略规模优先，渠道与机构使用同一套简洁计数视图。 */
    body{{background:#f3f6f8;color:#173044}}
    .page{{max-width:1480px;padding:22px}}
    .dashboard-head{{display:flex;align-items:center;justify-content:space-between;gap:24px;margin-bottom:12px;padding:20px 22px;border:1px solid #d8e1e6;border-radius:12px;background:#fff}}
    .dashboard-head h1{{margin:7px 0 0;font-size:28px;line-height:1.2;letter-spacing:-.025em}}
    .dashboard-head .desc{{margin:7px 0 0;color:#607383;font-size:13px;line-height:1.6}}
    .preview-flag{{margin:0;border-color:#d8e2e6;background:#f6f9fa;color:#5d7380}}
    .sync{{min-width:230px;border-color:#d2dee3;border-left-color:#16846b;box-shadow:none}}
    .sync strong{{color:#183f4d;font-size:15px}}
    .overview-board{{display:grid;grid-template-columns:minmax(430px,1.25fr) minmax(460px,1fr);gap:12px;margin-bottom:12px}}
    .headline-card{{min-width:0;padding:22px 24px;border-radius:12px;background:linear-gradient(135deg,#123d4b,#1d5261);color:#fff;box-shadow:0 8px 22px rgba(18,61,75,.14)}}
    .headline-card .metric-label{{display:block;color:#cbe0e4;font-size:12px;font-weight:800;letter-spacing:.04em}}
    .headline-card>strong{{display:block;margin-top:6px;font-size:52px;line-height:1;font-variant-numeric:tabular-nums;letter-spacing:-.04em}}
    .headline-card>p{{margin:9px 0 17px;color:#d5e4e7;font-size:11px;line-height:1.55}}
    .composition-bar{{display:flex;height:9px;overflow:hidden;border-radius:999px;background:rgba(255,255,255,.16)}}
    .composition-bar i,.composition-bar b{{display:block;height:100%}}
    .composition-bar i{{background:#70d2ae}}
    .composition-bar b{{background:#9cabb5}}
    .composition-labels{{display:flex;flex-wrap:wrap;gap:14px;margin-top:9px;color:#e4eef0;font-size:10px}}
    .composition-labels span{{display:flex;align-items:center;gap:5px}}
    .composition-labels i{{width:7px;height:7px;border-radius:50%;background:#70d2ae}}
    .composition-labels span:last-child i{{background:#9cabb5}}
    .overview-metrics{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}
    .metric-tile{{min-width:0;padding:15px 16px;border:1px solid #d8e1e6;border-left:4px solid #7a8d99;border-radius:10px;background:#fff}}
    .metric-tile>span{{display:block;color:#657886;font-size:11px;font-weight:750}}
    .metric-tile>strong{{display:block;margin:5px 0 7px;color:#18384b;font-size:28px;line-height:1;font-variant-numeric:tabular-nums}}
    .metric-tile>small{{display:block;color:#7a8b97;font-size:10px;line-height:1.45}}
    .metric-tile.is-current{{border-left-color:#16846b}}
    .metric-tile.is-performance{{border-left-color:#4a8294}}
    .metric-tile.is-channel{{border-left-color:#2f7183}}
    .metric-tile.is-manager{{border-left-color:#53699d}}
    .method-details{{margin-bottom:12px;border-color:#d8e1e6;border-radius:10px;background:#fff;color:#5f707e}}
    .method-details summary{{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:12px;padding:11px 14px;color:#315667}}
    .method-details summary em{{justify-self:end;color:#758793;font-size:10px;font-style:normal;font-weight:650}}
    .method-details summary::after{{align-self:center}}
    .method-details summary:hover{{background:#f7fafb}}
    .method-grid{{grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;padding:0 12px 12px}}
    .method-grid article{{border-color:#e2e8eb;border-radius:8px;background:#fafcfc}}
    .tablist{{display:inline-flex;margin:0 0 10px;padding:4px;border-color:#d2dde2;border-radius:9px;background:#e9eef1}}
    .tab-btn{{min-width:150px;min-height:40px;padding:7px 18px;border-radius:6px;color:#637581}}
    .tab-btn:focus-visible{{outline:3px solid rgba(22,132,107,.25);outline-offset:1px}}
    .tab-btn.is-active{{border-color:#b7d2ca;color:#0d684f;box-shadow:0 2px 6px rgba(20,73,60,.08)}}
    .card{{border-color:#d4dee3;border-radius:10px;box-shadow:none}}
    .card-head,.card.manager .card-head{{padding:14px 16px 11px;border-bottom:0;background:#fff}}
    .card-head h2{{margin:0;color:#1c3b4c;font-size:17px}}
    .card-head p{{margin:4px 0 0;color:#758691;font-size:10px}}
    .summary-line{{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:9px 16px;border-top:1px solid #e3e9ec;border-bottom:1px solid #dbe4e8;background:#f7f9fa}}
    .summary-line>div:first-child strong{{display:block;color:#244555;font-size:12px}}
    .summary-line>div:first-child small{{display:block;margin-top:2px;color:#81909a;font-size:9px}}
    .summary-totals{{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:7px}}
    .summary-line .summary-totals span{{padding:3px 7px;border:1px solid #dce4e8;border-radius:6px;background:#fff;color:#6b7c87;font-size:9px;font-weight:650}}
    .summary-totals b{{margin-left:3px;color:#173c4d;font-size:11px}}
    .table-wrap{{max-height:600px}}
    .table-content,table{{min-width:980px}}
    table{{table-layout:fixed}}
    th,td{{padding:9px 12px}}
    th{{height:52px;background:#edf2f4;color:#385363;font-size:10px;line-height:1.3}}
    th>span{{display:block;margin-top:3px;color:#7b8c97;font-size:8px;font-weight:650}}
    thead th{{top:0}}
    thead th:first-child{{position:sticky;left:0;z-index:8;text-align:left}}
    td{{height:62px;background:#fff;color:#4c606e;font-size:12px}}
    tbody td:first-child{{z-index:3}}
    tbody tr:hover td{{background:#f8fbfb;filter:none}}
    tbody tr.has-performance-gap td:first-child{{box-shadow:inset 3px 0 0 #d6924a}}
    tbody tr.is-guangfa td:first-child{{background:#edf8f3}}
    .name-cell strong{{color:#203a49;font-size:12px}}
    .name-cell small{{color:#33745f}}
    .rank{{background:#edf2f4;color:#607481}}
    .rank.gf{{background:#13765d}}
    td.current-cell,td.total-cell{{background:#f3faf7}}
    td.stopped-cell{{background:#f7f9fa}}
    td.current-cell strong,td.performance-cell strong,td.complete-cell strong,td.stopped-cell strong,td.total-cell strong{{display:block;color:#254454;font-size:17px;line-height:1.15}}
    td.current-cell strong{{color:#0d7357}}
    td.total-cell strong{{color:#183c4d}}
    td small{{display:block;margin-top:4px;color:#85939c;font-size:8px;font-weight:600;line-height:1.35}}
    .bar{{height:4px;margin-top:5px;background:#d9e8e2}}
    .bar i{{background:#2a9677}}
    .foot{{margin-top:10px;color:#82919a}}
    @media(max-width:1040px){{.overview-board{{grid-template-columns:1fr}}.method-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
    @media(max-width:720px){{
      .page{{padding:12px}}
      .dashboard-head{{align-items:stretch;flex-direction:column;gap:14px;padding:17px}}
      .dashboard-head h1{{font-size:25px}}
      .sync{{min-width:0}}
      .headline-card{{padding:19px}}
      .headline-card>strong{{font-size:44px}}
      .overview-metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}
      .metric-tile{{padding:13px}}
      .metric-tile>strong{{font-size:24px}}
      .method-details summary{{grid-template-columns:minmax(0,1fr) auto}}
      .method-details summary em{{grid-column:1/-1;justify-self:start}}
      .method-grid{{grid-template-columns:1fr}}
      .tablist{{display:grid;grid-template-columns:1fr 1fr;width:100%}}
      .tab-btn{{min-width:0;padding:7px}}
      .summary-line{{align-items:flex-start;flex-direction:column}}
      .summary-totals{{justify-content:flex-start}}
      .table-wrap{{max-height:480px}}
    }}
    @media(max-width:430px){{.composition-labels{{gap:8px}}.metric-tile>small{{font-size:9px}}}}
  </style>
</head>
<body>
  <main class="page" aria-labelledby="dashboard-title">
    <header class="dashboard-head">
      <div><span class="preview-flag">隔离预览 · 未并入现有发布集</span><h1 id="dashboard-title">策略机构总览</h1><p class="desc">一眼看清当前系统策略规模，并按销售渠道和投顾管理机构查看分布。</p></div>
      <div class="sync"><small>最近一次数据同步</small><strong>{html.escape(str(refresh_time))}</strong><span>数据更新至 {html.escape(str(data_through))}</span></div>
    </header>
    <section class="overview-board" aria-label="当前系统策略规模">
      <article class="headline-card" data-metric="strategy-total">
        <span class="metric-label">当前系统策略总数</span>
        <strong>{fmt(channel_totals['total'])}</strong>
        <p>正式策略包中的策略记录；渠道与机构两个视角均已对账。</p>
        <div class="composition-bar" role="img" aria-label="当前策略 {fmt(channel_totals['current'])} 条，占 {current_rate:.1f}%；历史或已终止策略 {fmt(channel_totals['stopped'])} 条，占 {stopped_rate:.1f}%">
          <i style="width:{current_rate:.2f}%"></i><b style="width:{stopped_rate:.2f}%"></b>
        </div>
        <div class="composition-labels"><span><i></i>当前 {fmt(channel_totals['current'])}（{current_rate:.1f}%）</span><span><i></i>历史／已终止 {fmt(channel_totals['stopped'])}（{stopped_rate:.1f}%）</span></div>
      </article>
      <div class="overview-metrics">
        <article class="metric-tile is-current" data-metric="strategy-current"><span>当前策略</span><strong>{fmt(channel_totals['current'])}</strong><small>未命中终止或历史留档规则</small></article>
        <article class="metric-tile is-performance" data-metric="performance-complete"><span>可评价业绩</span><strong>{fmt(channel_totals['performanceComplete'])}</strong><small>当前策略中，待补业绩 {fmt(channel_totals['performanceMissing'])} 条</small></article>
        <article class="metric-tile is-channel" data-metric="channel-count"><span>销售渠道</span><strong>{fmt(len(channels))}</strong><small>每条策略归属一个销售／展示渠道</small></article>
        <article class="metric-tile is-manager" data-metric="manager-count"><span>管理机构</span><strong>{fmt(len(managers))}</strong><small>按投顾机构标准名称归集</small></article>
      </div>
    </section>
    <details class="method-details">
      <summary><span>口径与质量明细</span><em>数据完整 {fmt(channel_totals['dataComplete'])} · 无基准 {fmt(channel_totals['missingBenchmark'])} · 无仓位 {fmt(channel_totals['missingPosition'])}</em></summary>
      <div class="method-grid">
        <article><strong>当前策略</strong><p>系统策略总数扣除已终止或历史留档策略。当前策略进一步拆为“可评价业绩”和“待补业绩”，两者相加等于当前策略数。</p></article>
        <article><strong>业绩完整</strong><p>先排除已终止策略，同时要求“官方披露业绩”质检为完整。最新业绩日期（缺失时用收益数据截至）距全库最新数据日为 <b>0—5个自然日</b>时，视为能够绘制当前连续业绩曲线。</p></article>
        <article><strong>数据完整</strong><p>仅在业绩完整策略中统计，并且必须同时具备基准文本、基准曲线、完整历史调仓质检、最新持仓日和大于0的持仓基金数。</p></article>
        <article><strong>无基准</strong><p>仅在业绩完整策略中统计。业绩基准文本为空，或基准可用状态不是“文本+曲线”，均计为无基准；仅有文本或仅有曲线也属于基准不完整。</p></article>
        <article><strong>无仓位</strong><p>仅在业绩完整策略中统计。历史调仓质检不是完整，或缺少最新持仓日，或持仓基金数不大于0，任一条件命中即计为无仓位。</p></article>
        <article><strong>已终止</strong><p>命中是否已停止、历史接口留档，或治理/运作/展示状态中的已停止、已终止、已下架、已清盘、期满、已止盈等规则。</p></article>
        <article><strong>业绩缺失</strong><p>未终止但不满足“业绩完整”条件的策略，包括官方业绩质检不完整、没有业绩日期、业绩落后超过5天或日期晚于全库最新数据日；这些策略没有可用于当前评价的连续官方披露曲线。</p></article>
        <article><strong>策略总数</strong><p>业绩完整、已终止和业绩缺失为互斥主分类，三者之和等于该销售渠道或投顾管理人的策略总数。</p></article>
        <article><strong>子项关系</strong><p>数据完整、无基准、无仓位均属于“业绩完整”范围；无基准和无仓位可以同时命中，因此这三个子项不能直接相加代替业绩完整总数。</p></article>
      </div>
      <p class="method-foot">当前页面使用全库最新数据日 {html.escape(latest_data_date.isoformat())}，5天阈值按自然日计算。</p>
    </details>
    <section class="tab-shell">
      <div class="tablist" role="tablist" aria-label="机构统计视角">
        <button class="tab-btn is-active" id="channel-tab" role="tab" aria-selected="true" aria-controls="channel-panel" data-panel="channel-panel">销售渠道 <span>{fmt(len(channels))}</span></button>
        <button class="tab-btn" id="manager-tab" role="tab" aria-selected="false" aria-controls="manager-panel" data-panel="manager-panel" tabindex="-1">管理机构 <span>{fmt(len(managers))}</span></button>
      </div>
      <section class="tabpanel" id="channel-panel" role="tabpanel" aria-labelledby="channel-tab"><article class="card channel"><div class="card-head"><div><h2>销售渠道分布</h2><p>策略所在的销售或展示渠道</p></div></div>{summary_line(channels, '销售渠道')}{table_html(channels, name_header='销售渠道', pin_guangfa=False, system_total=channel_totals['total'])}</article></section>
      <section class="tabpanel" id="manager-panel" role="tabpanel" aria-labelledby="manager-tab" hidden><article class="card manager"><div class="card-head"><div><h2>管理机构分布</h2><p>负责管理或提供投顾服务的标准机构</p></div></div>{summary_line(managers, '管理机构')}{table_html(managers, name_header='投顾管理机构', pin_guangfa=True, system_total=manager_totals['total'])}</article></section>
    </section>
    <p class="foot">预览生成于 {html.escape(generated_at)}；本页仅用于确认视觉和指标口径，不写入导航、部署清单或 GitHub 发布集。</p>
  </main>
  <script>
    window.__INSTITUTION_PREVIEW_AUDIT__ = {audit_json};
    const tabButtons = Array.from(document.querySelectorAll('.tab-btn'));
    function activateTab(button, moveFocus = false) {{
      tabButtons.forEach((item) => {{
        const active = item === button;
        item.classList.toggle('is-active', active);
        item.setAttribute('aria-selected', String(active));
        item.tabIndex = active ? 0 : -1;
      }});
      document.querySelectorAll('.tabpanel').forEach((panel) => {{ panel.hidden = panel.id !== button.dataset.panel; }});
      if (moveFocus) button.focus();
    }}
    tabButtons.forEach((button, index) => {{
      button.addEventListener('click', () => activateTab(button));
      button.addEventListener('keydown', (event) => {{
        let nextIndex = null;
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (index + 1) % tabButtons.length;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (index - 1 + tabButtons.length) % tabButtons.length;
        if (event.key === 'Home') nextIndex = 0;
        if (event.key === 'End') nextIndex = tabButtons.length - 1;
        if (nextIndex === null) return;
        event.preventDefault();
        activateTab(tabButtons[nextIndex], true);
      }});
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    pack = args.strategy_pack.resolve()
    output = args.output.resolve()
    summary = load_summary(pack)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(summary, pack), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(output),
                "strategyCount": len(summary["strategies"]),
                "sourceDataThrough": (summary.get("overview") or {}).get("数据更新至"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
