from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("缺少 PyYAML，请先安装 yaml 解析依赖。") from exc


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "字段口径说明.yaml"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SITE_DATA_DIR = PROJECT_ROOT / "site" / "basic_data" / "data"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "field_dictionary_quality"
PACK_VERSION = 1
JS_GLOBAL = "window.__BASIC_FIELD_DICTIONARY_PACK__"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"字段口径配置不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"字段口径配置不是 YAML 对象: {path}")
    return data


def connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"SQLite 数据库不存在: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [str(row["name"]) for row in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
    return [
        {
            "cid": int(row["cid"]),
            "字段名": row["name"],
            "SQLite类型": row["type"],
            "非空": bool(row["notnull"]),
            "默认值": row["dflt_value"],
            "主键序号": int(row["pk"]),
        }
        for row in rows
    ]


def table_count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {quote_ident(table)}").fetchone()
    except sqlite3.Error:
        return None
    return int(row["c"]) if row else None


def normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def field_sources(field: dict[str, Any]) -> list[dict[str, str]]:
    raw_sources = field.get("来源")
    if raw_sources is not None:
        sources: list[dict[str, str]] = []
        for item in normalize_list(raw_sources):
            if isinstance(item, dict):
                table = item.get("来源表") or item.get("表")
                column = item.get("来源字段") or item.get("字段")
                if table and column:
                    sources.append({"来源表": str(table), "来源字段": str(column)})
        return sources

    table = field.get("来源表") or field.get("表")
    column = field.get("来源字段") or field.get("字段")
    if table and column:
        return [{"来源表": str(table), "来源字段": str(column)}]
    return []


def is_coverage_field(field: dict[str, Any]) -> bool:
    return field.get("纳入覆盖率", True) is not False


def entity_target_tables(entity: dict[str, Any]) -> list[str]:
    tables = [str(x) for x in normalize_list(entity.get("目标表")) if x]
    main_table = entity.get("主表")
    if main_table and str(main_table) not in tables:
        tables.insert(0, str(main_table))
    return tables


def compact_field(field: dict[str, Any], schema: dict[str, set[str]]) -> dict[str, Any]:
    sources = field_sources(field)
    source_items: list[dict[str, Any]] = []
    for source in sources:
        table = source["来源表"]
        column = source["来源字段"]
        source_items.append(
            {
                "来源表": table,
                "来源字段": column,
                "表存在": table in schema,
                "字段存在": column in schema.get(table, set()),
            }
        )

    return {
        "字段名": field.get("字段名") or field.get("中文名") or field.get("来源字段"),
        "中文名": field.get("中文名") or field.get("字段名") or field.get("来源字段"),
        "类型": field.get("类型", ""),
        "重要性": field.get("重要性", "一般"),
        "标准字段": bool(field.get("标准字段", False)),
        "纳入覆盖率": is_coverage_field(field),
        "最低非空率": field.get("最低非空率"),
        "口径": field.get("口径", ""),
        "来源": source_items,
        "来源可用": any(item["字段存在"] for item in source_items),
    }


def build_pack(config: dict[str, Any], conn: sqlite3.Connection, db_path: Path, config_path: Path) -> dict[str, Any]:
    all_tables = list_tables(conn)
    all_columns = {table: table_columns(conn, table) for table in all_tables}
    schema_sets = {table: {str(col["字段名"]) for col in cols} for table, cols in all_columns.items()}

    entities: list[dict[str, Any]] = []
    referenced_tables: set[str] = set()
    for raw_entity in config.get("实体", []):
        if not isinstance(raw_entity, dict):
            continue
        target_tables = entity_target_tables(raw_entity)
        referenced_tables.update(target_tables)
        fields = [field for field in raw_entity.get("字段", []) if isinstance(field, dict)]
        packed_fields = [compact_field(field, schema_sets) for field in fields]

        documented_by_table: dict[str, set[str]] = {}
        for field in packed_fields:
            for source in field["来源"]:
                documented_by_table.setdefault(source["来源表"], set()).add(source["来源字段"])

        table_items: list[dict[str, Any]] = []
        for table in target_tables:
            columns = all_columns.get(table, [])
            documented = documented_by_table.get(table, set())
            table_items.append(
                {
                    "表名": table,
                    "存在": table in schema_sets,
                    "行数": table_count(conn, table) if table in schema_sets else None,
                    "字段数": len(columns),
                    "字段": columns,
                    "已配置字段数": len(documented & schema_sets.get(table, set())),
                    "未配置字段": [col["字段名"] for col in columns if col["字段名"] not in documented],
                }
            )

        coverage_fields = [field for field in packed_fields if field["纳入覆盖率"]]
        covered_fields = [field for field in coverage_fields if field["来源可用"]]
        missing_fields = [field for field in coverage_fields if not field["来源可用"]]

        entities.append(
            {
                "实体": raw_entity.get("实体"),
                "说明": raw_entity.get("说明", ""),
                "主表": raw_entity.get("主表", ""),
                "目标表": target_tables,
                "主键": normalize_list(raw_entity.get("主键")),
                "粒度": raw_entity.get("粒度", ""),
                "字段": packed_fields,
                "表结构": table_items,
                "覆盖摘要": {
                    "配置字段数": len(packed_fields),
                    "纳入覆盖率字段数": len(coverage_fields),
                    "来源可用字段数": len(covered_fields),
                    "来源缺失字段数": len(missing_fields),
                    "来源字段覆盖率": round(len(covered_fields) / len(coverage_fields), 4) if coverage_fields else 1.0,
                    "来源缺失字段": [field["字段名"] for field in missing_fields],
                },
            }
        )

    return {
        "version": PACK_VERSION,
        "generatedAt": now_iso(),
        "source": {
            "config": str(config_path.resolve()),
            "db": str(db_path.resolve()),
        },
        "配置版本": config.get("版本"),
        "名称": config.get("名称", ""),
        "说明": config.get("说明", ""),
        "标准字段来源": config.get("标准字段来源", {}),
        "实体": entities,
        "数据库表": {
            table: {
                "字段数": len(columns),
                "是否被口径配置引用": table in referenced_tables,
            }
            for table, columns in all_columns.items()
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_js(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"{JS_GLOBAL} = {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n"
    path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建字段字典与指标口径站点数据包")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="字段口径 YAML 路径")
    parser.add_argument("--db", "--db-path", dest="db", type=Path, default=DEFAULT_DB_PATH, help="SQLite 数据库路径")
    parser.add_argument(
        "--site-data-dir",
        type=Path,
        default=DEFAULT_SITE_DATA_DIR,
        help="站点 data 目录，输出 field_dictionary_pack.json/js",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="兼容参数；构建脚本不在该目录写入文件",
    )
    parser.add_argument("--dry-run", action="store_true", help="只构建并打印摘要，不写入文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = read_yaml(args.config)
    with connect(args.db) as conn:
        pack = build_pack(config, conn, args.db, args.config)

    json_path = args.site_data_dir / "field_dictionary_pack.json"
    js_path = args.site_data_dir / "field_dictionary_pack.js"
    entity_count = len(pack["实体"])
    field_count = sum(len(entity["字段"]) for entity in pack["实体"])

    if args.dry_run:
        print(f"[dry-run] 将输出: {json_path}")
        print(f"[dry-run] 将输出: {js_path}")
        print(f"[dry-run] 实体数={entity_count}, 字段数={field_count}")
        return 0

    write_json(json_path, pack)
    write_js(js_path, pack)
    print(f"已写入: {json_path}")
    print(f"已写入: {js_path}")
    print(f"实体数={entity_count}, 字段数={field_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
