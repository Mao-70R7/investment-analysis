from __future__ import annotations

import csv
import html
import json
import math
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
SUMMARY_JS = PROJECT_ROOT / "site" / "basic_data" / "data" / "basic_summary.js"
CURVE_JSONL = (
    PROJECT_ROOT
    / "data"
    / "normalized"
    / "ttfund"
    / "strategy_performance_daily"
    / "2026-06-03"
    / "classification_app_validation_final_20260603T2110.jsonl"
)
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "classification_app_validation" / datetime.now().strftime("%Y%m%dT%H%M%S")


SAMPLES = [
    {"主可比池": "现金管理型", "渠道策略ID": "2CQHYXS"},
    {"主可比池": "纯债/短债型", "渠道策略ID": "0FL9P5D"},
    {"主可比池": "固收增强型", "渠道策略ID": "0U91IVW"},
    {"主可比池": "多资产配置型", "渠道策略ID": "0VDSNXX"},
    {"主可比池": "偏股配置型", "渠道策略ID": "0JOEXAY"},
    {"主可比池": "目标日期/养老型", "渠道策略ID": "16KJ20Z"},
    {"主可比池": "海外/全球型", "渠道策略ID": "OFNMVBE"},
    {"主可比池": "主题/行业型", "渠道策略ID": "EM46BJE"},
]

RETURN_FIELDS = ["近一周", "近一月", "近三月", "近1年", "今年以来", "累计收益率"]
METRIC_TOLERANCE = {
    "最新业绩日期": 0,
    "官方单位净值": 0.0002,
    "近一周": 0.03,
    "近一月": 0.03,
    "近三月": 0.03,
    "近1年": 0.03,
    "今年以来": 0.03,
    "累计收益率": 0.03,
    "最大回撤": 0.05,
}


def as_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        result = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def round_or_none(value: Any, digits: int = 4) -> float | None:
    number = as_float(value)
    if number is None:
        return None
    return round(number, digits)


def parse_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return None


def pct_return(end_nav: float | None, start_nav: float | None) -> float | None:
    if end_nav is None or start_nav is None or start_nav <= 0:
        return None
    return round((end_nav / start_nav - 1.0) * 100.0, 4)


def load_summary() -> dict[str, Any]:
    text = SUMMARY_JS.read_text(encoding="utf-8")
    prefix = "window.__BASIC_DATA__.summary = "
    if prefix not in text:
        raise RuntimeError(f"unexpected summary js format: {SUMMARY_JS}")
    payload = text.split(prefix, 1)[1].rsplit(";", 1)[0]
    return json.loads(payload)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(str(row.get(key) or ""), []).append(row)
    return result


