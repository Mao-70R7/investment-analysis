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
DEFAULT_ALGORITHM_VERSION = "rebalance_asset_fee_dual_nav_v9_ttfund_rules_cifm_overseas_placeholder_20260527"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="诊断天天基金 App 业绩大偏差策略中的基金净值提前结束和代码映射候选。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--algorithm-version", default=DEFAULT_ALGORITHM_VERSION)
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def default_output_dir() -> Path:
    now = datetime.now().astimezone()
    return PROJECT_ROOT / "outputs" / "ttfund_top_deviation_fund_gaps" / now.strftime("%Y-%m-%d") / now.strftime("%Y%m%dT%H%M%S%z")


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params)]


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_date(value: Any) -> datetime.date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def nav_gap_days(max_nav_date: Any, segment_end: Any) -> int | None:
    max_date = parse_date(max_nav_date)
    end_date = parse_date(segment_end)
    if not max_date or not end_date:
        return None
    return (end_date - max_date).days


def classify_nav_gap(max_nav_date: Any, segment_end: Any) -> tuple[str, int | None]:
    gap_days = nav_gap_days(max_nav_date, segment_end)
    if gap_days is None:
        return "无可用净值或日期不可解析", None
    if gap_days <= 0:
        return "净值已覆盖区间", gap_days
    if gap_days <= 3:
        return "近期净值披露滞后", gap_days
    if gap_days <= 10:
        return "节假日或QD披露滞后待补", gap_days
    if gap_days <= 60:
        return "阶段性净值缺口待核对", gap_days
    return "长期停更/清盘转型或代码映射待修复", gap_days


