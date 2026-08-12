from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site" / "basic_data"

ECONOMIC_FIELDS = [
    "基金代码",
    "基金名称",
    "基金公司",
    "基金类型",
    "二级分类",
    "报告期",
    "标准资产大类",
    "标准资产细类",
    "经济资产暴露",
    "经济行业暴露",
    "主题标签",
    "穿透层级",
    "穿透方法",
    "证据说明",
    "置信度",
    "质量状态",
    "原始资产暴露",
    "原始基金其他占比",
    "当前持仓权重_百分比",
    "当前持仓策略数",
    "生成时间",
]

FUND_DETAIL_EXTRA_FIELDS = [
    "原始资产暴露",
    "原始行业暴露",
    "经济资产暴露",
    "经济行业暴露",
    "经济主题标签",
    "经济资产大类",
    "经济资产细类",
    "经济暴露报告期",
    "穿透方法",
    "经济暴露证据说明",
    "经济暴露置信度",
    "经济暴露质量状态",
]

EQUITY_ASSET_PATTERN = re.compile(r"A股|港股|美股|新兴市场|其他发达市场|海外权益|存托凭证|REIT")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步基金经济暴露快照到 basic_data 页面数据包。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR, help="basic_data 页面目录，例如 site/basic_data。")
    parser.add_argument("--skip-fund-detail", action="store_true", help="只生成经济暴露包，不改写 fund_detail_pack.js。")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"missing sqlite database: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def parse_json_object(value: Any) -> dict[str, float]:
    if not value:
        return {}
    if isinstance(value, dict):
        data = value
    else:
        try:
            data = json.loads(str(value))
        except json.JSONDecodeError:
            data = None
            text = str(value or "").strip()
            parsed: dict[str, float] = {}
            for part in re.split(r"[、,，;；\s]+", text):
                match = re.match(r"^(.+?)([-+]?\d+(?:\.\d+)?)%$", part.strip())
                if match:
                    label = match.group(1).strip()
                    if label and label not in {"-", "--", "未识别", "未分类"}:
                        parsed[label] = round(float(match.group(2)), 4)
            return parsed
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for key, raw_value in data.items():
        label = str(key).strip()
        if not label or label in {"-", "--", "未识别", "未分类"}:
            continue
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            continue
        if abs(number) > 1e-9:
            out[label] = round(number, 4)
    return out


