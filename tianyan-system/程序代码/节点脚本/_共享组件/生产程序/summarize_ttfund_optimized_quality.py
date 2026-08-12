from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "ttfund_optimized_quality"
OLD_ALGORITHM_VERSION = "rebalance_asset_fee_dual_nav_v4_ttfund_20260527"
NEW_ALGORITHM_VERSION = "rebalance_asset_fee_dual_nav_v5_ttfund_optimized_20260527"
CHANNEL_ID = "ttfund"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize optimized ttfund data quality convergence.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--old-algorithm-version", default=OLD_ALGORITHM_VERSION)
    parser.add_argument("--new-algorithm-version", default=NEW_ALGORITHM_VERSION)
    parser.add_argument("--example-limit", type=int, default=8)
    return parser.parse_args()


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params)]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                headers.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def issue_group(issue: str | None, included: int | bool = False) -> str:
    if included:
        return "已纳入模拟"
    text = issue or "未标明"
    if text.startswith("调后权重和不闭合"):
        return "调后权重不闭合"
    if text.startswith("区间结束净值覆盖不足"):
        return "区间结束净值覆盖不足"
    if text.startswith("缺基金净值"):
        return "缺基金净值"
    if text.startswith("调仓日起始净值覆盖不足"):
        return "调仓日起始净值覆盖不足"
    if text.startswith("成立日后无可用调仓区间"):
        return "没有历史调仓事件/无可用区间"
    if text.startswith("无调后正权重"):
        return "调后空仓/清盘事件"
    return text


def issue_reason(group: str) -> str:
    mapping = {
        "调后权重不闭合": "调后有基金正权重，但合计明显偏离100%，且不符合本次可确认的异常占位行剔除规则；继续使用会扭曲基金占比。",
        "区间结束净值覆盖不足": "调仓持有区间内至少一只基金净值提前结束，通常是基金清盘、转型、净值源停更或代码映射缺失，无法完整回放到下一调仓/期末。",
        "缺基金净值": "调仓仓位中存在基金代码，但当前基金净值库没有该基金可用净值，主要集中在QD/互认/境外份额或历史停牌清盘品种。",
        "调仓日起始净值覆盖不足": "基金净值起始日晚于调仓日，缺少建仓当天或确认起始日所需净值。",
        "没有历史调仓事件/无可用区间": "策略信息存在，但天天侧没有采集到可用于建立仓位链的历史调仓事件；按未运作/未披露调仓链单独标注。",
    }
    return mapping.get(group, "未归入标准问题类。")