def norm_name(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    for token in [" ", "\u3000", "-", "_", "（", "）", "(", ")", "人民币", "份额"]:
        text = text.replace(token, "")
    return text.lower()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def load_fund_profiles(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT f."基金代码", f."基金名称", f."基金公司", f."基金类型",
               MIN(n."交易日期") AS "最早净值日期",
               MAX(n."交易日期") AS "最晚净值日期",
               COUNT(n."交易日期") AS "净值行数"
        FROM "基金信息" f
        LEFT JOIN "基金日度净值" n ON n."基金代码" = f."基金代码"
        GROUP BY f."基金代码", f."基金名称", f."基金公司", f."基金类型"
        """,
    )
    return {str(row["基金代码"]): row for row in rows}


def load_alias_candidates(conn: sqlite3.Connection, profiles: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in fetch_dicts(conn, 'SELECT "映射名称", "基金代码", "标准基金名称", "匹配方式", "置信度" FROM "基金名称映射"'):
        code = str(row["基金代码"])
        profile = profiles.get(code, {})
        payload = {
            "候选基金代码": code,
            "候选基金名称": row.get("标准基金名称") or profile.get("基金名称"),
            "候选基金公司": profile.get("基金公司"),
            "候选基金类型": profile.get("基金类型"),
            "候选最晚净值日期": profile.get("最晚净值日期"),
            "候选净值行数": profile.get("净值行数"),
            "匹配来源": row.get("匹配方式"),
            "置信度": row.get("置信度"),
        }
        candidates[norm_name(row.get("映射名称"))].append(payload)
        candidates[norm_name(row.get("标准基金名称"))].append(payload)
    for code, profile in profiles.items():
        payload = {
            "候选基金代码": code,
            "候选基金名称": profile.get("基金名称"),
            "候选基金公司": profile.get("基金公司"),
            "候选基金类型": profile.get("基金类型"),
            "候选最晚净值日期": profile.get("最晚净值日期"),
            "候选净值行数": profile.get("净值行数"),
            "匹配来源": "基金信息",
            "置信度": "基础库",
        }
        candidates[norm_name(profile.get("基金名称"))].append(payload)
    return candidates


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    profiles = load_fund_profiles(conn)
    aliases = load_alias_candidates(conn, profiles)

    top_rows = fetch_dicts(
        conn,
        """
        SELECT *
        FROM "策略官方偏差分析"
        WHERE "算法版本" = ?
          AND "渠道ID" = 'ttfund'
          AND "App展示默认绝对偏差_百分点" IS NOT NULL
        ORDER BY "App展示默认绝对偏差_百分点" DESC
        LIMIT ?
        """,
        [args.algorithm_version, args.top_n],
    )
    strategy_ids = [str(row["统一策略ID"]) for row in top_rows]
    if not strategy_ids:
        summary = {"策略数": 0, "输出目录": str(output_dir)}
        (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    placeholders = ",".join("?" for _ in strategy_ids)
    segments = fetch_dicts(
        conn,
        f"""
        SELECT *
        FROM "策略模拟净值区间"
        WHERE "算法版本" = ?
          AND "统一策略ID" IN ({placeholders})
          AND COALESCE("结束覆盖不足基金数", 0) > 0
        ORDER BY "统一策略ID", "区间开始日期"
        """,
        [args.algorithm_version, *strategy_ids],
    )
    details_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_ids = sorted({str(row["调仓事件ID"]) for row in segments if row.get("调仓事件ID")})
    if event_ids:
        event_ph = ",".join("?" for _ in event_ids)
        for row in fetch_dicts(
            conn,
            f"""
            SELECT *
            FROM "策略调仓明细"
            WHERE "调仓事件ID" IN ({event_ph})
              AND COALESCE("调后权重_百分比", 0) > 0
            """,
            event_ids,
        ):
            details_by_event[str(row["调仓事件ID"])].append(row)

    strategy_by_id = {str(row["统一策略ID"]): row for row in top_rows}
    fund_gap_rows: list[dict[str, Any]] = []
    replacement_rows: list[dict[str, Any]] = []
    for segment in segments:
        strategy = strategy_by_id.get(str(segment["统一策略ID"]), {})
        segment_end = str(segment.get("区间结束日期") or "")
        for detail in details_by_event.get(str(segment["调仓事件ID"]), []):
            code = str(detail.get("基金代码") or "").zfill(6)
            if not code or code == "000000":
                continue
            profile = profiles.get(code, {})
            max_date = profile.get("最晚净值日期")
            if max_date and segment_end and str(max_date) >= segment_end:
                continue
            gap_type, gap_days = classify_nav_gap(max_date, segment_end)
            fund_name = detail.get("基金名称")
            candidates = [
                item
                for item in aliases.get(norm_name(fund_name), [])
                if item.get("候选基金代码") != code
                and item.get("候选最晚净值日期")
                and (not max_date or str(item["候选最晚净值日期"]) > str(max_date))
            ]
            unique_codes = {item["候选基金代码"] for item in candidates}
            if len(unique_codes) == 1:
                candidate_status = "唯一候选"
                best = candidates[0]
                replacement_rows.append(
                    {
                        "渠道策略ID": strategy.get("渠道策略ID"),
                        "策略名称": strategy.get("策略名称"),
                        "投顾机构": strategy.get("投顾机构"),
                        "原基金代码": code,
                        "原基金名称": fund_name,
                        "原最晚净值日期": max_date,
                        "候选基金代码": best.get("候选基金代码"),
                        "候选基金名称": best.get("候选基金名称"),
                        "候选最晚净值日期": best.get("候选最晚净值日期"),
                        "候选匹配来源": best.get("匹配来源"),
                        "处理建议": "需人工确认后才可替换历史调仓基金代码",
                    }
                )
            elif len(unique_codes) > 1:
                candidate_status = "多候选需人工确认"
            else:
                candidate_status = "无可替代代码候选"
            fund_gap_rows.append(
                {
                    "渠道策略ID": strategy.get("渠道策略ID"),
                    "策略名称": strategy.get("策略名称"),
                    "投顾机构": strategy.get("投顾机构"),
                    "App展示默认绝对偏差_百分点": strategy.get("App展示默认绝对偏差_百分点"),
                    "区间开始日期": segment.get("区间开始日期"),
                    "区间结束日期": segment.get("区间结束日期"),
                    "调仓事件ID": segment.get("调仓事件ID"),
                    "基金代码": code,
                    "基金名称": fund_name,
                    "调后权重_百分比": detail.get("调后权重_百分比"),
                    "基金最晚净值日期": max_date,
                    "区间末日距最晚净值天数": gap_days,
                    "净值缺口类型": gap_type,
                    "候选状态": candidate_status,
                    "候选代码数": len(unique_codes),
                }
            )

    gap_type_counter = Counter(row["净值缺口类型"] for row in fund_gap_rows)
    summary = {
        "算法版本": args.algorithm_version,
        "诊断TopN": args.top_n,
        "Top策略数": len(top_rows),
        "命中净值提前结束区间数": len(segments),
        "命中基金缺口明细数": len(fund_gap_rows),
        "真实提前结束或映射待修复明细数": sum(
            count
            for gap_type, count in gap_type_counter.items()
            if gap_type in {"阶段性净值缺口待核对", "长期停更/清盘转型或代码映射待修复", "无可用净值或日期不可解析"}
        ),
        "净值缺口类型分布": dict(gap_type_counter),
        "唯一候选替换数": len(replacement_rows),
        "候选状态分布": dict(Counter(row["候选状态"] for row in fund_gap_rows)),
        "输出目录": str(output_dir),
    }
    write_csv(output_dir / "top_deviation_fund_gaps.csv", fund_gap_rows)
    write_csv(output_dir / "safe_replacement_candidates.csv", replacement_rows)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