def parse_json_array(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        data = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def exposure_text(value: Any) -> str:
    items = parse_json_object(value)
    rows = sorted(items.items(), key=lambda item: item[1], reverse=True)
    return "、".join(f"{key}{number:.1f}%" for key, number in rows)


def absolute_industry_exposure(asset_exposure: Any, industry_exposure: Any) -> dict[str, float]:
    assets = parse_json_object(asset_exposure)
    industries = parse_json_object(industry_exposure)
    equity_share = sum(value for key, value in assets.items() if EQUITY_ASSET_PATTERN.search(str(key)))
    if equity_share <= 0 or not industries:
        return {}
    output: dict[str, float] = {}
    for key, value in industries.items():
        absolute = equity_share * value / 100
        if abs(absolute) > 1e-9:
            output[key] = round(absolute, 4)
    return output


def exposure_total(exposure: Any) -> float:
    return round(sum(parse_json_object(exposure).values()), 4)


def equity_asset_share(asset_exposure: Any) -> float:
    assets = parse_json_object(asset_exposure)
    return round(sum(value for key, value in assets.items() if EQUITY_ASSET_PATTERN.search(str(key))), 4)


def fund_level_industry_exposure(asset_exposure: Any, industry_exposure: Any) -> dict[str, float]:
    industries = parse_json_object(industry_exposure)
    if not industries:
        return {}
    equity_share = equity_asset_share(asset_exposure)
    industry_total = sum(industries.values())
    if equity_share > 0 and industry_total <= equity_share + 0.5:
        return {key: round(value, 4) for key, value in industries.items() if abs(value) > 1e-9}
    if equity_share > 0 and industry_total <= 100.5:
        return absolute_industry_exposure(asset_exposure, industries)
    return {}


def tag_text(value: Any) -> str:
    tags = []
    for item in parse_json_array(value):
        if isinstance(item, dict):
            label = item.get("主题名称") or item.get("名称") or item.get("label") or item.get("name")
        else:
            label = item
        label_text = str(label or "").strip()
        if label_text and label_text not in tags:
            tags.append(label_text)
    return "、".join(tags)


def read_economic_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "基金经济暴露快照"):
        raise SystemExit("missing table: 基金经济暴露快照; run 节点脚本/_共享组件/生产程序/构建基金经济暴露快照.py first")
    rows = [dict(row) for row in conn.execute('SELECT * FROM "基金经济暴露快照" ORDER BY "当前持仓权重_百分比" DESC, "基金代码"').fetchall()]
    info_by_code = {}
    if table_exists(conn, "基金信息"):
        info_by_code = {
            str(row["基金代码"]): dict(row)
            for row in conn.execute('SELECT "基金代码", "基金名称", "基金公司", "基金类型" FROM "基金信息"').fetchall()
        }
    dictionary_by_code = {}
    if table_exists(conn, "基金标准分类字典"):
        dictionary_by_code = {
            str(row["基金代码"]): dict(row)
            for row in conn.execute(
                'SELECT "基金代码", "标准基金名称", "基金公司", "天天基金大类", "天天基金二级分类" FROM "基金标准分类字典"'
            ).fetchall()
        }
    output: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("基金代码") or "")
        info = info_by_code.get(code, {})
        dictionary = dictionary_by_code.get(code, {})
        output.append(
            {
                "基金代码": code,
                "基金名称": info.get("基金名称") or dictionary.get("标准基金名称") or row.get("基金名称") or "",
                "基金公司": info.get("基金公司") or dictionary.get("基金公司") or "",
                "基金类型": info.get("基金类型") or dictionary.get("天天基金大类") or "",
                "二级分类": dictionary.get("天天基金二级分类") or "",
                "报告期": row.get("报告期") or "",
                "标准资产大类": row.get("标准资产大类") or "",
                "标准资产细类": row.get("标准资产细类") or "",
                "经济资产暴露": parse_json_object(row.get("经济资产暴露JSON")),
                "经济行业暴露": parse_json_object(row.get("经济行业暴露JSON")),
                "主题标签": parse_json_array(row.get("主题标签JSON")),
                "穿透层级": row.get("穿透层级") or 0,
                "穿透方法": row.get("穿透方法") or "",
                "证据说明": row.get("证据说明") or "",
                "置信度": row.get("置信度") or "",
                "质量状态": row.get("质量状态") or "",
                "原始资产暴露": parse_json_object(row.get("原始资产暴露JSON")),
                "原始基金其他占比": row.get("原始基金其他占比") or 0,
                "当前持仓权重_百分比": row.get("当前持仓权重_百分比") or 0,
                "当前持仓策略数": row.get("当前持仓策略数") or 0,
                "生成时间": row.get("生成时间") or "",
            }
        )
    return output


def write_text_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except UnicodeDecodeError:
            pass
    last_error: OSError | None = None
    for attempt in range(8):
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{attempt}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
            return
        except OSError as exc:
            last_error = exc
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            time.sleep(0.15 * (attempt + 1))
    if last_error is not None:
        raise last_error


def write_economic_pack(site_dir: Path, rows: list[dict[str, Any]]) -> None:
    data_dir = site_dir / "data"
    compact_rows = [[row.get(field, "") for field in ECONOMIC_FIELDS] for row in rows]
    pack = {
        "version": 1,
        "generatedAt": now_iso(),
        "fields": ECONOMIC_FIELDS,
        "rows": compact_rows,
        "口径说明": {
            "主口径": "基金经济暴露快照是页面资产、行业、主题、AI 暴露分析的标准业务口径。",
            "原始资产暴露": "保留基金季报原始资产配置，仅用于审计追溯，不作为负责人默认业务结论。",
            "经济资产暴露": "在原始季报基础上按基金标准分类、名称、指数、QDII/FOF/ETF联接/黄金/固收等规则重映射基金/其他。",
        },
    }
    payload = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
    write_text_if_changed(data_dir / "fund_economic_exposure_pack.json", json.dumps(pack, ensure_ascii=False, indent=2))
    write_text_if_changed(data_dir / "fund_economic_exposure_pack.js", f"window.__BASIC_FUND_ECONOMIC_EXPOSURE_PACK__ = {payload};\n")


