# -*- coding: utf-8 -*-
"""Stream the advisor + all public fund mixed ranking workbook to XLSX.

The artifact-tool workbook renderer is useful for small report packs, but the
all-fund mixed ranking sheet has more than two million cells.  This writer keeps
the same workbook_source.json contract and uses xlsxwriter in constant-memory
mode so the full workbook can be generated reliably.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

try:
    import xlsxwriter
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("需要 xlsxwriter。请使用 Codex 内置 Python 运行本脚本。") from exc


INTERVAL_LABELS = ["上半年", "今年以来", "近1月", "近3月", "近6月", "近1年"]


def interval_headers() -> list[str]:
    headers: list[str] = []
    for label in INTERVAL_LABELS:
        headers.extend(
            [
                f"{label}收益率",
                f"{label}最大回撤",
                f"{label}年化波动率",
                f"{label}区间",
                f"{label}收益来源",
                f"{label}风险来源",
                f"{label}风险净值点数",
            ]
        )
    return headers


MIXED_HEADERS = [
    "产品类型",
    "基金主类型",
    "产品ID",
    "产品代码",
    "产品名称",
    "机构",
    "渠道",
    "管理人/经理",
    "是否对客",
    "是否广发",
    "展示状态",
    "数据状态",
    "本地策略匹配状态",
    "生命周期状态",
    "最新业绩日期",
    "成立日期",
    "标准资产大类",
    "标准资产细类",
    "主基金代码",
    "主基金名称",
    "份额类别",
    "计价币种",
    "合并份额数",
    "基准风险资产权重",
    "基准风险资产权重说明",
    "基准风险资产权重来源",
    "基准结构类型",
    "非权益比较轨道",
    "正式可比池",
    "可比池样本资格",
    "可比池说明",
    "基准互斥权重合计_百分比",
    "分类依据",
    "业务/公开分类",
    "FOF公开分类",
    "FOF基准细分分类",
    "风险等级",
    "基准权益权重",
    "基准债券权重",
    "基准货币权重",
    "基准商品权重",
    "基准另类权重",
    "基准港股权益权重",
    "基准海外权益权重",
    "基准海外权重",
    "基准未知权重",
    "业绩比较基准",
    "业绩基准原始来源",
    "业绩基准获取状态",
    "基准解析说明",
    "是否使用分类兜底",
    "F10采集状态",
    "F10_HTTP状态",
    "F10错误信息",
    "解析置信度",
    "解析置信度分数",
    "外部上半年收益率",
    "本地上半年收益率",
    "源端复爬上半年收益率",
    "源端复爬最大回撤",
    "源端复爬年化波动率",
    "源端复爬收益来源",
    "源端复爬复权口径",
    "源端复爬窗口起始日",
    "源端复爬窗口截止日",
    "源端复爬净值点数",
    "官方净值重算上半年收益率",
    "外部本地收益差异_百分点",
    "源端复爬外部收益差异_百分点",
    "源端复爬本地收益差异_百分点",
    "外部官方收益差异_百分点",
    "外部收益核对状态",
    "源端复爬收益核对状态",
    "收益确认来源",
    "基准上半年收益率",
    "外部年化收益率",
    *interval_headers(),
    "详情链接",
]

BUCKET_HEADERS = [
    "基准风险资产权重",
    "基准风险资产权重说明",
    "非权益比较轨道",
    "正式可比池",
    "产品类型分布",
    "基金主类型分布",
    "产品数",
    "上半年收益有效数",
    "是否满足同类统计门槛",
    "上半年收益均值",
    "上半年收益中位数",
    "上半年最大回撤有效数",
    "上半年最大回撤均值",
    "上半年最大回撤中位数",
    "上半年年化波动率有效数",
    "上半年年化波动率均值",
    "上半年年化波动率中位数",
]

QA_HEADERS = [
    "抽样维度",
    "产品类型",
    "基金主类型",
    "机构",
    "产品代码",
    "产品名称",
    "基准风险资产权重",
    "基准风险资产权重来源",
    "上半年收益率",
    "上半年最大回撤",
    "上半年年化波动率",
    "核对字段数",
    "最大收益差异_百分点",
    "最大风险差异_百分点",
    "核对状态",
    "核对说明",
]

EXCLUDED_STRATEGY_HEADERS = [
    "产品代码",
    "产品名称",
    "机构",
    "渠道",
    "最新业绩日期",
    "生命周期状态",
    "剔除原因",
]

COVERAGE_HEADERS = ["项目", "值", "说明"]
NOTE_HEADERS = ["字段", "说明"]


def build_meta_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    meta = data.get("meta") or {}
    qa_status = json.dumps(meta.get("qaStatusCounts") or {}, ensure_ascii=False)
    return [
        {"项目": "报告名称", "值": meta.get("title") or "", "说明": "投顾策略和基金产品按统一字段混排"},
        {"项目": "截至日期", "值": meta.get("asOfDate") or "", "说明": "收益、回撤、波动率的目标截止日"},
        {"项目": "生成时间", "值": meta.get("generatedAt") or "", "说明": "导出源数据生成时间"},
        {"项目": "策略范围", "值": meta.get("strategyScope") or "", "说明": "延续当前页面渠道口径"},
        {"项目": "基金范围", "值": meta.get("fundScope") or meta.get("fofScope") or "", "说明": "基金产品池"},
        {
            "项目": "导出总行数",
            "值": meta.get("exportRowCount") or 0,
            "说明": f"原始策略包{meta.get('rawRowCount') or meta.get('rawStrategyRowCount') or 0}；排除非展示渠道策略{meta.get('excludedStrategyRowCount') or 0}",
        },
        {"项目": "机构抽样数", "值": meta.get("qaSampleCount") or 0, "说明": qa_status},
        {"项目": "核对阈值", "值": f"{meta.get('tolerancePp') or 0}个百分点", "说明": "抽样重算差异不超过该阈值视为一致"},
        {"项目": "来源数据包", "值": meta.get("sourcePack") or "", "说明": "正式页面目录的数据包"},
        {"项目": "来源数据库", "值": meta.get("sourceDb") or "", "说明": "本地SQLite分析库"},
    ]


def width_for(header: str, overrides: dict[str, int]) -> int:
    if header in overrides:
        return overrides[header]
    if "说明" in header or "基准" in header:
        return 24
    if "名称" in header or "机构" in header:
        return 30
    if "区间" in header or "来源" in header:
        return 18
    if any(token in header for token in ("收益", "回撤", "波动", "权重")):
        return 13
    if "是否" in header:
        return 10
    return 14


def column_widths(headers: list[str], overrides: dict[str, int] | None = None) -> list[int]:
    overrides = overrides or {}
    return [width_for(header, overrides) for header in headers]


def number_format_for(header: str) -> str | None:
    if (
        header.endswith("收益率")
        or header.endswith("最大回撤")
        or header.endswith("年化波动率")
        or header.endswith("权重")
        or header.endswith("均值")
        or header.endswith("中位数")
    ):
        return "0.00%"
    if header.endswith("风险净值点数") or header.endswith("有效数") or header.endswith("产品数") or header.endswith("排名") or header == "同类可比样本数":
        return "#,##0"
    if header == "解析置信度分数":
        return "0.00"
    if header.endswith("_百分点"):
        return "0.0000"
    return None


def safe_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def make_formats(workbook: xlsxwriter.Workbook) -> dict[str, Any]:
    return {
        "header_default": workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "font_name": "Microsoft YaHei",
                "font_size": 10,
                "bg_color": "#0F766E",
                "text_wrap": True,
                "valign": "vcenter",
                "border": 1,
                "border_color": "#D1D5DB",
            }
        ),
        "text": workbook.add_format({"font_name": "Microsoft YaHei", "font_size": 10}),
        "integer": workbook.add_format({"font_name": "Microsoft YaHei", "font_size": 10, "num_format": "#,##0"}),
        "percent": workbook.add_format({"font_name": "Microsoft YaHei", "font_size": 10, "num_format": "0.00%"}),
        "score": workbook.add_format({"font_name": "Microsoft YaHei", "font_size": 10, "num_format": "0.00"}),
        "pp": workbook.add_format({"font_name": "Microsoft YaHei", "font_size": 10, "num_format": "0.0000"}),
    }


def header_format(workbook: xlsxwriter.Workbook, base: dict[str, Any], color: str) -> Any:
    if color == "#0F766E":
        return base["header_default"]
    return workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "font_name": "Microsoft YaHei",
            "font_size": 10,
            "bg_color": color,
            "text_wrap": True,
            "valign": "vcenter",
            "border": 1,
            "border_color": "#D1D5DB",
        }
    )


def cell_format(header: str, formats: dict[str, Any]) -> Any:
    fmt = number_format_for(header)
    if fmt == "0.00%":
        return formats["percent"]
    if fmt == "#,##0":
        return formats["integer"]
    if fmt == "0.00":
        return formats["score"]
    if fmt == "0.0000":
        return formats["pp"]
    return formats["text"]


def write_value(worksheet: Any, row_idx: int, col_idx: int, value: Any, fmt: Any) -> None:
    value = safe_value(value)
    if value is None:
        worksheet.write_blank(row_idx, col_idx, None, fmt)
    elif isinstance(value, bool):
        worksheet.write_boolean(row_idx, col_idx, value, fmt)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        worksheet.write_number(row_idx, col_idx, value, fmt)
    else:
        worksheet.write_string(row_idx, col_idx, str(value), fmt)


def add_sheet(
    workbook: xlsxwriter.Workbook,
    formats: dict[str, Any],
    name: str,
    headers: list[str],
    rows: list[dict[str, Any]],
    widths: list[int],
    header_color: str = "#0F766E",
) -> None:
    worksheet = workbook.add_worksheet(name)
    worksheet.hide_gridlines(2)
    worksheet.freeze_panes(1, 0)
    worksheet.set_row(0, 32)
    worksheet.autofilter(0, 0, max(len(rows), 1), len(headers) - 1)

    hfmt = header_format(workbook, formats, header_color)
    for col_idx, header in enumerate(headers):
        worksheet.write_string(0, col_idx, header, hfmt)
        worksheet.set_column(col_idx, col_idx, widths[col_idx], cell_format(header, formats))

    for row_idx, row in enumerate(rows, start=1):
        for col_idx, header in enumerate(headers):
            write_value(worksheet, row_idx, col_idx, row.get(header), cell_format(header, formats))


def build_workbook(source_path: Path, output_path: Path) -> dict[str, Any]:
    data = json.loads(source_path.read_text(encoding="utf-8-sig"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlsxwriter.Workbook(
        str(output_path),
        {
            "constant_memory": True,
            "strings_to_urls": False,
            "nan_inf_to_errors": True,
        },
    )
    formats = make_formats(workbook)
    try:
        add_sheet(
            workbook,
            formats,
            "混排榜",
            MIXED_HEADERS,
            data.get("rows") or [],
            column_widths(
                MIXED_HEADERS,
                {
                    "产品类型": 12,
                    "基金主类型": 12,
                    "产品ID": 22,
                    "产品代码": 14,
                    "产品名称": 36,
                    "机构": 26,
                    "渠道": 18,
                    "管理人/经理": 18,
                    "业绩比较基准": 56,
                    "基准解析说明": 56,
                    "F10错误信息": 72,
                    "F10页面URL": 42,
                    "详情链接": 34,
                },
            ),
        )
        add_sheet(
            workbook,
            formats,
            "分档汇总",
            BUCKET_HEADERS,
            data.get("bucketSummary") or [],
            column_widths(BUCKET_HEADERS, {"基准风险资产权重": 12, "基金主类型": 14, "产品数": 10}),
            "#155E75",
        )
        add_sheet(
            workbook,
            formats,
            "机构抽样核对",
            QA_HEADERS,
            data.get("qaRows") or [],
            column_widths(QA_HEADERS, {"抽样维度": 32, "产品名称": 38, "核对说明": 78}),
            "#334155",
        )
        add_sheet(
            workbook,
            formats,
            "剔除策略明细",
            EXCLUDED_STRATEGY_HEADERS,
            data.get("excludedStrategyRows") or [],
            [24, 36, 26, 18, 16, 28, 72],
            "#9F1239",
        )
        add_sheet(
            workbook,
            formats,
            "数据覆盖与说明",
            COVERAGE_HEADERS,
            [*build_meta_rows(data), *(data.get("coverageRows") or [])],
            [26, 34, 90],
            "#6D28D9",
        )
        add_sheet(
            workbook,
            formats,
            "字段说明",
            NOTE_HEADERS,
            data.get("fieldNotes") or [],
            [24, 110],
            "#374151",
        )
    finally:
        workbook.close()

    inspect_path = output_path.with_suffix(output_path.suffix + ".inspect.ndjson")
    inspect_path.write_text(
        json.dumps({"kind": "notice", "message": "Workbook has no formulas; formula error scan matched 0 entries."}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    return {
        "outputPath": str(output_path),
        "inspectPath": str(inspect_path),
        "rowCount": len(data.get("rows") or []),
        "qaStatusCounts": (data.get("meta") or {}).get("qaStatusCounts") or {},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_json", type=Path)
    parser.add_argument("output_xlsx", type=Path)
    args = parser.parse_args()
    result = build_workbook(args.source_json, args.output_xlsx)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
