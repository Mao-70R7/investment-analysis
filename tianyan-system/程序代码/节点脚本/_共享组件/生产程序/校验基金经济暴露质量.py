from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "fund_economic_exposure_quality"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "基金分类穿透规则.yaml"
CN_TZ = timezone(timedelta(hours=8))

DEFAULT_FIXED_KEYWORDS = [
    "货币",
    "现金",
    "同业存单",
    "短债",
    "中短债",
    "纯债",
    "债券",
    "债券指数",
    "中债",
    "国债",
    "地方债",
    "国开债",
    "国开",
    "政金债",
    "政策性金融债",
    "农发债",
    "进出债",
    "信用债",
    "利率债",
    "固收",
    "可转债",
]
GENERIC_KEYS = {"基金", "其他", "其它", "未分类", "未知", "待穿透"}
UNRESOLVED_KEYS = {"其他", "未分类", "多资产FOF", "待穿透FOF"}


@dataclass(frozen=True)
class RuleConfig:
    fixed_keywords: list[str]
    high_fund_other_threshold: float = 30.0
    manual_review_threshold: float = 30.0


def now_cn() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_code(value: Any) -> str:
    match = re.search(r"(\d{6})", str(value or ""))
    return match.group(1) if match else ""


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        is not None
    )


def rows_as_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def parse_json_object(value: Any) -> dict[str, float]:
    if not value:
        return {}
    try:
        data = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    output: dict[str, float] = {}
    for key, raw in data.items():
        number = safe_float(raw)
        if abs(number) >= 0.0001:
            output[str(key)] = number
    return output


def load_rule_config(config_path: Path = DEFAULT_CONFIG) -> RuleConfig:
    fixed_keywords = list(DEFAULT_FIXED_KEYWORDS)
    high_threshold = 30.0
    manual_threshold = 30.0
    if not config_path.exists():
        return RuleConfig(fixed_keywords, high_threshold, manual_threshold)
    try:
        import yaml  # type: ignore
    except ImportError:
        return RuleConfig(fixed_keywords, high_threshold, manual_threshold)
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    thresholds = data.get("阈值") or {}
    high_threshold = safe_float(thresholds.get("基金其他高占比阈值"), high_threshold)
    manual_threshold = safe_float(thresholds.get("人工补充占比阈值"), manual_threshold)
    for item in data.get("优先级") or []:
        if isinstance(item, dict) and clean_text(item.get("规则代码")) == "固收优先":
            values = [clean_text(value) for value in item.get("关键词") or [] if clean_text(value)]
            if values:
                fixed_keywords = values
            break
    return RuleConfig(fixed_keywords, high_threshold, manual_threshold)


def translate_asset_key(key: str) -> str:
    text = clean_text(key)
    if text in GENERIC_KEYS:
        return "基金" if text == "基金" else "其他"
    if any(token in text for token in ("货币", "现金", "存款")):
        return "货币及现金"
    if "海外债" in text:
        return "海外债券"
    if any(token in text for token in ("债", "固收", "同业存单")):
        return "债券"
    if "黄金" in text:
        return "黄金"
    if any(token in text for token in ("商品", "原油", "油气", "白银")):
        return "商品"
    if "港" in text or "H股" in text:
        return "港股"
    if "美股" in text or "美国" in text:
        return "美股"
    if "新兴" in text:
        return "新兴市场"
    if "海外权益" in text or "发达" in text:
        return "海外权益"
    if "存托" in text:
        return "存托凭证"
    if "股" in text or "权益" in text:
        return "A股"
    return text or "其他"


def normalized_asset_map(exposure: dict[str, float]) -> dict[str, float]:
    output: dict[str, float] = {}
    for key, value in exposure.items():
        mapped = translate_asset_key(key)
        output[mapped] = output.get(mapped, 0.0) + safe_float(value)
    return output


def equity_share(exposure: dict[str, float]) -> float:
    normalized = normalized_asset_map(exposure)
    return round(
        sum(
            value
            for key, value in normalized.items()
            if key in {"A股", "港股", "美股", "海外权益", "新兴市场", "存托凭证"} or "权益" in key or "股票" in key
        ),
        4,
    )