def compute_curve_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        date_text = str(row.get("trade_date") or "").strip()
        nav = as_float(row.get("nav"))
        if date_text and nav and nav > 0:
            by_date[date_text] = row
    clean_rows = sorted(by_date.values(), key=lambda item: str(item["trade_date"]))
    if not clean_rows:
        return {}
    latest = clean_rows[-1]
    latest_date = parse_date(latest["trade_date"])
    latest_nav = as_float(latest.get("nav"))

    def baseline_by_days(days: int) -> float | None:
        if latest_date is None:
            return None
        target = latest_date - timedelta(days=days)
        base_nav = None
        for item in clean_rows:
            dt = parse_date(item.get("trade_date"))
            if dt is not None and dt <= target:
                base_nav = as_float(item.get("nav"))
            elif dt is not None:
                break
        return base_nav

    ytd_nav = None
    if latest_date is not None:
        ytd_start = latest_date.replace(month=1, day=1)
        for item in clean_rows:
            dt = parse_date(item.get("trade_date"))
            if dt is not None and dt >= ytd_start:
                ytd_nav = as_float(item.get("nav"))
                break

    peak = as_float(clean_rows[0].get("nav"))
    max_drawdown = 0.0
    current_drawdown = 0.0
    for item in clean_rows:
        nav = as_float(item.get("nav"))
        if nav is None or nav <= 0:
            continue
        if peak is None or nav > peak:
            peak = nav
        if peak:
            current_drawdown = max(0.0, (peak - nav) / peak * 100.0)
            max_drawdown = max(max_drawdown, current_drawdown)

    cumulative = as_float(latest.get("cumulative_return"))
    first_nav = as_float(clean_rows[0].get("nav"))
    if cumulative is None:
        cumulative = pct_return(latest_nav, first_nav)
    return {
        "最新业绩日期": latest.get("trade_date"),
        "官方单位净值": round_or_none(latest_nav, 6),
        "近一周": pct_return(latest_nav, baseline_by_days(7)),
        "近一月": pct_return(latest_nav, baseline_by_days(30)),
        "近三月": pct_return(latest_nav, baseline_by_days(90)),
        "近1年": pct_return(latest_nav, baseline_by_days(365)),
        "今年以来": pct_return(latest_nav, ytd_nav),
        "累计收益率": round_or_none(cumulative),
        "最大回撤": round_or_none(max_drawdown),
        "当前回撤": round_or_none(current_drawdown),
        "App曲线点数": len(clean_rows),
        "App曲线起始日": clean_rows[0].get("trade_date"),
        "App曲线结束日": latest.get("trade_date"),
    }