def summarize_quality(conn: sqlite3.Connection, algorithm_version: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT *
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ? AND "渠道ID" = ?
        """,
        (algorithm_version, CHANNEL_ID),
    )
    grouped = Counter(issue_group(row.get("首个问题类型"), int(row.get("是否纳入模拟") or 0) == 1) for row in rows)
    return (
        {
            "算法版本": algorithm_version,
            "策略数": len(rows),
            "纳入模拟策略数": int(grouped.get("已纳入模拟", 0)),
            "未纳入模拟策略数": len(rows) - int(grouped.get("已纳入模拟", 0)),
            "问题分布": dict(grouped),
        },
        rows,
    )


def repair_summary(conn: sqlite3.Connection, algorithm_version: str) -> list[dict[str, Any]]:
    rules = [
        ("清盘/止盈空仓", "%清盘%"),
        ("其他异常占位行剔除", "%剔除其他%"),
        ("调前权重缺失占位行剔除", "%剔除调前权重缺失%"),
        ("同日重复事件折叠", "%同日%"),
        ("权重轻微归一化", "%归一化%"),
        ("缺投顾费率按0处理", "%缺投顾费率按0处理%"),
    ]
    rows: list[dict[str, Any]] = []
    for name, pattern in rules:
        result = conn.execute(
            """
            SELECT COUNT(DISTINCT "统一策略ID") AS strategy_count, COUNT(*) AS interval_count
            FROM "策略模拟净值区间"
            WHERE "算法版本" = ? AND "渠道ID" = ? AND "修复说明" LIKE ?
            """,
            (algorithm_version, CHANNEL_ID, pattern),
        ).fetchone()
        rows.append({"修复规则": name, "策略数": int(result[0] or 0), "区间数": int(result[1] or 0)})
    return rows


def remaining_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if int(row.get("是否纳入模拟") or 0) == 1:
            continue
        group = issue_group(row.get("首个问题类型"), False)
        result.append(
            {
                "问题分类": group,
                "问题原因": issue_reason(group),
                "统一策略ID": row.get("统一策略ID"),
                "渠道策略ID": row.get("渠道策略ID"),
                "策略名称": row.get("策略名称"),
                "投顾机构": row.get("投顾机构"),
                "首个问题日期": row.get("首个问题日期"),
                "首个问题类型": row.get("首个问题类型"),
                "问题说明": row.get("问题说明"),
                "原始调仓事件数": row.get("原始调仓事件数"),
                "折叠后调仓日期数": row.get("折叠后调仓日期数"),
            }
        )
    return sorted(result, key=lambda item: (str(item["问题分类"]), str(item["统一策略ID"])))


def current_projection_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    if not conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='最新持仓推算稽核策略汇总'"
    ).fetchone()[0]:
        return {"可用": False}
    rows = fetch_dicts(conn, 'SELECT * FROM "最新持仓推算稽核策略汇总" WHERE "渠道ID" = ?', (CHANNEL_ID,))
    return {
        "可用": True,
        "策略数": len(rows),
        "状态分布": dict(Counter(str(row.get("稽核状态") or "未标明") for row in rows)),
        "归因分布": dict(Counter(str(row.get("归因分类") or "未标明") for row in rows)),
        "可推算策略数": sum(int(row.get("是否可推算补齐") or 0) for row in rows),
        "不可推算策略数": sum(1 for row in rows if str(row.get("稽核状态") or "") == "不可推算"),
        "生成时间": max((str(row.get("生成时间") or "") for row in rows), default=None),
    }


def render_report(path: Path, payload: dict[str, Any], remaining: list[dict[str, Any]], examples: dict[str, list[dict[str, Any]]]) -> None:
    old = payload["旧口径"]
    new = payload["新口径"]
    projection = payload["最新仓位稽核"]
    lines = [
        "# 天天基金投顾数据优化后质量报告",
        "",
        f"- 生成时间：{payload['生成时间']}",
        f"- 旧算法版本：`{old['算法版本']}`",
        f"- 新算法版本：`{new['算法版本']}`",
        "",
        "## 收敛结果",
        "",
        f"- 净值回放纳入模拟：{old['纳入模拟策略数']}/{old['策略数']} -> {new['纳入模拟策略数']}/{new['策略数']}，增加 {new['纳入模拟策略数'] - old['纳入模拟策略数']} 个。",
        f"- 未纳入模拟：{old['未纳入模拟策略数']} -> {new['未纳入模拟策略数']}，减少 {old['未纳入模拟策略数'] - new['未纳入模拟策略数']} 个。",
        f"- 清盘/止盈空仓不再作为缺失：旧口径 `{old['问题分布'].get('调后空仓/清盘事件', 0)}` 个，优化后剩余 `{new['问题分布'].get('调后空仓/清盘事件', 0)}` 个。",
        "",
        "## 修复规则命中",
        "",
        "| 修复规则 | 策略数 | 区间数 |",
        "| --- | ---: | ---: |",
    ]
    for row in payload["修复规则命中"]:
        lines.append(f"| {row['修复规则']} | {row['策略数']} | {row['区间数']} |")
    lines.extend(
        [
            "",
            "## 剩余不完整策略",
            "",
            "| 问题分类 | 策略数 | 问题原因 |",
            "| --- | ---: | --- |",
        ]
    )
    for row in payload["剩余问题分类"]:
        lines.append(f"| {row['问题分类']} | {row['策略数']} | {row['问题原因']} |")
    lines.extend(["", "## 问题策略举例", ""])
    for group, group_examples in examples.items():
        lines.extend([f"### {group}", ""])
        for item in group_examples:
            lines.append(
                "- "
                f"{item['统一策略ID']} / {item['策略名称']} / {item['投顾机构']}："
                f"首个问题日期 {item.get('首个问题日期') or '-'}，"
                f"问题 `{item.get('首个问题类型')}`。"
            )
        lines.append("")
    lines.extend(
        [
            "## 最新仓位推算",
            "",
            f"- 可推算补齐策略数：{projection.get('可推算策略数')} / {projection.get('策略数')}",
            f"- 不可推算策略数：{projection.get('不可推算策略数')}",
            f"- 状态分布：{json.dumps(projection.get('状态分布', {}), ensure_ascii=False)}",
            f"- 归因分布：{json.dumps(projection.get('归因分布', {}), ensure_ascii=False)}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    generated_at = datetime.now().isoformat(timespec="seconds")
    output_dir = args.output_root / datetime.now().strftime("%Y-%m-%d") / datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    old_summary, _old_rows = summarize_quality(conn, args.old_algorithm_version)
    new_summary, new_rows = summarize_quality(conn, args.new_algorithm_version)
    remaining = remaining_rows(new_rows)
    counts = Counter(row["问题分类"] for row in remaining)
    remaining_summary = [
        {"问题分类": group, "策略数": count, "问题原因": issue_reason(group)}
        for group, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in remaining:
        if len(examples[row["问题分类"]]) < args.example_limit:
            examples[row["问题分类"]].append(row)
    payload = {
        "生成时间": generated_at,
        "数据库": str(args.db_path.resolve()),
        "旧口径": old_summary,
        "新口径": new_summary,
        "修复规则命中": repair_summary(conn, args.new_algorithm_version),
        "剩余问题分类": remaining_summary,
        "最新仓位稽核": current_projection_summary(conn),
        "输出目录": str(output_dir.resolve()),
    }
    write_csv(output_dir / "remaining_incomplete_strategies.csv", remaining)
    write_csv(output_dir / "remaining_incomplete_examples.csv", [item for rows in examples.values() for item in rows])
    write_csv(output_dir / "remaining_issue_summary.csv", remaining_summary)
    write_csv(output_dir / "repair_rule_summary.csv", payload["修复规则命中"])
    (output_dir / "optimization_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    render_report(output_dir / "ttfund_optimized_quality_report.md", payload, remaining, dict(examples))
    conn.close()
    print(json.dumps({
        "output_dir": str(output_dir.resolve()),
        "old_included": old_summary["纳入模拟策略数"],
        "new_included": new_summary["纳入模拟策略数"],
        "remaining_not_included": new_summary["未纳入模拟策略数"],
        "remaining_issue_summary": remaining_summary,
        "projection": payload["最新仓位稽核"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