def fixed_share(exposure: dict[str, float]) -> float:
    normalized = normalized_asset_map(exposure)
    return round(
        sum(
            value
            for key, value in normalized.items()
            if key in {"债券", "海外债券", "政策性金融债", "信用债", "短债", "同业存单", "可转债", "货币及现金"}
            or "债" in key
            or "固收" in key
        ),
        4,
    )


def generic_share(exposure: dict[str, float]) -> float:
    normalized = normalized_asset_map(exposure)
    return round(sum(value for key, value in normalized.items() if key in {"基金", "其他"}), 4)


def unresolved_share(exposure: dict[str, float]) -> float:
    return round(sum(value for key, value in exposure.items() if key in UNRESOLVED_KEYS), 4)


def latest_by_code(rows: list[dict[str, Any]], code_key: str, report_key: str, tie_key: str = "") -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = normalize_code(row.get(code_key))
        if not code:
            continue
        sort_key = (clean_text(row.get(report_key)), clean_text(row.get(tie_key)) if tie_key else "")
        current = result.get(code)
        if not current:
            result[code] = row
            continue
        current_sort = (clean_text(current.get(report_key)), clean_text(current.get(tie_key)) if tie_key else "")
        if sort_key >= current_sort:
            result[code] = row
    return result


def load_dictionary(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "基金标准分类字典"):
        return {}
    rows = rows_as_dicts(conn, 'SELECT * FROM "基金标准分类字典"')
    return {normalize_code(row.get("基金代码")): row for row in rows if normalize_code(row.get("基金代码"))}


def load_latest_snapshot(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "基金分类快照"):
        return {}
    rows = rows_as_dicts(conn, 'SELECT * FROM "基金分类快照"')
    return latest_by_code(rows, "基金代码", "报告期", "生成时间")


def load_economic_snapshot(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "基金经济暴露快照"):
        raise SystemExit("缺少表：基金经济暴露快照。请先运行 节点脚本/_共享组件/生产程序/构建基金经济暴露快照.py。")
    rows = rows_as_dicts(conn, 'SELECT * FROM "基金经济暴露快照"')
    return {normalize_code(row.get("基金代码")): row for row in rows if normalize_code(row.get("基金代码"))}


def load_current_holding_summary(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "策略当前持仓"):
        return {}
    rows = rows_as_dicts(
        conn,
        """
        WITH latest AS (
          SELECT "统一策略ID", MAX("持仓日期") AS latest_date
          FROM "策略当前持仓"
          WHERE "基金代码" GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          GROUP BY "统一策略ID"
        )
        SELECT h."基金代码",
               MAX(h."基金名称") AS "基金名称",
               SUM(CASE WHEN COALESCE(h."基金权重_百分比", 0) > 0 THEN COALESCE(h."基金权重_百分比", 0) ELSE 0 END) AS "当前持仓权重_百分比",
               COUNT(DISTINCT h."统一策略ID") AS "当前持仓策略数",
               MAX(h."持仓日期") AS "最新持仓日期"
        FROM "策略当前持仓" h
        JOIN latest l ON l."统一策略ID" = h."统一策略ID" AND l.latest_date = h."持仓日期"
        WHERE h."基金代码" GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        GROUP BY h."基金代码"
        """,
    )
    return {normalize_code(row.get("基金代码")): row for row in rows if normalize_code(row.get("基金代码"))}


def source_text(dictionary_row: dict[str, Any] | None, economic_row: dict[str, Any] | None = None) -> str:
    pieces: list[str] = []
    for row in (dictionary_row, economic_row):
        if not row:
            continue
        for key in (
            "基金代码",
            "标准基金名称",
            "基金名称",
            "天天基金细分类",
            "天天基金大类",
            "天天基金二级分类",
            "标准资产大类",
            "标准资产细类",
            "市场地域标签",
            "投顾资产分类桶",
            "跟踪指数_名称推断",
            "穿透方法",
            "证据说明",
        ):
            if row.get(key):
                pieces.append(str(row.get(key)))
    return " ".join(pieces)


def is_index_like(dictionary_row: dict[str, Any]) -> bool:
    text = source_text(dictionary_row)
    return (
        safe_float(dictionary_row.get("是否指数基金")) > 0
        or safe_float(dictionary_row.get("是否ETF")) > 0
        or safe_float(dictionary_row.get("是否ETF联接")) > 0
        or any(token in text for token in ("指数", "ETF", "联接"))
    )


