# -*- coding: utf-8 -*-
"""Build the external fund/Guangfa reconciliation workbook."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import xlsxwriter


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = ROOT / "data" / "analysis_zh_current.sqlite"
FORMAL_ROOT = PROJECT_ROOT / "site"
DEFAULT_SOURCE = FORMAL_ROOT / "reports" / "advisor_public_fund_mixed_performance_20260630" / "workbook_source.json"
DEFAULT_OUTPUT = FORMAL_ROOT / "reports" / "基金与广发策略外部数据差异核对_截至20260630.xlsx"


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_rows(source: Path) -> list[dict[str, Any]]:
    return json.loads(source.read_text(encoding="utf-8-sig")).get("rows") or []


def fund_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    query = '''
    SELECT e."基金代码", e."基金名称" AS "外部基金名称",
           s."基金名称" AS "本地基金名称", s."基金公司",
           m."主基金代码", m."主基金名称", m."是否主份额", m."份额类别", m."计价币种", m."合并份额数",
           e."上半年复权收益率_百分比" AS "外部上半年收益率_百分比",
           s."本地上半年收益率_百分比", s."上半年收益率_百分比" AS "报表上半年收益率_百分比",
           s."源端复爬上半年收益率_百分比", s."源端复爬收益来源", s."源端复爬复权口径",
           s."源端复爬窗口起始日", s."源端复爬窗口截止日", s."源端复爬净值点数",
           s."外部收益差异_百分点", s."源端复爬外部收益差异_百分点", s."源端复爬本地收益差异_百分点",
           s."外部收益核对状态", s."源端复爬收益核对状态", s."收益确认来源",
           e."业绩比较基准" AS "外部业绩比较基准", s."业绩比较基准" AS "报表业绩比较基准",
           s."基准风险资产权重", s."非权益比较轨道", s."正式可比池",
           s."基准权益权重_百分比", s."基准债券权重_百分比", s."基准货币权重_百分比",
           s."基准商品权重_百分比", s."基准另类权重_百分比", s."基准未知权重_百分比",
           s."上半年最大回撤_百分比", s."上半年年化波动率_百分比"
    FROM "外部基金0630核对" e
    LEFT JOIN "公募基金产品绩效快照" s ON s."基金代码" = e."基金代码"
    LEFT JOIN "基金主份额映射" m ON m."基金代码" = e."基金代码"
    ORDER BY e."基金代码"
    '''
    return [dict(row) for row in conn.execute(query)]


def mapping_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute('SELECT * FROM "基金主份额映射" WHERE "是否主份额"=1 ORDER BY "主基金代码"')]


def strategy_rows(source_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "产品代码", "产品名称", "机构", "本地策略匹配状态", "业绩比较基准",
        "基准风险资产权重", "非权益比较轨道", "正式可比池",
        "外部上半年收益率", "源端复爬上半年收益率", "官方净值重算上半年收益率", "外部官方收益差异_百分点",
        "源端复爬外部收益差异_百分点", "源端复爬收益来源", "外部收益核对状态",
        "上半年收益率", "上半年收益来源", "基准上半年收益率",
        "上半年最大回撤", "上半年年化波动率", "最新业绩日期",
    ]
    return [{field: row.get(field) for field in fields} for row in source_rows if row.get("渠道") == "广发基金"]


def anomaly_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    query = '''
    SELECT "基金代码", "基金名称", "基金公司", "业绩比较基准",
           "基准风险资产权重", "非权益比较轨道", "正式可比池", "可比池样本资格", "可比池说明",
           "基准权益权重_百分比", "基准债券权重_百分比", "基准货币权重_百分比",
           "基准商品权重_百分比", "基准另类权重_百分比", "基准未知权重_百分比",
           "基准互斥权重合计_百分比", "基准解析说明"
    FROM "公募基金产品绩效快照"
    WHERE COALESCE("基准风险资产权重", '') = '' OR COALESCE("可比池样本资格", '否') <> '是'
       OR ABS(COALESCE("基准互斥权重合计_百分比", 0) - 100) > 0.01
    ORDER BY "基金代码"
    '''
    return [dict(row) for row in conn.execute(query)]


def summary_rows(funds: list[dict[str, Any]], strategies: list[dict[str, Any]], anomalies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fund_status = Counter(clean(row.get("外部收益核对状态")) or "未标识" for row in funds)
    strategy_status = Counter(clean(row.get("外部收益核对状态")) or "未标识" for row in strategies)
    return [
        {"项目": "外部基金记录数", "值": len(funds), "说明": "副本全部基金0630.xlsx有效代码"},
        {"项目": "基金收益核对状态", "值": json.dumps(dict(fund_status), ensure_ascii=False), "说明": "差异超过0.05个百分点时不沿用本地风险指标"},
        {"项目": "外部广发策略记录数", "值": len(strategies), "说明": "原始代码全部保留，不按同名策略合并"},
        {"项目": "广发收益核对状态", "值": json.dumps(dict(strategy_status), ensure_ascii=False), "说明": "与官方披露单位净值重算差异不超过0.01个百分点视为一致"},
        {"项目": "基准分类待处理记录数", "值": len(anomalies), "说明": "未分档、未进入正式可比池或互斥权重不等于100%的记录"},
    ]


def headers(rows: list[dict[str, Any]]) -> list[str]:
    return list(rows[0].keys()) if rows else ["说明"]


def write_sheet(workbook: xlsxwriter.Workbook, name: str, rows: list[dict[str, Any]], color: str) -> None:
    columns = headers(rows)
    sheet = workbook.add_worksheet(name)
    sheet.hide_gridlines(2)
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, max(1, len(rows)), len(columns) - 1)
    head = workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": color, "border": 1, "text_wrap": True, "font_name": "Microsoft YaHei"})
    text_fmt = workbook.add_format({"font_name": "Microsoft YaHei", "font_size": 10})
    num_fmt = workbook.add_format({"font_name": "Microsoft YaHei", "font_size": 10, "num_format": "0.000000"})
    pct_fmt = workbook.add_format({"font_name": "Microsoft YaHei", "font_size": 10, "num_format": "0.00%"})
    for col, column in enumerate(columns):
        sheet.write(0, col, column, head)
        width = 56 if "基准" in column or "说明" in column else 28 if "名称" in column or "来源" in column else 16
        sheet.set_column(col, col, width)
    for ridx, row in enumerate(rows, 1):
        for cidx, column in enumerate(columns):
            value = row.get(column)
            fmt = pct_fmt if column in {"外部上半年收益率", "官方净值重算上半年收益率", "上半年最大回撤", "上半年年化波动率"} else num_fmt if isinstance(value, (int, float)) else text_fmt
            if value is None:
                sheet.write_blank(ridx, cidx, None, fmt)
            elif isinstance(value, (int, float)):
                sheet.write_number(ridx, cidx, value, fmt)
            else:
                sheet.write_string(ridx, cidx, clean(value), fmt)


def build(db_path: Path, source_path: Path, output_path: Path) -> dict[str, Any]:
    source_rows = load_rows(source_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        funds = fund_rows(conn)
        mappings = mapping_rows(conn)
        anomalies = anomaly_rows(conn)
    finally:
        conn.close()
    strategies = strategy_rows(source_rows)
    summaries = summary_rows(funds, strategies, anomalies)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlsxwriter.Workbook(str(output_path), {"constant_memory": True, "strings_to_urls": False})
    try:
        write_sheet(workbook, "总览", summaries, "#334155")
        write_sheet(workbook, "基金收益与基准核对", funds, "#0F766E")
        write_sheet(workbook, "广发策略核对", strategies, "#B91C1C")
        write_sheet(workbook, "主份额映射", mappings, "#155E75")
        write_sheet(workbook, "基准分类待处理", anomalies, "#92400E")
    finally:
        workbook.close()
    return {"output": str(output_path), "fund_rows": len(funds), "strategy_rows": len(strategies), "primary_rows": len(mappings), "anomaly_rows": len(anomalies)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.db, args.source, args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