def parse_fund_detail_pack(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r"fundDetailPack\s*=\s*(\{.*\});?\s*$", text, re.S)
    if not match:
        raise SystemExit(f"cannot parse fund detail pack: {path}")
    return json.loads(match.group(1))


def normalize_code(value: Any) -> str:
    match = re.search(r"(\d{6})", str(value or ""))
    return match.group(1) if match else ""


def ensure_fields(pack: dict[str, Any], fields: list[str]) -> None:
    fund_fields = pack.setdefault("fundFields", [])
    funds = pack.setdefault("funds", [])
    for field in fields:
        if field in fund_fields:
            continue
        fund_fields.append(field)
        for row in funds:
            row.append("")


def row_to_obj(fields: list[str], row: list[Any]) -> dict[str, Any]:
    return {field: row[index] if index < len(row) else "" for index, field in enumerate(fields)}


def write_obj_to_row(fields: list[str], row: list[Any], data: dict[str, Any]) -> None:
    if len(row) < len(fields):
        row.extend([""] * (len(fields) - len(row)))
    for field, value in data.items():
        if field in fields:
            row[fields.index(field)] = value


def dedupe_fund_rows(fields: list[str], rows: list[list[Any]], economic_codes: set[str]) -> tuple[list[list[Any]], int]:
    code_index = fields.index("基金代码") if "基金代码" in fields else -1
    if code_index < 0:
        return rows, 0

    def score(row: list[Any]) -> tuple[int, int, int]:
        obj = row_to_obj(fields, row)
        code = normalize_code(obj.get("基金代码"))
        economic_hit = 1 if code in economic_codes else 0
        non_empty = sum(1 for value in obj.values() if str(value or "").strip())
        exposure_fields = sum(1 for field in ("经济资产暴露", "经济行业暴露", "资产暴露", "行业暴露") if str(obj.get(field) or "").strip())
        return (economic_hit, exposure_fields, non_empty)

    best_by_code: dict[str, list[Any]] = {}
    output: list[list[Any]] = []
    removed = 0
    for row in rows:
        code = normalize_code(row[code_index] if code_index < len(row) else "")
        if not code:
            output.append(row)
            continue
        existing = best_by_code.get(code)
        if existing is None:
            best_by_code[code] = row
            continue
        removed += 1
        if score(row) > score(existing):
            best_by_code[code] = row
    emitted_codes: set[str] = set()
    for row in rows:
        code = normalize_code(row[code_index] if code_index < len(row) else "")
        if not code:
            continue
        if code in emitted_codes:
            continue
        output.append(best_by_code[code])
        emitted_codes.add(code)
    return output, removed