def matched_fixed_keywords(text: str, fixed_keywords: list[str]) -> list[str]:
    text = text.replace("自由现金流", "自由现流").replace("现金流", "现流")
    upper_text = text.upper()
    return [word for word in fixed_keywords if word and word.upper() in upper_text]


def build_fixed_income_index_audit(
    dictionary: dict[str, dict[str, Any]],
    economic: dict[str, dict[str, Any]],
    holding: dict[str, dict[str, Any]],
    rules: RuleConfig,
) -> dict[str, Any]:
    risk_rows: list[dict[str, Any]] = []
    misclassified: list[dict[str, Any]] = []
    corrected: list[dict[str, Any]] = []
    for code, row in dictionary.items():
        text = source_text(row)
        hits = matched_fixed_keywords(text, rules.fixed_keywords)
        if not hits or not is_index_like(row):
            continue
        econ = economic.get(code)
        exposure = parse_json_object((econ or {}).get("经济资产暴露JSON"))
        item = {
            "基金代码": code,
            "基金名称": clean_text((econ or {}).get("基金名称")) or clean_text(row.get("标准基金名称")),
            "命中关键词": hits,
            "是否ETF联接": int(safe_float(row.get("是否ETF联接"))),
            "是否指数基金": int(safe_float(row.get("是否指数基金"))),
            "经济权益占比": equity_share(exposure),
            "经济固收占比": fixed_share(exposure),
            "标准资产大类": clean_text((econ or {}).get("标准资产大类")),
            "标准资产细类": clean_text((econ or {}).get("标准资产细类")),
            "穿透方法": clean_text((econ or {}).get("穿透方法")),
            "当前持仓权重_百分比": round(safe_float((holding.get(code) or {}).get("当前持仓权重_百分比")), 4),
        }
        risk_rows.append(item)
        if item["经济权益占比"] >= 50:
            misclassified.append(item)
        elif item["经济固收占比"] >= 50:
            corrected.append(item)
    sorter = lambda item: (-safe_float(item.get("当前持仓权重_百分比")), item["基金代码"])
    return {
        "风险样本数": len(risk_rows),
        "仍疑似误归权益数": len(misclassified),
        "已按固收修正数": len(corrected),
        "仍疑似误归权益样本": sorted(misclassified, key=sorter)[:50],
        "已修正样本": sorted(corrected, key=sorter)[:50],
    }


def build_fund_other_remap_audit(
    latest_snapshot: dict[str, dict[str, Any]],
    economic: dict[str, dict[str, Any]],
    holding: dict[str, dict[str, Any]],
    rules: RuleConfig,
) -> dict[str, Any]:
    high_rows: list[dict[str, Any]] = []
    remapped_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    for code, snapshot in latest_snapshot.items():
        raw = parse_json_object(snapshot.get("资产暴露JSON"))
        raw_generic = generic_share(raw)
        if raw_generic < rules.high_fund_other_threshold:
            continue
        econ = economic.get(code)
        out = parse_json_object((econ or {}).get("经济资产暴露JSON"))
        out_generic = generic_share(out)
        item = {
            "基金代码": code,
            "基金名称": clean_text((econ or {}).get("基金名称")) or clean_text(snapshot.get("基金名称")),
            "报告期": clean_text(snapshot.get("报告期")),
            "原始基金其他占比": raw_generic,
            "输出基金其他占比": out_generic,
            "标准资产大类": clean_text((econ or {}).get("标准资产大类")),
            "标准资产细类": clean_text((econ or {}).get("标准资产细类")),
            "穿透方法": clean_text((econ or {}).get("穿透方法")),
            "质量状态": clean_text((econ or {}).get("质量状态")),
            "当前持仓权重_百分比": round(safe_float((holding.get(code) or {}).get("当前持仓权重_百分比")), 4),
            "经济资产暴露": out,
        }
        high_rows.append(item)
        if out and out_generic < 1:
            remapped_rows.append(item)
        else:
            unresolved_rows.append(item)
    sorter = lambda item: (-safe_float(item.get("当前持仓权重_百分比")), -safe_float(item.get("原始基金其他占比")), item["基金代码"])
    return {
        "原始基金其他高占比数": len(high_rows),
        "已重映射数": len(remapped_rows),
        "仍残留基金其他数": len(unresolved_rows),
        "已重映射样本": sorted(remapped_rows, key=sorter)[:80],
        "仍残留样本": sorted(unresolved_rows, key=sorter)[:80],
    }