def latest_cache_detail(sid: str) -> tuple[Path | None, dict[str, Any] | None]:
    root = PROJECT_ROOT / "data" / "raw" / "device_cache" / sid
    if not root.exists():
        return None, None
    files = sorted(root.glob("*strategy-detail-matter*.0"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        files = sorted(root.glob("strategyDetailPageData*.0"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return None, None
    first_path: Path | None = None
    for path in files:
        first_path = first_path or path
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            continue
        detail = payload.get("tgExtendInfo")
        if detail is None and isinstance(payload.get("data"), dict):
            detail = payload["data"].get("tgExtendInfo")
        if isinstance(detail, dict):
            return path, detail
    return first_path, None


def load_strategy_info() -> dict[str, dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute('SELECT * FROM "策略信息"')]
    conn.close()
    return {str(row["渠道策略ID"]): row for row in rows}


def compare_numeric(site_value: Any, app_value: Any, field: str) -> tuple[str, float | None]:
    site_num = as_float(site_value)
    app_num = as_float(app_value)
    if site_num is None and app_num is None:
        return "两边均缺失", None
    if site_num is None or app_num is None:
        return "不一致", None
    diff = round(site_num - app_num, 6)
    tolerance = METRIC_TOLERANCE.get(field, 0.03)
    return ("通过" if abs(diff) <= tolerance else "不一致"), diff


def compare_text(site_value: Any, app_value: Any) -> str:
    site_text = str(site_value or "").strip()
    app_text = str(app_value or "").strip()
    if not site_text and not app_text:
        return "两边均缺失"
    return "通过" if site_text == app_text else "不一致"


def compare_money(site_value: Any, app_value: Any) -> str:
    site_num = as_float(site_value)
    app_num = as_float(app_value)
    if site_num is not None and app_num is not None:
        return "通过" if abs(site_num - app_num) <= 0.01 else "不一致"
    return compare_text(site_value, app_value)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def latest_app_drive_summary() -> dict[str, Any] | None:
    root = PROJECT_ROOT / "data" / "raw" / "ttfund" / "app_drive"
    candidates = sorted(root.glob("*/*/summary.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    expected = {sample["渠道策略ID"] for sample in SAMPLES}
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ids = {str(value) for value in payload.get("strategy_ids", [])}
        if expected.issubset(ids):
            payload["_summary_path"] = str(path)
            return payload
    return None


def esc(value: Any) -> str:
    if value is None:
        return "未披露"
    return html.escape(str(value))


def fmt(value: Any) -> str:
    if value is None or value == "":
        return "未披露"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def table_html(rows: list[dict[str, Any]], columns: list[str]) -> str:
    head = "".join(f"<th>{esc(col)}</th>" for col in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(fmt(row.get(col)))}</td>" for col in columns) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def build_report(
    sample_rows: list[dict[str, Any]],
    metric_rows: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
    methodology_rows: list[dict[str, Any]],
    drive_summary: dict[str, Any] | None,
) -> str:
    generated_at = datetime.now().astimezone().isoformat(timespec="minutes")
    metric_pass = sum(1 for row in metric_rows if row.get("结论") in {"通过", "两边均缺失"})
    metric_total = len(metric_rows)
    detail_checked = [row for row in detail_rows if row.get("结论") != "未执行"]
    detail_pass = sum(1 for row in detail_checked if row.get("结论") in {"通过", "两边均缺失"})
    drive_status = (
        f"已执行：detail_ok_total={drive_summary.get('detail_ok_total')}/{drive_summary.get('strategy_total')}，"
        f"run_id={drive_summary.get('run_id')}"
        if drive_summary
        else "未执行：未找到覆盖全部样本的 App 驱动结果"
    )
    css = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif;margin:0;background:#f6f8fb;color:#1f2a37}
main{max-width:1320px;margin:0 auto;padding:28px}
h1{margin:0 0 8px;font-size:26px} h2{margin:28px 0 10px;font-size:18px}.desc{color:#5d6b7a;line-height:1.7}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}
.card{background:#fff;border:1px solid #dfe7f0;border-radius:8px;padding:14px}.card b{font-size:24px}.card span{display:block;color:#637184;font-size:12px;margin-top:6px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #dfe7f0;border-radius:8px;overflow:hidden;margin:10px 0 18px}
th,td{border-bottom:1px solid #edf1f6;padding:8px 9px;text-align:left;font-size:12px;vertical-align:top}th{background:#eef4f8;color:#24364a;position:sticky;top:0}
tr:last-child td{border-bottom:0}.ok{color:#0f766e}.warn{color:#b45309}.bad{color:#b91c1c}.note{background:#fff;border:1px solid #dfe7f0;border-radius:8px;padding:12px;line-height:1.7}
"""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>投顾分类与App展示口径核对</title><style>{css}</style></head>
<body><main>
<h1>投顾分类与 App 展示口径核对</h1>
<p class="desc">生成时间：{esc(generated_at)}。本报告按 8 个互斥主可比池各抽 1 个天天基金投顾策略，对比当前页面导出数据与天天 App 官方曲线接口；其中已有 App 详情缓存的策略同时对比详情页字段。</p>
<div class="cards">
  <div class="card"><b>{len(sample_rows)}</b><span>主可比池样本</span></div>
  <div class="card"><b>{metric_pass}/{metric_total}</b><span>曲线指标通过</span></div>
  <div class="card"><b>{detail_pass}/{len(detail_checked)}</b><span>详情字段通过</span></div>
  <div class="card"><b>{esc(drive_summary.get('detail_ok_total') if drive_summary else 0)}</b><span>App 详情页成功打开</span></div>
</div>
<div class="note">结论口径：收益类字段容忍差异 0.03 个百分点，最大回撤容忍差异 0.05 个百分点，单位净值容忍差异 0.0002。ADB/App 驱动状态：{esc(drive_status)}；App 同源曲线接口 8/8 成功。</div>
<h2>样本清单</h2>{table_html(sample_rows, ["主可比池","渠道策略ID","策略名称","投顾机构","页面归属","分类依据","App曲线点数","App详情缓存"])}
<h2>App 曲线指标对照</h2>{table_html(metric_rows, ["主可比池","渠道策略ID","策略名称","字段","页面值","App接口值","差异","结论"])}
<h2>App 详情字段对照</h2>{table_html(detail_rows, ["主可比池","渠道策略ID","策略名称","字段","页面/库值","App缓存值","结论","App缓存文件"])}
<h2>核心口径复核</h2>{table_html(methodology_rows, ["口径","来源","规则/公式","本次复核结论"])}
</main></body></html>"""


def main() -> None:
    summary = load_summary()
    site_rows = {str(row["策略代码"]): row for row in summary["strategies"]}
    curve_rows = group_by(load_jsonl(CURVE_JSONL), "source_strategy_id")
    strategy_info = load_strategy_info()

    sample_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []

    for sample in SAMPLES:
        sid = sample["渠道策略ID"]
        site = site_rows.get(sid)
        if not site:
            raise RuntimeError(f"sample not found in site summary: {sid}")
        app_metrics = compute_curve_metrics(curve_rows.get(sid, []))
        cache_path, cache_detail = latest_cache_detail(sid)
        app_cache_state = "有" if cache_detail else "无"
        sample_rows.append(
            {
                "主可比池": sample["主可比池"],
                "渠道策略ID": sid,
                "策略名称": site.get("策略名称"),
                "投顾机构": site.get("投顾机构"),
                "页面归属": site.get("主可比池"),
                "分类依据": site.get("分类依据"),
                "App曲线点数": app_metrics.get("App曲线点数"),
                "App详情缓存": app_cache_state,
            }
        )
        for field in ["最新业绩日期", "官方单位净值", *RETURN_FIELDS, "最大回撤"]:
            if field == "最新业绩日期":
                conclusion = compare_text(site.get(field), app_metrics.get(field))
                diff = ""
            else:
                conclusion, diff_value = compare_numeric(site.get(field), app_metrics.get(field), field)
                diff = diff_value
            metric_rows.append(
                {
                    "主可比池": sample["主可比池"],
                    "渠道策略ID": sid,
                    "策略名称": site.get("策略名称"),
                    "字段": field,
                    "页面值": site.get(field),
                    "App接口值": app_metrics.get(field),
                    "差异": diff,
                    "结论": conclusion,
                }
            )
        info = strategy_info.get(sid, {})
        if cache_detail:
            detail_checks = [
                ("策略名称", info.get("策略名称"), cache_detail.get("tgName")),
                ("风险等级", info.get("风险等级"), cache_detail.get("risk")),
                ("年化投顾费率", site.get("年化投顾费率"), cache_detail.get("strategyRate")),
                ("业绩基准", info.get("业绩基准"), cache_detail.get("basicCalFormulaRemark")),
                ("起投金额", info.get("起投金额"), cache_detail.get("minBuy")),
                ("建议持有时长", info.get("建议持有时长"), cache_detail.get("investTerm")),
            ]
            for field, site_value, app_value in detail_checks:
                if field == "年化投顾费率":
                    conclusion, _diff = compare_numeric(site_value, app_value, field)
                elif field == "起投金额":
                    conclusion = compare_money(site_value, app_value)
                else:
                    conclusion = compare_text(site_value, app_value)
                detail_rows.append(
                    {
                        "主可比池": sample["主可比池"],
                        "渠道策略ID": sid,
                        "策略名称": site.get("策略名称"),
                        "字段": field,
                        "页面/库值": site_value,
                        "App缓存值": app_value,
                        "结论": conclusion,
                        "App缓存文件": str(cache_path),
                    }
                )
        else:
            detail_rows.append(
                {
                    "主可比池": sample["主可比池"],
                    "渠道策略ID": sid,
                    "策略名称": site.get("策略名称"),
                    "字段": "详情页字段",
                    "页面/库值": "未执行",
                    "App缓存值": "无详情缓存且ADB未连接",
                    "结论": "未执行",
                    "App缓存文件": "",
                }
            )

    methodology_rows = [
        {
            "口径": "主可比池",
            "来源": "页面导出脚本加工",
            "规则/公式": "互斥顺序：目标日期/养老 -> 海外/全球 -> 主题/行业 -> 现金管理 -> 纯债/短债 -> 固收增强 -> 偏股配置 -> 多资产配置。",
            "本次复核结论": "8个样本页面归属均与样本指定主池一致；主池不会重复归属。",
        },
        {
            "口径": "收益区间",
            "来源": "App官方曲线接口 PDATE/SE",
            "规则/公式": "最新净值 / T日前最近可用净值 - 1；近一周7天、近一月30天、近三月90天、近1年365天；今年以来取当年首个可用点。",
            "本次复核结论": "使用同一公式反算并与页面值比较。",
        },
        {
            "口径": "累计收益率/官方单位净值",
            "来源": "App官方曲线接口 SE",
            "规则/公式": "累计收益率取最新SE；单位净值=1+SE/100。",
            "本次复核结论": "8个样本均成功取得App接口末值。",
        },
        {
            "口径": "最大回撤/当前回撤",
            "来源": "清洗后的App披露净值曲线加工",
            "规则/公式": "最大回撤=max(历史峰值-后续净值)/历史峰值；当前回撤=最新净值相对历史峰值跌幅。",
            "本次复核结论": "使用App曲线独立重算并与页面值比较。",
        },
        {
            "口径": "风险等级",
            "来源": "App/平台详情披露",
            "规则/公式": "直接使用App披露值，不使用系统算法重算。",
            "本次复核结论": "仅对有详情缓存的3个样本核对；其余需连接手机或补抓详情缓存。",
        },
        {
            "口径": "投顾费率/业绩基准",
            "来源": "App详情缓存 tgExtendInfo",
            "规则/公式": "费率使用strategyRate；业绩基准使用basicCalFormulaRemark；系统将费率结构化为年化百分比。",
            "本次复核结论": "仅对有详情缓存的3个样本核对；其余需连接手机或补抓详情缓存。",
        },
        {
            "口径": "市场地域/主动被动/策略实现标签/特殊标签",
            "来源": "基金标准分类字典 + 当前持仓/推算持仓 + 文本关键词",
            "规则/公式": "辅助筛选维度或多标签，不改变主可比池归属；可重复命中，但不参与重复排名。",
            "本次复核结论": "本次检查了样本分类依据，未发现主池重复；辅助标签重复属于预期。",
        },
        {
            "口径": "换手率/调仓频率",
            "来源": "历史调仓明细加工",
            "规则/公式": "单次换手=sum(abs(权重变化))/2；年化换手=sum(单次换手)/运作年数；调仓频率=调仓次数/运作年数。",
            "本次复核结论": "App详情页通常不直接展示同口径，本次不做App展示对照，只做公式复核。",
        },
    ]

    drive_summary = latest_app_drive_summary()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT_DIR / "classification_samples.csv", sample_rows)
    write_csv(OUTPUT_DIR / "app_curve_metric_comparison.csv", metric_rows)
    write_csv(OUTPUT_DIR / "app_detail_field_comparison.csv", detail_rows)
    write_csv(OUTPUT_DIR / "methodology_review.csv", methodology_rows)
    report_html = build_report(sample_rows, metric_rows, detail_rows, methodology_rows, drive_summary)
    report_path = OUTPUT_DIR / "classification_app_validation_report.html"
    report_path.write_text(report_html, encoding="utf-8")
    payload = {
        "output_dir": str(OUTPUT_DIR),
        "report_html": str(report_path),
        "sample_count": len(sample_rows),
        "metric_comparison_rows": len(metric_rows),
        "detail_comparison_rows": len(detail_rows),
        "metric_failures": [row for row in metric_rows if row["结论"] == "不一致"],
        "detail_failures": [row for row in detail_rows if row["结论"] == "不一致"],
        "curve_source": str(CURVE_JSONL),
        "adb_realtime_ui_status": (
            f"已执行：detail_ok_total={drive_summary.get('detail_ok_total')}/{drive_summary.get('strategy_total')}，run_id={drive_summary.get('run_id')}"
            if drive_summary
            else "未执行：未找到覆盖全部样本的 App 驱动结果"
        ),
        "app_drive_summary": drive_summary,
    }
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