def apply_to_fund_detail_pack(site_dir: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path = site_dir / "data" / "fund_detail_pack.js"
    if not path.exists():
        return {"updatedFunds": 0, "addedFunds": 0, "skipped": "missing fund_detail_pack.js"}
    pack = parse_fund_detail_pack(path)
    ensure_fields(pack, FUND_DETAIL_EXTRA_FIELDS)
    fields = pack["fundFields"]
    funds = pack.setdefault("funds", [])
    code_index = fields.index("基金代码")
    by_code = {normalize_code(row[code_index] if code_index < len(row) else ""): row for row in funds}
    economic_codes = {normalize_code(row.get("基金代码")) for row in rows if normalize_code(row.get("基金代码"))}
    updated = 0
    added = 0
    for econ in rows:
        code = normalize_code(econ.get("基金代码"))
        if not code:
            continue
        row = by_code.get(code)
        if row is None:
            row = [""] * len(fields)
            write_obj_to_row(fields, row, {"基金代码": code, "基金名称": econ.get("基金名称") or ""})
            funds.append(row)
            by_code[code] = row
            added += 1
        obj = row_to_obj(fields, row)
        economic_asset_obj = econ.get("经济资产暴露")
        economic_industry_obj = econ.get("经济行业暴露")
        economic_asset = exposure_text(economic_asset_obj)
        economic_industry = exposure_text(fund_level_industry_exposure(economic_asset_obj, economic_industry_obj))
        raw_asset = exposure_text(econ.get("原始资产暴露")) or obj.get("资产暴露") or ""
        raw_industry = obj.get("行业暴露") or ""
        update = {
            "基金名称": econ.get("基金名称") or obj.get("基金名称") or "",
            "基金公司": econ.get("基金公司") or obj.get("基金公司") or "",
            "基金类型": econ.get("基金类型") or obj.get("基金类型") or "",
            "二级分类": econ.get("二级分类") or obj.get("二级分类") or "",
            "标准资产大类": econ.get("标准资产大类") or obj.get("标准资产大类") or "",
            "标准资产细类": econ.get("标准资产细类") or obj.get("标准资产细类") or "",
            "基金穿透报告期": econ.get("报告期") or obj.get("基金穿透报告期") or "",
            "基金分类来源": "基金经济暴露快照",
            "资产暴露": economic_asset or obj.get("资产暴露") or "",
            "行业暴露": economic_industry or obj.get("行业暴露") or "",
            "原始资产暴露": raw_asset,
            "原始行业暴露": raw_industry,
            "经济资产暴露": economic_asset,
            "经济行业暴露": economic_industry,
            "经济主题标签": tag_text(econ.get("主题标签")),
            "经济资产大类": econ.get("标准资产大类") or "",
            "经济资产细类": econ.get("标准资产细类") or "",
            "经济暴露报告期": econ.get("报告期") or "",
            "穿透方法": econ.get("穿透方法") or "",
            "经济暴露证据说明": econ.get("证据说明") or "",
            "经济暴露置信度": econ.get("置信度") or "",
            "经济暴露质量状态": econ.get("质量状态") or "",
        }
        write_obj_to_row(fields, row, update)
        updated += 1
    fallback_updated = 0
    for row in funds:
        obj = row_to_obj(fields, row)
        code = normalize_code(obj.get("基金代码"))
        if not code or code in economic_codes:
            continue
        current_economic_asset = parse_json_object(obj.get("经济资产暴露"))
        fallback_asset = current_economic_asset or parse_json_object(obj.get("资产暴露")) or parse_json_object(obj.get("原始资产暴露"))
        if not fallback_asset:
            continue
        current_economic_industry = parse_json_object(obj.get("经济行业暴露"))
        fallback_industry = current_economic_industry or parse_json_object(obj.get("行业暴露")) or parse_json_object(obj.get("原始行业暴露"))
        normalized_industry = fund_level_industry_exposure(fallback_asset, fallback_industry)
        update = {}
        if not current_economic_asset:
            update["经济资产暴露"] = exposure_text(fallback_asset)
        if normalized_industry:
            update["经济行业暴露"] = exposure_text(normalized_industry)
        elif current_economic_industry:
            update["经济行业暴露"] = ""
        if update:
            update.setdefault("经济暴露质量状态", obj.get("经济暴露质量状态") or "规则兜底")
            update.setdefault("经济暴露证据说明", obj.get("经济暴露证据说明") or "未进入基金经济暴露快照，使用基金详情包既有资产/行业画像兜底，并按总资产口径校准。")
            write_obj_to_row(fields, row, update)
            fallback_updated += 1
    pack["funds"], removed = dedupe_fund_rows(fields, funds, economic_codes)
    payload = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
    write_text_if_changed(path, f"window.__BASIC_DATA__ = window.__BASIC_DATA__ || {{}}; window.__BASIC_DATA__.fundDetailPack = {payload};\n")
    return {"updatedFunds": updated, "addedFunds": added, "fallbackUpdatedFunds": fallback_updated, "dedupedFunds": removed, "path": str(path)}


def main() -> None:
    args = parse_args()
    site_dir = args.site_dir.resolve()
    with connect_db(args.db_path) as conn:
        rows = read_economic_rows(conn)
    write_economic_pack(site_dir, rows)
    result: dict[str, Any] = {"economicRows": len(rows), "siteDir": str(site_dir)}
    if not args.skip_fund_detail:
        result.update(apply_to_fund_detail_pack(site_dir, rows))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