def build_current_holding_coverage(
    economic: dict[str, dict[str, Any]],
    holding: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    total_weight = sum(safe_float(row.get("当前持仓权重_百分比")) for row in holding.values())
    covered_weight = 0.0
    usable_weight = 0.0
    missing: list[dict[str, Any]] = []
    manual_weight = 0.0
    for code, hold in holding.items():
        weight = safe_float(hold.get("当前持仓权重_百分比"))
        econ = economic.get(code)
        if not econ or not parse_json_object(econ.get("经济资产暴露JSON")):
            missing.append(
                {
                    "基金代码": code,
                    "基金名称": clean_text(hold.get("基金名称")),
                    "当前持仓权重_百分比": round(weight, 4),
                    "当前持仓策略数": int(safe_float(hold.get("当前持仓策略数"))),
                    "原因": "缺少基金经济暴露快照",
                }
            )
            continue
        covered_weight += weight
        if clean_text(econ.get("质量状态")) != "需人工补充":
            usable_weight += weight
        else:
            manual_weight += weight
    return {
        "当前持仓基金数": len(holding),
        "当前持仓权重合计": round(total_weight, 4),
        "快照覆盖基金数": len(holding) - len(missing),
        "快照覆盖权重": round(covered_weight, 4),
        "可用暴露权重": round(usable_weight, 4),
        "需人工补充权重": round(manual_weight, 4),
        "快照加权覆盖率": round(covered_weight / total_weight * 100, 4) if total_weight else 0,
        "可用暴露加权覆盖率": round(usable_weight / total_weight * 100, 4) if total_weight else 0,
        "缺失当前持仓基金样本": sorted(missing, key=lambda item: -safe_float(item.get("当前持仓权重_百分比")))[:80],
    }


def build_manual_supplement_list(
    economic: dict[str, dict[str, Any]],
    holding: dict[str, dict[str, Any]],
    coverage_missing: list[dict[str, Any]],
    rules: RuleConfig,
) -> list[dict[str, Any]]:
    result_by_code: dict[str, dict[str, Any]] = {}
    for item in coverage_missing:
        result_by_code[item["基金代码"]] = item
    for code, row in economic.items():
        exposure = parse_json_object(row.get("经济资产暴露JSON"))
        industry = parse_json_object(row.get("经济行业暴露JSON"))
        reasons: list[str] = []
        if clean_text(row.get("质量状态")) == "需人工补充":
            reasons.append("质量状态为需人工补充")
        if clean_text(row.get("置信度")) == "低":
            reasons.append("置信度低")
        if unresolved_share(exposure) >= rules.manual_review_threshold:
            reasons.append("输出仍有较高未细分暴露")
        if equity_share(exposure) >= 5 and not industry and safe_float((holding.get(code) or {}).get("当前持仓权重_百分比")) > 0:
            reasons.append("当前持仓权益暴露缺行业穿透")
        if not reasons:
            continue
        hold = holding.get(code) or {}
        result_by_code[code] = {
            "基金代码": code,
            "基金名称": clean_text(row.get("基金名称")) or clean_text(hold.get("基金名称")),
            "当前持仓权重_百分比": round(safe_float(hold.get("当前持仓权重_百分比")), 4),
            "当前持仓策略数": int(safe_float(hold.get("当前持仓策略数"))),
            "标准资产大类": clean_text(row.get("标准资产大类")),
            "标准资产细类": clean_text(row.get("标准资产细类")),
            "质量状态": clean_text(row.get("质量状态")),
            "置信度": clean_text(row.get("置信度")),
            "原因": "；".join(reasons),
            "经济资产暴露": exposure,
            "证据说明": clean_text(row.get("证据说明")),
        }
    return sorted(result_by_code.values(), key=lambda item: (-safe_float(item.get("当前持仓权重_百分比")), item["基金代码"]))[:200]


def build_quality_report(conn: sqlite3.Connection, rules: RuleConfig) -> dict[str, Any]:
    dictionary = load_dictionary(conn)
    latest_snapshot = load_latest_snapshot(conn)
    economic = load_economic_snapshot(conn)
    holding = load_current_holding_summary(conn)
    fixed_audit = build_fixed_income_index_audit(dictionary, economic, holding, rules)
    remap_audit = build_fund_other_remap_audit(latest_snapshot, economic, holding, rules)
    coverage = build_current_holding_coverage(economic, holding)
    manual = build_manual_supplement_list(economic, holding, coverage["缺失当前持仓基金样本"], rules)
    status_counter = Counter(clean_text(row.get("质量状态")) for row in economic.values())
    confidence_counter = Counter(clean_text(row.get("置信度")) for row in economic.values())
    return {
        "生成时间": now_cn(),
        "输入表": {
            "基金标准分类字典": len(dictionary),
            "基金分类快照_latest": len(latest_snapshot),
            "基金经济暴露快照": len(economic),
            "当前持仓基金": len(holding),
        },
        "质量状态分布": dict(status_counter),
        "置信度分布": dict(confidence_counter),
        "固收指数误归权益样本": fixed_audit,
        "基金其他高占比重映射": remap_audit,
        "当前持仓加权覆盖": coverage,
        "仍需人工补充列表": manual,
    }


def write_report(output_dir: Path, report: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "基金经济暴露质量报告.json"
    md_path = output_dir / "基金经济暴露质量报告.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def render_sample_table(rows: list[dict[str, Any]], columns: list[str], limit: int = 20) -> list[str]:
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows[:limit]:
        values = [str(row.get(column, "")) for column in columns]
        lines.append("| " + " | ".join(value.replace("\n", " ") for value in values) + " |")
    if not rows:
        lines.append("| 无 |  |  | |" if len(columns) == 4 else "| 无 |")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    coverage = report["当前持仓加权覆盖"]
    fixed = report["固收指数误归权益样本"]
    remap = report["基金其他高占比重映射"]
    lines = [
        "# 基金经济暴露质量报告",
        "",
        f"- 生成时间：{report['生成时间']}",
        f"- 基金经济暴露快照行数：{report['输入表']['基金经济暴露快照']}",
        f"- 固收指数仍疑似误归权益：{fixed['仍疑似误归权益数']} / 风险样本 {fixed['风险样本数']}",
        f"- 基金/其他高占比已重映射：{remap['已重映射数']} / {remap['原始基金其他高占比数']}",
        f"- 当前持仓快照加权覆盖率：{coverage['快照加权覆盖率']}%",
        f"- 当前持仓可用暴露加权覆盖率：{coverage['可用暴露加权覆盖率']}%",
        "",
        "## 质量状态分布",
        "",
        "| 状态 | 数量 |",
        "| --- | ---: |",
    ]
    for key, value in report["质量状态分布"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## 固收指数仍疑似误归权益样本",
            "",
            *render_sample_table(
                fixed["仍疑似误归权益样本"],
                ["基金代码", "基金名称", "经济权益占比", "经济固收占比", "命中关键词", "当前持仓权重_百分比"],
                20,
            ),
            "",
            "## 基金/其他高占比但已重映射样本",
            "",
            *render_sample_table(
                remap["已重映射样本"],
                ["基金代码", "基金名称", "原始基金其他占比", "标准资产细类", "穿透方法", "当前持仓权重_百分比"],
                20,
            ),
            "",
            "## 仍需人工补充列表",
            "",
            *render_sample_table(
                report["仍需人工补充列表"],
                ["基金代码", "基金名称", "当前持仓权重_百分比", "标准资产细类", "原因"],
                30,
            ),
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验基金经济暴露快照质量，并输出 JSON/Markdown 报告。")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 分析库路径，默认 data/analysis_zh_current.sqlite")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="质量报告输出目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印报告摘要，不写文件")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="基金分类穿透规则 YAML")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rules = load_rule_config(args.config)
    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        report = build_quality_report(conn, rules)
    if args.dry_run:
        print(json.dumps({"状态": "dry_run", **report}, ensure_ascii=False, indent=2))
        return
    outputs = write_report(args.output_dir, report)
    print(
        json.dumps(
            {
                "状态": "completed",
                "固收指数仍疑似误归权益数": report["固收指数误归权益样本"]["仍疑似误归权益数"],
                "基金其他高占比已重映射数": report["基金其他高占比重映射"]["已重映射数"],
                "当前持仓可用暴露加权覆盖率": report["当前持仓加权覆盖"]["可用暴露加权覆盖率"],
                "仍需人工补充数": len(report["仍需人工补充列表"]),
                "输出": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
