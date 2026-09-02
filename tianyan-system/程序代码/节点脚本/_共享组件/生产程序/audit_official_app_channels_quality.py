from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "official_app_channel_quality"
DEFAULT_CHANNELS = ["gffunds", "gfsec_fima", "zocaifu", "huaxia_tougu", "gfsec_robot"]
STANDARD_ALGORITHM_VERSION = "standard_rebalance_asset_dual_nav_v10_all_channels_20260528"
DISCLOSURE_VERSION = "app_disclosure_nav_v1_20260528"

CHANNEL_LABELS = {
    "gffunds": "广发基金",
    "gfsec_fima": "广发证券",
    "gfsec_robot": "广发证券",
    "gfbank_cgb": "广发银行发现精彩",
    "zocaifu": "中欧财富/中欧钱滚滚",
    "huaxia_tougu": "华夏投顾/华夏财富查理智投",
}

RAW_TO_DB_TABLES = [
    ("strategy_master", "策略信息", "策略主数据"),
    ("strategy_performance_daily", "策略日度业绩", "官方日度业绩"),
    ("strategy_rebalance_event", "策略调仓事件", "历史调仓事件"),
    ("strategy_rebalance_fund_delta", "策略调仓明细", "调仓基金明细"),
    ("strategy_fund_snapshot", "策略当前持仓", "持仓快照/当前持仓"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按天天投顾同口径核对官方 App 渠道原始采集和加工数据质量。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--channel-id", action="append", default=[], help="指定渠道ID，可传多次。")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params)]


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def count_jsonl(path: Path | None) -> int:
    if not path or not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def latest_entity_file(root: Path, channel_id: str, entity: str, suffix: str) -> Path | None:
    base = root / channel_id / entity
    if not base.exists():
        return None
    files = sorted(base.glob(f"*/*{suffix}"))
    return files[-1] if files else None


def latest_collection_summary(root: Path, channel_id: str) -> dict[str, Any]:
    path = latest_entity_file(root, channel_id, "collection_summary", ".json")
    if not path:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_path"] = str(path)
    return data


def pct(part: int | float | None, total: int | float | None) -> float | None:
    if not total:
        return None
    return round(float(part or 0) / float(total) * 100.0, 2)


def quantile(values: list[float], q: float) -> float | None:
    clean = sorted(v for v in values if v is not None and math.isfinite(v))
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 6)
    pos = (len(clean) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return round(clean[lo], 6)
    return round(clean[lo] * (hi - pos) + clean[hi] * (pos - lo), 6)


def channel_strategy_count(conn: sqlite3.Connection, channel_id: str) -> int:
    return int(
        fetch_one(conn, 'SELECT COUNT(*) AS n FROM "策略信息" WHERE "渠道ID" = ?', (channel_id,)).get("n") or 0
    )


def table_channel_count(conn: sqlite3.Connection, table: str, channel_id: str) -> dict[str, Any]:
    return fetch_one(
        conn,
        f'SELECT COUNT(*) AS "入库行数", COUNT(DISTINCT "统一策略ID") AS "入库策略数" FROM "{table}" WHERE "渠道ID" = ?',
        (channel_id,),
    )


def raw_vs_db_rows(conn: sqlite3.Connection, normalized_root: Path, channel_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    collection_summary = latest_collection_summary(normalized_root, channel_id)
    for entity, table, label in RAW_TO_DB_TABLES:
        path = latest_entity_file(normalized_root, channel_id, entity, ".jsonl")
        raw_count = count_jsonl(path)
        db_count = table_channel_count(conn, table, channel_id)
        db_rows = int(db_count.get("入库行数") or 0)
        if entity == "strategy_fund_snapshot" and channel_id == "gffunds":
            status = "口径不同"
            note = "广发该文件保存历史持仓快照，入库当前持仓表仅保留当前/最新持仓口径；调仓基金明细已与原始文件全量核对。"
        elif (
            entity == "strategy_fund_snapshot"
            and channel_id == "gfsec_robot"
            and collection_summary.get("recommendation_fund_list_ok") is True
            and collection_summary.get("fund_level_position_ok") is False
        ):
            status = "推荐清单"
            note = "广发证券公开接口返回推荐基金清单，不是策略当前持仓；标准化文件保留线索，入库当前持仓表按0行处理。"
        elif raw_count == db_rows:
            status = "一致"
            note = "原始标准化文件行数与入库行数一致。"
        else:
            status = "不一致"
            note = f"原始标准化 {raw_count} 行，入库 {db_rows} 行，需要按字段清洗或口径差异复核。"
        rows.append(
            {
                "渠道ID": channel_id,
                "渠道名称": CHANNEL_LABELS.get(channel_id, channel_id),
                "数据对象": label,
                "标准化文件": str(path) if path else "",
                "原始标准化行数": raw_count,
                "入库表": table,
                "入库行数": db_rows,
                "入库策略数": int(db_count.get("入库策略数") or 0),
                "核对结论": status,
                "说明": note,
            }
        )
    return rows


def weight_closure_summary(conn: sqlite3.Connection, channel_id: str) -> dict[str, Any]:
    current_rows = fetch_dicts(
        conn,
        """
        SELECT "统一策略ID", MAX("策略名称") AS "策略名称", "持仓日期",
               SUM(COALESCE("基金权重_百分比", 0)) AS "权重和",
               COUNT(*) AS "基金数",
               SUM(CASE WHEN COALESCE("基金权重_百分比", 0) > 0 AND ("基金代码" IS NULL OR TRIM("基金代码") = '') THEN 1 ELSE 0 END) AS "正权重缺代码数"
        FROM "策略当前持仓" h
        LEFT JOIN "策略信息" s USING ("统一策略ID")
        WHERE h."渠道ID" = ?
        GROUP BY "统一策略ID", "持仓日期"
        """,
        (channel_id,),
    )
    current_bad = [
        row
        for row in current_rows
        if abs(float(row.get("权重和") or 0) - 100.0) > 1.0 or int(row.get("正权重缺代码数") or 0) > 0
    ]
    rebalance_rows = fetch_dicts(
        conn,
        """
        SELECT d."统一策略ID", MAX(s."策略名称") AS "策略名称", d."调仓事件ID", MAX(d."调仓日期") AS "调仓日期",
               SUM(CASE WHEN COALESCE(d."调后权重_百分比", 0) > 0 THEN d."调后权重_百分比" ELSE 0 END) AS "调后权重和",
               COUNT(*) AS "基金数",
               SUM(CASE WHEN COALESCE(d."调后权重_百分比", 0) > 0 AND (d."基金代码" IS NULL OR TRIM(d."基金代码") = '') THEN 1 ELSE 0 END) AS "正权重缺代码数"
        FROM "策略调仓明细" d
        LEFT JOIN "策略信息" s USING ("统一策略ID")
        WHERE d."渠道ID" = ?
        GROUP BY d."调仓事件ID", d."统一策略ID"
        """,
        (channel_id,),
    )
    rebalance_bad = [
        row
        for row in rebalance_rows
        if abs(float(row.get("调后权重和") or 0) - 100.0) > 1.0 or int(row.get("正权重缺代码数") or 0) > 0
    ]
    return {
        "当前持仓快照数": len(current_rows),
        "当前持仓权重闭合快照数": len(current_rows) - len(current_bad),
        "当前持仓异常快照数": len(current_bad),
        "调仓事件数": len(rebalance_rows),
        "调仓后权重闭合事件数": len(rebalance_rows) - len(rebalance_bad),
        "调仓后权重异常事件数": len(rebalance_bad),
        "当前持仓异常样例": current_bad[:20],
        "调仓异常样例": rebalance_bad[:20],
    }


def fund_dependency_summary(conn: sqlite3.Connection, channel_id: str) -> dict[str, Any]:
    codes = {
        str(row["基金代码"]).zfill(6)
        for row in fetch_dicts(
            conn,
            """
            SELECT DISTINCT "基金代码" FROM "策略当前持仓" WHERE "渠道ID" = ? AND "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
            UNION
            SELECT DISTINCT "基金代码" FROM "策略调仓明细" WHERE "渠道ID" = ? AND "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
            """,
            (channel_id, channel_id),
        )
    }
    if not codes:
        return {"涉及基金数": 0, "有净值基金数": 0, "无净值基金数": 0, "分红事件数": 0, "无净值基金样例": []}
    placeholders = ",".join("?" for _ in codes)
    nav_rows = fetch_dicts(
        conn,
        f"""
        SELECT "基金代码", MIN("交易日期") AS "净值起始日", MAX("交易日期") AS "净值结束日", COUNT(*) AS "净值行数"
        FROM "基金日度净值"
        WHERE "基金代码" IN ({placeholders})
        GROUP BY "基金代码"
        """,
        tuple(sorted(codes)),
    )
    nav_by_code = {str(row["基金代码"]).zfill(6): row for row in nav_rows}
    div_count = int(
        fetch_one(
            conn,
            f'SELECT COUNT(*) AS n FROM "基金分红送配" WHERE "基金代码" IN ({placeholders})',
            tuple(sorted(codes)),
        ).get("n")
        or 0
    )
    missing = sorted(codes - set(nav_by_code))
    return {
        "涉及基金数": len(codes),
        "有净值基金数": len(nav_by_code),
        "无净值基金数": len(missing),
        "净值覆盖率_百分比": pct(len(nav_by_code), len(codes)),
        "分红事件数": div_count,
        "无净值基金样例": missing[:30],
    }


def simulation_summary(conn: sqlite3.Connection, channel_id: str) -> dict[str, Any]:
    rows = fetch_dicts(
        conn,
        'SELECT * FROM "策略模拟净值质量" WHERE "渠道ID" = ? AND "算法版本" = ?',
        (channel_id, STANDARD_ALGORITHM_VERSION),
    )
    included = sum(1 for row in rows if int(row.get("是否纳入模拟") or 0) == 1)
    return {
        "策略数": len(rows),
        "纳入标准回放策略数": included,
        "纳入标准回放占比_百分比": pct(included, len(rows)),
        "质量等级分布": dict(Counter(str(row.get("质量等级") or "未披露") for row in rows)),
        "首个问题类型分布": dict(Counter(str(row.get("首个问题类型") or "无") for row in rows)),
    }


def performance_compare_summary(conn: sqlite3.Connection, channel_id: str) -> dict[str, Any]:
    rows = fetch_dicts(
        conn,
        'SELECT * FROM "策略业绩口径对比" WHERE "渠道ID" = ? AND "披露口径版本" = ? AND "算法版本" = ?',
        (channel_id, DISCLOSURE_VERSION, STANDARD_ALGORITHM_VERSION),
    )
    abs_values = []
    for row in rows:
        value = row.get("标准费前绝对偏差_百分点")
        if value is None:
            value = row.get("标准费后绝对偏差_百分点")
        try:
            if value is not None:
                abs_values.append(float(value))
        except (TypeError, ValueError):
            pass
    comparable = sum(1 for row in rows if row.get("对比状态") == "可对比")
    return {
        "对比记录策略数": len(rows),
        "可对比策略数": comparable,
        "可对比占比_百分比": pct(comparable, len(rows)),
        "对比状态分布": dict(Counter(str(row.get("对比状态") or "未披露") for row in rows)),
        "费前绝对偏差中位数_百分点": quantile(abs_values, 0.5),
        "费前绝对偏差P90_百分点": quantile(abs_values, 0.9),
        "费前绝对偏差最大值_百分点": round(max(abs_values), 6) if abs_values else None,
    }


def holding_audit_summary(conn: sqlite3.Connection, channel_id: str) -> dict[str, Any]:
    rows = fetch_dicts(conn, 'SELECT * FROM "最新持仓推算稽核策略汇总" WHERE "渠道ID" = ?', (channel_id,))
    diffs = []
    for row in rows:
        value = row.get("最大绝对差_百分点")
        try:
            if value is not None:
                diffs.append(float(value))
        except (TypeError, ValueError):
            pass
    inferred = sum(1 for row in rows if int(row.get("是否可推算补齐") or 0) == 1 and int(row.get("是否已有当前持仓") or 0) == 0)
    return {
        "稽核策略数": len(rows),
        "稽核状态分布": dict(Counter(str(row.get("稽核状态") or "未披露") for row in rows)),
        "归因分类分布": dict(Counter(str(row.get("归因分类") or "未披露") for row in rows)),
        "当前缺失已推算补齐策略数": inferred,
        "最大绝对差中位数_百分点": quantile(diffs, 0.5),
        "最大绝对差P90_百分点": quantile(diffs, 0.9),
        "最大绝对差最大值_百分点": round(max(diffs), 6) if diffs else None,
    }


def problem_strategy_rows(conn: sqlite3.Connection, channel_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in fetch_dicts(
        conn,
        """
        SELECT "统一策略ID", "渠道策略ID", "策略名称", "稽核状态", "归因分类", "稽核结论",
               "最大绝对差_百分点", "当前权重和_百分比", "调后权重和_百分比"
        FROM "最新持仓推算稽核策略汇总"
        WHERE "渠道ID" = ? AND "稽核状态" NOT IN ('通过', '小额差异')
        ORDER BY "稽核状态", "统一策略ID"
        """,
        (channel_id,),
    ):
        row.update({"渠道ID": channel_id, "问题来源": "当前持仓推算稽核"})
        rows.append(row)
    for row in fetch_dicts(
        conn,
        """
        SELECT "统一策略ID", "渠道策略ID", "策略名称", "质量等级", "首个问题类型", "问题说明", "修复说明"
        FROM "策略模拟净值质量"
        WHERE "渠道ID" = ? AND "算法版本" = ? AND COALESCE("是否纳入模拟", 0) <> 1
        ORDER BY "统一策略ID"
        """,
        (channel_id, STANDARD_ALGORITHM_VERSION),
    ):
        row.update({"渠道ID": channel_id, "问题来源": "标准净值回放"})
        rows.append(row)
    for row in fetch_dicts(
        conn,
        """
        SELECT "统一策略ID", "渠道策略ID", "策略名称", "对比状态", "问题说明", "共同交易日数"
        FROM "策略业绩口径对比"
        WHERE "渠道ID" = ? AND "披露口径版本" = ? AND "算法版本" = ? AND "对比状态" <> '可对比'
        ORDER BY "统一策略ID"
        """,
        (channel_id, DISCLOSURE_VERSION, STANDARD_ALGORITHM_VERSION),
    ):
        row.update({"渠道ID": channel_id, "问题来源": "官方披露业绩对比"})
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def render_report(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# 官方 App 渠道数据质量核对报告",
        "",
        f"- 生成时间：{payload['生成时间']}",
        f"- 覆盖渠道：{', '.join(payload['渠道'])}",
        "- 核对范围：标准化采集文件、入库主数据、历史调仓基金权重、当前持仓、基金净值/分红依赖、官方披露业绩、标准回放业绩和最新持仓推算稽核。",
        "",
        "## 总体结论",
    ]
    for channel_id, item in payload["渠道汇总"].items():
        raw_bad = [row for row in item["原始与入库核对"] if row["核对结论"] == "不一致"]
        weight = item["权重闭合"]
        audit = item["最新持仓推算稽核"]
        perf = item["官方披露业绩对比"]
        lines.append(
            f"- {item['渠道名称']}：策略 {item['策略数']} 个；"
            f"调仓事件 {weight['调仓事件数']} 个，调仓后权重异常 {weight['调仓后权重异常事件数']} 个；"
            f"当前持仓异常 {weight['当前持仓异常快照数']} 个；"
            f"持仓稽核状态 {json.dumps(audit['稽核状态分布'], ensure_ascii=False)}；"
            f"官方业绩可比 {perf['可对比策略数']}/{perf['对比记录策略数']}，"
            f"费前绝对偏差中位数 {perf['费前绝对偏差中位数_百分点']} pct；"
            f"原始入库不一致项 {len(raw_bad)} 个。"
        )
    lines.extend(["", "## 分渠道明细"])
    for channel_id, item in payload["渠道汇总"].items():
        lines.extend(
            [
                "",
                f"### {item['渠道名称']}（{channel_id}）",
                "",
                f"- 采集批次：{item['采集批次ID']}；采集时间：{item['采集时间']}；采集状态：{item['采集状态']}。",
                f"- 原始标准化文件核对：{json.dumps({row['数据对象']: row['核对结论'] for row in item['原始与入库核对']}, ensure_ascii=False)}。",
                f"- 基金依赖：涉及基金 {item['基金依赖']['涉及基金数']} 只，有净值 {item['基金依赖']['有净值基金数']} 只，"
                f"无净值 {item['基金依赖']['无净值基金数']} 只，分红事件 {item['基金依赖']['分红事件数']} 条。",
                f"- 标准回放：纳入 {item['标准净值回放']['纳入标准回放策略数']}/{item['标准净值回放']['策略数']}，"
                f"质量分布 {json.dumps(item['标准净值回放']['质量等级分布'], ensure_ascii=False)}。",
                f"- 最新持仓稽核：{json.dumps(item['最新持仓推算稽核']['稽核状态分布'], ensure_ascii=False)}；"
                f"最大绝对差中位数 {item['最新持仓推算稽核']['最大绝对差中位数_百分点']} pct，"
                f"P90 {item['最新持仓推算稽核']['最大绝对差P90_百分点']} pct。",
                f"- 官方披露业绩对比：{json.dumps(item['官方披露业绩对比']['对比状态分布'], ensure_ascii=False)}；"
                f"费前绝对偏差 P90 {item['官方披露业绩对比']['费前绝对偏差P90_百分点']} pct。",
            ]
        )
        if item["问题策略样例"]:
            lines.append("- 问题策略样例：")
            for row in item["问题策略样例"][:8]:
                lines.append(
                    f"  - {row.get('统一策略ID')} {row.get('策略名称')}：{row.get('问题来源')}；"
                    f"{row.get('稽核状态') or row.get('质量等级') or row.get('对比状态')}；"
                    f"{row.get('归因分类') or row.get('首个问题类型') or row.get('问题说明') or ''}"
                )
    lines.extend(
        [
            "",
            "## 输出文件",
            "",
            "- `official_app_channel_quality_summary.json`：机器可读汇总。",
            "- `official_app_channel_raw_vs_db.csv`：原始标准化文件与入库表核对。",
            "- `official_app_channel_problem_strategies.csv`：需要复核的问题策略清单。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    channels = args.channel_id or DEFAULT_CHANNELS
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    run_id = generated_at.replace("-", "").replace(":", "").replace("+", "").replace("T", "T")
    output_dir = args.output_root / generated_at[:10] / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(args.db_path)
    payload: dict[str, Any] = {"生成时间": generated_at, "渠道": channels, "渠道汇总": {}}
    raw_rows_all: list[dict[str, Any]] = []
    problem_rows_all: list[dict[str, Any]] = []

    for channel_id in channels:
        summary = latest_collection_summary(args.normalized_root, channel_id)
        raw_rows = raw_vs_db_rows(conn, args.normalized_root, channel_id)
        problem_rows = problem_strategy_rows(conn, channel_id)
        raw_rows_all.extend(raw_rows)
        problem_rows_all.extend(problem_rows)
        payload["渠道汇总"][channel_id] = {
            "渠道名称": CHANNEL_LABELS.get(channel_id, channel_id),
            "策略数": channel_strategy_count(conn, channel_id),
            "采集批次ID": summary.get("run_id"),
            "采集时间": summary.get("captured_at"),
            "采集状态": summary.get("collection_status", "未披露"),
            "标准化汇总文件": summary.get("_path"),
            "标准化汇总": summary,
            "原始与入库核对": raw_rows,
            "权重闭合": weight_closure_summary(conn, channel_id),
            "基金依赖": fund_dependency_summary(conn, channel_id),
            "标准净值回放": simulation_summary(conn, channel_id),
            "官方披露业绩对比": performance_compare_summary(conn, channel_id),
            "最新持仓推算稽核": holding_audit_summary(conn, channel_id),
            "问题策略数": len(problem_rows),
            "问题策略样例": problem_rows[:20],
        }
    conn.close()

    (output_dir / "official_app_channel_quality_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(output_dir / "official_app_channel_raw_vs_db.csv", raw_rows_all)
    write_csv(output_dir / "official_app_channel_problem_strategies.csv", problem_rows_all)
    render_report(output_dir / "official_app_channel_quality_report.md", payload)
    print(json.dumps({"outputDir": str(output_dir), "channels": channels}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
