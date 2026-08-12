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
REPORT_JSON = "field_coverage_report.json"
REPORT_MD = "field_coverage_report.md"

IDENTIFIER_CANDIDATES = (
    "统一策略ID",
    "渠道ID",
    "渠道策略ID",
    "策略名称",
    "投顾机构",
    "交易日期",
    "统计日期",
    "持仓日期",
    "调仓日期",
    "调仓事件ID",
    "调仓明细ID",
    "基金代码",
    "基金名称",
    "报告期",
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [str(row["name"]) for row in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
    return [str(row["name"]) for row in rows]


def normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def entity_target_tables(entity: dict[str, Any]) -> list[str]:
    tables = [str(x) for x in normalize_list(entity.get("目标表")) if x]
    main_table = entity.get("主表")
    if main_table and str(main_table) not in tables:
        tables.insert(0, str(main_table))
    return tables


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


def field_name(field: dict[str, Any]) -> str:
    return str(field.get("字段名") or field.get("中文名") or field.get("来源字段") or "")


def threshold_for(field: dict[str, Any]) -> float | None:
    raw = field.get("最低非空率")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    importance = str(field.get("重要性", "一般"))
    if importance == "核心":
        return 0.95
    if importance == "重要":
        return 0.8
    return None


def nonempty_expr(column: str) -> str:
    ident = quote_ident(column)
    return (
        f"{ident} IS NOT NULL "
        f"AND TRIM(CAST({ident} AS TEXT)) NOT IN ('', 'null', 'NULL', 'None', '[]', '{{}}')"
    )


def build_schema(conn: sqlite3.Connection) -> dict[str, set[str]]:
    return {table: set(table_columns(conn, table)) for table in list_tables(conn)}


def collect_table_field_refs(config: dict[str, Any], schema: dict[str, set[str]]) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for entity in config.get("实体", []):
        if not isinstance(entity, dict):
            continue
        for field in entity.get("字段", []):
            if not isinstance(field, dict) or not is_coverage_field(field):
                continue
            for source in field_sources(field):
                table = source["来源表"]
                column = source["来源字段"]
                if table in schema and column in schema[table]:
                    refs.setdefault(table, set()).add(column)
    return refs


def aggregate_nonempty_stats(
    conn: sqlite3.Connection,
    table_fields: dict[str, set[str]],
) -> dict[tuple[str, str], dict[str, Any]]:
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    for table, fields in sorted(table_fields.items()):
        ordered_fields = sorted(fields)
        if not ordered_fields:
            continue
        select_parts = ["COUNT(*) AS __row_count__"]
        for idx, column in enumerate(ordered_fields):
            select_parts.append(f"SUM(CASE WHEN {nonempty_expr(column)} THEN 1 ELSE 0 END) AS c{idx}")
        sql = f"SELECT {', '.join(select_parts)} FROM {quote_ident(table)}"
        row = conn.execute(sql).fetchone()
        row_count = int(row["__row_count__"]) if row else 0
        for idx, column in enumerate(ordered_fields):
            nonempty_count = int(row[f"c{idx}"] or 0) if row else 0
            stats[(table, column)] = {
                "来源表": table,
                "来源字段": column,
                "行数": row_count,
                "非空行数": nonempty_count,
                "非空率": round(nonempty_count / row_count, 6) if row_count else 0.0,
            }
    return stats


def sample_missing_rows(
    conn: sqlite3.Connection,
    schema: dict[str, set[str]],
    table: str,
    column: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if table not in schema or column not in schema[table]:
        return []
    id_columns = [name for name in IDENTIFIER_CANDIDATES if name in schema[table]]
    if column not in id_columns:
        id_columns.append(column)
    select_cols = id_columns[:10]
    sql = (
        f"SELECT {', '.join(quote_ident(col) for col in select_cols)} "
        f"FROM {quote_ident(table)} "
        f"WHERE NOT ({nonempty_expr(column)}) "
        f"LIMIT ?"
    )
    return [dict(row) for row in conn.execute(sql, (limit,)).fetchall()]


def choose_primary_existing_source(
    sources: list[dict[str, str]],
    schema: dict[str, set[str]],
) -> dict[str, str] | None:
    for source in sources:
        table = source["来源表"]
        column = source["来源字段"]
        if table in schema and column in schema[table]:
            return source
    return None


def file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "bytes": 0, "mtime": ""}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
    }


def validate_config(
    config: dict[str, Any],
    conn: sqlite3.Connection,
    db_path: Path,
    config_path: Path,
    site_data_dir: Path,
) -> dict[str, Any]:
    schema = build_schema(conn)
    table_refs = collect_table_field_refs(config, schema)
    nonempty_stats = aggregate_nonempty_stats(conn, table_refs)

    entity_reports: list[dict[str, Any]] = []
    all_important_gaps: list[dict[str, Any]] = []
    all_schema_missing: list[dict[str, Any]] = []

    for entity in config.get("实体", []):
        if not isinstance(entity, dict):
            continue
        name = str(entity.get("实体", ""))
        target_tables = entity_target_tables(entity)
        fields = [field for field in entity.get("字段", []) if isinstance(field, dict)]
        coverage_fields = [field for field in fields if is_coverage_field(field)]

        documented_by_table: dict[str, set[str]] = {}
        field_reports: list[dict[str, Any]] = []
        covered_fields = 0
        data_numerator = 0
        data_denominator = 0
        entity_schema_missing: list[dict[str, Any]] = []
        entity_gaps: list[dict[str, Any]] = []

        for field in fields:
            sources = field_sources(field)
            for source in sources:
                documented_by_table.setdefault(source["来源表"], set()).add(source["来源字段"])

            source_items = []
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

            included = is_coverage_field(field)
            primary_source = choose_primary_existing_source(sources, schema)
            has_source = primary_source is not None
            if included and has_source:
                covered_fields += 1

            stat = None
            if included and primary_source:
                stat = nonempty_stats.get((primary_source["来源表"], primary_source["来源字段"]))
                if stat:
                    data_numerator += int(stat["非空行数"])
                    data_denominator += int(stat["行数"])

            if included and not has_source:
                missing = {
                    "实体": name,
                    "字段名": field_name(field),
                    "重要性": field.get("重要性", "一般"),
                    "来源": source_items,
                    "原因": "来源表或来源字段不存在",
                }
                entity_schema_missing.append(missing)
                all_schema_missing.append(missing)

            threshold = threshold_for(field)
            if included and has_source and stat and threshold is not None and stat["非空率"] < threshold:
                table = primary_source["来源表"]
                column = primary_source["来源字段"]
                gap = {
                    "实体": name,
                    "字段名": field_name(field),
                    "重要性": field.get("重要性", "一般"),
                    "来源表": table,
                    "来源字段": column,
                    "最低非空率": threshold,
                    "实际非空率": stat["非空率"],
                    "行数": stat["行数"],
                    "非空行数": stat["非空行数"],
                    "缺失样本": sample_missing_rows(conn, schema, table, column),
                }
                entity_gaps.append(gap)
                all_important_gaps.append(gap)

            field_reports.append(
                {
                    "字段名": field_name(field),
                    "类型": field.get("类型", ""),
                    "重要性": field.get("重要性", "一般"),
                    "纳入覆盖率": included,
                    "最低非空率": threshold,
                    "来源": source_items,
                    "来源可用": has_source,
                    "数据覆盖": stat,
                    "口径": field.get("口径", ""),
                }
            )

        table_doc_items = []
        documented_column_total = 0
        target_column_total = 0
        for table in target_tables:
            columns = schema.get(table, set())
            documented = documented_by_table.get(table, set()) & columns
            target_column_total += len(columns)
            documented_column_total += len(documented)
            table_doc_items.append(
                {
                    "表名": table,
                    "存在": table in schema,
                    "字段数": len(columns),
                    "已配置字段数": len(documented),
                    "字段口径覆盖率": round(len(documented) / len(columns), 6) if columns else 0.0,
                    "未配置字段": sorted(columns - documented),
                }
            )

        entity_reports.append(
            {
                "实体": name,
                "说明": entity.get("说明", ""),
                "主表": entity.get("主表", ""),
                "目标表": target_tables,
                "配置字段数": len(fields),
                "纳入覆盖率字段数": len(coverage_fields),
                "来源可用字段数": covered_fields,
                "来源字段覆盖率": round(covered_fields / len(coverage_fields), 6) if coverage_fields else 1.0,
                "数据非空覆盖率": round(data_numerator / data_denominator, 6) if data_denominator else 0.0,
                "目标表字段口径覆盖率": round(documented_column_total / target_column_total, 6)
                if target_column_total
                else 0.0,
                "目标表": table_doc_items,
                "字段": field_reports,
                "来源缺失字段": entity_schema_missing,
                "重要缺失字段": entity_gaps,
            }
        )

    overall_fields = sum(item["纳入覆盖率字段数"] for item in entity_reports)
    overall_available = sum(item["来源可用字段数"] for item in entity_reports)
    report = {
        "version": 1,
        "generatedAt": now_iso(),
        "source": {
            "config": str(config_path.resolve()),
            "db": str(db_path.resolve()),
            "siteDataDir": str(site_data_dir.resolve()),
        },
        "sitePack": {
            "json": file_info(site_data_dir / "field_dictionary_pack.json"),
            "js": file_info(site_data_dir / "field_dictionary_pack.js"),
        },
        "standardSources": config.get("标准字段来源", {}),
        "summary": {
            "实体数": len(entity_reports),
            "纳入覆盖率字段数": overall_fields,
            "来源可用字段数": overall_available,
            "来源字段覆盖率": round(overall_available / overall_fields, 6) if overall_fields else 1.0,
            "来源缺失字段数": len(all_schema_missing),
            "重要缺失字段数": len(all_important_gaps),
        },
        "entities": entity_reports,
        "schemaMissingFields": all_schema_missing,
        "importantMissingFields": all_important_gaps,
    }
    return report


def md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 字段口径覆盖校验报告")
    lines.append("")
    lines.append(f"- 生成时间：{report['generatedAt']}")
    lines.append(f"- 配置：`{report['source']['config']}`")
    lines.append(f"- 数据库：`{report['source']['db']}`")
    lines.append("")
    summary = report["summary"]
    lines.append("## 总览")
    lines.append("")
    lines.append(
        f"实体数 {summary['实体数']}；纳入覆盖率字段 {summary['纳入覆盖率字段数']}；"
        f"来源字段覆盖率 {pct(summary['来源字段覆盖率'])}；"
        f"来源缺失字段 {summary['来源缺失字段数']}；重要缺失字段 {summary['重要缺失字段数']}。"
    )
    lines.append("")
    lines.append("| 实体 | 目标表 | 配置字段 | 覆盖字段 | 来源覆盖率 | 数据非空覆盖率 | 目标表字段口径覆盖率 | 重要缺失字段 |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for entity in report["entities"]:
        target_tables = "、".join(item["表名"] for item in entity["目标表"])
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(entity["实体"]),
                    md_escape(target_tables),
                    str(entity["配置字段数"]),
                    str(entity["纳入覆盖率字段数"]),
                    pct(entity["来源字段覆盖率"]),
                    pct(entity["数据非空覆盖率"]),
                    pct(entity["目标表字段口径覆盖率"]),
                    str(len(entity["重要缺失字段"])),
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("## 重要缺失字段")
    lines.append("")
    gaps = report["importantMissingFields"][:50]
    if not gaps:
        lines.append("未发现低于阈值的重要字段。")
    else:
        for gap in gaps:
            lines.append(
                f"- {gap['实体']} / {gap['字段名']}：`{gap['来源表']}.{gap['来源字段']}` "
                f"非空率 {pct(gap['实际非空率'])}，阈值 {pct(gap['最低非空率'])}，"
                f"非空 {gap['非空行数']}/{gap['行数']}。"
            )
            samples = gap.get("缺失样本", [])[:3]
            if samples:
                sample_text = json.dumps(samples, ensure_ascii=False, separators=(",", ":"))
                lines.append(f"  样本：`{md_escape(sample_text)}`")

    if report["schemaMissingFields"]:
        lines.append("")
        lines.append("## 来源缺失字段")
        lines.append("")
        for item in report["schemaMissingFields"][:50]:
            lines.append(f"- {item['实体']} / {item['字段名']}：{item['原因']}。")

    lines.append("")
    lines.append("## 口径提示")
    lines.append("")
    lines.append("- 投顾费率覆盖率以 `策略基准费率状态.投顾费率文本` 和 `策略基准费率状态.年化投顾费率_百分比` 为准。")
    lines.append("- 业绩基准覆盖率以 `策略基准费率状态.业绩基准文本` 和 `策略基准费率状态.基准可用状态` 为准。")
    lines.append("- `策略信息.投顾费率` 与 `策略信息.业绩基准` 仅作为源字段留痕，不纳入覆盖率主指标。")
    lines.append("- 经营洞察衍生指标独立统计，不并入策略主表覆盖率。")
    lines.append("")
    return "\n".join(lines)


def write_report(output_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / REPORT_JSON
    md_path = output_dir / REPORT_MD
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验字段口径覆盖率并输出 JSON/Markdown 报告")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="字段口径 YAML 路径")
    parser.add_argument("--db", "--db-path", dest="db", type=Path, default=DEFAULT_DB_PATH, help="SQLite 数据库路径")
    parser.add_argument("--site-data-dir", type=Path, default=DEFAULT_SITE_DATA_DIR, help="站点 data 目录")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="质量报告输出目录")
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印摘要，不写入报告文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = read_yaml(args.config)
    with connect(args.db) as conn:
        report = validate_config(config, conn, args.db, args.config, args.site_data_dir)

    summary = report["summary"]
    if args.dry_run:
        print(f"[dry-run] 将输出: {args.output_dir / REPORT_JSON}")
        print(f"[dry-run] 将输出: {args.output_dir / REPORT_MD}")
        print(
            "[dry-run] "
            f"实体数={summary['实体数']}, "
            f"来源字段覆盖率={pct(summary['来源字段覆盖率'])}, "
            f"重要缺失字段数={summary['重要缺失字段数']}"
        )
        return 0

    json_path, md_path = write_report(args.output_dir, report)
    print(f"已写入: {json_path}")
    print(f"已写入: {md_path}")
    print(
        f"实体数={summary['实体数']}, "
        f"来源字段覆盖率={pct(summary['来源字段覆盖率'])}, "
        f"重要缺失字段数={summary['重要缺失字段数']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
