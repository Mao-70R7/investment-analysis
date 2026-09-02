from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "ttfund"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "signal_strategy_events"

T_GOV = "策略治理标签"
T_SIGNAL_EVENT = "信号策略事件"
T_SIGNAL_INSTRUCTION = "信号策略基金指令"
T_FUND_NAV = "基金日度净值"

WINDOWS = (
    ("1月", 30),
    ("3月", 90),
    ("6月", 180),
    ("1年", 365),
)

TTFUND_TYPE_MAP = {
    "1": "股票类",
    "2": "现金类",
    "3": "混合类",
    "4": "债券类",
    "5": "QDII类",
    "6": "商品类",
    "7": "FOF类",
    "8": "其他",
}

OPERATION_MAP = {
    1: "买入",
    2: "减仓",
    3: "调整",
    4: "调入",
    5: "卖出",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def provenance_path(file: Path, project_root: Path) -> str:
    resolved_file = file.resolve()
    roots: list[Path] = []
    workspace_root = clean(os.environ.get("ADVISOR_WORKSPACE_ROOT"))
    if workspace_root:
        roots.append(Path(workspace_root))
    roots.append(project_root)
    for root in roots:
        try:
            return str(resolved_file.relative_to(root.resolve()))
        except (OSError, ValueError):
            continue
    try:
        return str(file.absolute().relative_to(project_root.absolute()))
    except (OSError, ValueError):
        return str(resolved_file)


def norm_code(value: Any) -> str:
    text = clean(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits and len(digits) <= 6:
        return digits.zfill(6)
    return text


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def round_or_none(value: Any, digits: int = 4) -> float | None:
    number = to_float(value)
    if number is None:
        return None
    return round(number, digits)


def parse_date(value: Any) -> str:
    text = clean(value)
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10] if fmt != "%Y%m%d" else text[:8], fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10]


def parse_iso_date(value: Any) -> date | None:
    text = parse_date(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f'''
        CREATE TABLE IF NOT EXISTS "{T_SIGNAL_EVENT}" (
            "信号事件ID" TEXT PRIMARY KEY,
            "统一策略ID" TEXT NOT NULL,
            "渠道ID" TEXT,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "投顾机构" TEXT,
            "信号日期" TEXT,
            "信号时间" TEXT,
            "信号标题" TEXT,
            "信号原因" TEXT,
            "原始快照路径" TEXT,
            "原始事件序号" INTEGER,
            "指令数" INTEGER,
            "买入指令数" INTEGER,
            "卖出指令数" INTEGER,
            "加仓指令数" INTEGER,
            "减仓指令数" INTEGER,
            "净买入权重_百分点" REAL,
            "总调整强度_百分点" REAL,
            "可评价指令数_1月" INTEGER,
            "胜率_1月" REAL,
            "加权方向收益_1月" REAL,
            "可评价指令数_3月" INTEGER,
            "胜率_3月" REAL,
            "加权方向收益_3月" REAL,
            "可评价指令数_6月" INTEGER,
            "胜率_6月" REAL,
            "加权方向收益_6月" REAL,
            "可评价指令数_1年" INTEGER,
            "胜率_1年" REAL,
            "加权方向收益_1年" REAL,
            "信号评价结论" TEXT,
            "生成时间" TEXT
        );

        CREATE TABLE IF NOT EXISTS "{T_SIGNAL_INSTRUCTION}" (
            "信号指令ID" TEXT PRIMARY KEY,
            "信号事件ID" TEXT NOT NULL,
            "统一策略ID" TEXT NOT NULL,
            "渠道ID" TEXT,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "信号日期" TEXT,
            "信号时间" TEXT,
            "基金代码" TEXT,
            "基金名称" TEXT,
            "分组名称" TEXT,
            "天天基金资产类型" TEXT,
            "指令方向" TEXT,
            "调前权重_百分比" REAL,
            "调后权重_百分比" REAL,
            "权重变化_百分点" REAL,
            "指令强度_百分点" REAL,
            "原始动作码" INTEGER,
            "基金收益率_1月" REAL,
            "方向收益_1月" REAL,
            "评价_1月" TEXT,
            "收益开始日期_1月" TEXT,
            "收益结束日期_1月" TEXT,
            "基金收益率_3月" REAL,
            "方向收益_3月" REAL,
            "评价_3月" TEXT,
            "收益开始日期_3月" TEXT,
            "收益结束日期_3月" TEXT,
            "基金收益率_6月" REAL,
            "方向收益_6月" REAL,
            "评价_6月" TEXT,
            "收益开始日期_6月" TEXT,
            "收益结束日期_6月" TEXT,
            "基金收益率_1年" REAL,
            "方向收益_1年" REAL,
            "评价_1年" TEXT,
            "收益开始日期_1年" TEXT,
            "收益结束日期_1年" TEXT,
            "数据状态" TEXT,
            "生成时间" TEXT
        );
        '''
    )


def load_signal_strategies(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, T_GOV):
        return []
    return fetch_all(
        conn,
        f'''
        SELECT *
        FROM "{T_GOV}"
        WHERE COALESCE("是否信号类组合", 0) = 1
        ORDER BY "渠道ID", "渠道策略ID"
        ''',
    )


def extract_event_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("adjustHistory", "adjustList", "historyList", "list", "List", "Data", "Result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = extract_event_list(value)
            if nested:
                return nested
    for key in ("Data", "data", "result", "Result"):
        nested = extract_event_list(payload.get(key))
        if nested:
            return nested
    return []


def latest_mtime(paths: list[Path]) -> float:
    return max((path.stat().st_mtime for path in paths if path.exists()), default=0.0)


def candidate_signal_files(raw_root: Path, source_strategy_id: str) -> list[Path]:
    names = {
        "raw_adjust_history_tag1.json",
        "raw_adjust_latest_tag0.json",
    }
    files: list[Path] = []
    for parent in (
        raw_root / "incremental_update_runs",
        raw_root / "loggedin_cache",
        raw_root / "app_drive",
        raw_root / "direct_interface_test",
    ):
        if not parent.exists():
            continue
        for strategy_dir in parent.rglob(source_strategy_id):
            if not strategy_dir.is_dir():
                continue
            for file in strategy_dir.iterdir():
                if not file.is_file():
                    continue
                if file.name in names or file.name.startswith(f"adjuseHouseList{source_strategy_id}") or file.name.startswith(f"adjuseHouseListHis{source_strategy_id}"):
                    files.append(file)
    files = sorted(set(files), key=lambda path: (path.stat().st_mtime, str(path)), reverse=True)
    return files


def load_events_from_files(files: list[Path], project_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    seen: set[tuple[str, str, str]] = set()
    events: list[dict[str, Any]] = []
    used_files: list[str] = []
    for file in files:
        try:
            text = file.read_text(encoding="utf-8")
            payload = json.loads(text)
        except Exception:
            continue
        raw_events = extract_event_list(payload)
        if not raw_events:
            continue
        relative = provenance_path(file, project_root)
        used_files.append(relative)
        for raw_index, event in enumerate(raw_events):
            event_date = parse_date(event.get("dateStr") or event.get("adjustDate") or event.get("date"))
            event_time = clean(event.get("timeStr") or event.get("adjustTime") or event.get("time"))
            reason = clean(event.get("reason") or event.get("adjustReason") or event.get("title"))
            key = (event_date, event_time, reason[:200])
            if key in seen:
                continue
            seen.add(key)
            item = dict(event)
            item["_raw_file"] = relative
            item["_raw_index"] = raw_index
            events.append(item)
    events.sort(key=lambda item: (parse_date(item.get("dateStr") or item.get("adjustDate") or item.get("date")), clean(item.get("timeStr") or item.get("time"))), reverse=True)
    return events, used_files


def iter_fund_instructions(event: dict[str, Any]) -> list[dict[str, Any]]:
    buckets = event.get("adjustList") or event.get("arr") or event.get("fundGroupList") or []
    if isinstance(buckets, dict):
        buckets = [buckets]
    if not isinstance(buckets, list):
        return []

    rows: list[dict[str, Any]] = []
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        group_type = clean(bucket.get("type") or bucket.get("newFundType"))
        group_name = clean(bucket.get("typeName") or bucket.get("newFundTypeName") or TTFUND_TYPE_MAP.get(group_type) or group_type)
        funds = bucket.get("changeList") or bucket.get("fundList") or bucket.get("funds") or []
        if isinstance(funds, dict):
            funds = [funds]
        if not isinstance(funds, list):
            continue
        for fund in funds:
            if not isinstance(fund, dict):
                continue
            code = norm_code(fund.get("fundCode") or fund.get("code"))
            name = clean(fund.get("fundName") or fund.get("name"))
            before = to_float(fund.get("preRatio") if fund.get("preRatio") is not None else fund.get("preRate"))
            after = to_float(fund.get("afterRatio") if fund.get("afterRatio") is not None else fund.get("afterRate"))
            if before is None and after is None and not code and not name:
                continue
            before = before if before is not None else 0.0
            after = after if after is not None else 0.0
            change = after - before
            op_code = fund.get("operationInt")
            try:
                op_int = int(op_code) if op_code is not None else None
            except (TypeError, ValueError):
                op_int = None
            rows.append(
                {
                    "基金代码": code,
                    "基金名称": name,
                    "分组名称": group_name,
                    "天天基金资产类型": group_type,
                    "调前权重_百分比": before,
                    "调后权重_百分比": after,
                    "权重变化_百分点": change,
                    "原始动作码": op_int,
                    "指令方向": classify_direction(before, after, op_int),
                }
            )
    rows.sort(key=lambda row: (abs(to_float(row.get("权重变化_百分点")) or 0.0), clean(row.get("基金代码"))), reverse=True)
    return rows


def classify_direction(before: float, after: float, op_int: int | None) -> str:
    change = after - before
    if before <= 0 and after > 0:
        return "买入"
    if before > 0 and after <= 0:
        return "卖出"
    if change > 0.01:
        return "加仓"
    if change < -0.01:
        return "减仓"
    if op_int in OPERATION_MAP:
        return OPERATION_MAP[op_int]
    return "持有"


def instruction_side(direction: str, change: float) -> int:
    if direction in {"买入", "加仓", "调入"} or change > 0:
        return 1
    if direction in {"卖出", "减仓", "调出"} or change < 0:
        return -1
    return 0


def load_fund_nav(conn: sqlite3.Connection, fund_codes: set[str]) -> dict[str, list[tuple[str, float]]]:
    if not fund_codes or not table_exists(conn, T_FUND_NAV):
        return {}
    placeholders = ",".join("?" for _ in fund_codes)
    rows = fetch_all(
        conn,
        f'''
        SELECT "基金代码", "交易日期", COALESCE("累计净值", "单位净值") AS nav
        FROM "{T_FUND_NAV}"
        WHERE "基金代码" IN ({placeholders})
          AND COALESCE("累计净值", "单位净值") IS NOT NULL
        ORDER BY "基金代码", "交易日期"
        ''',
        tuple(sorted(fund_codes)),
    )
    by_code: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in rows:
        code = norm_code(row.get("基金代码"))
        dt = parse_date(row.get("交易日期"))
        nav = to_float(row.get("nav"))
        if code and dt and nav and nav > 0:
            by_code[code].append((dt, nav))
    return by_code


def nearest_on_or_after(series: list[tuple[str, float]], target: date) -> tuple[str, float] | None:
    target_text = target.isoformat()
    for dt, nav in series:
        if dt >= target_text:
            return dt, nav
    return None


def nearest_on_or_before(series: list[tuple[str, float]], target: date) -> tuple[str, float] | None:
    target_text = target.isoformat()
    selected: tuple[str, float] | None = None
    for dt, nav in series:
        if dt <= target_text:
            selected = (dt, nav)
        else:
            break
    return selected


def evaluate_instruction(series: list[tuple[str, float]], signal_date: str, direction: str, change: float) -> dict[str, Any]:
    result: dict[str, Any] = {}
    start_date = parse_iso_date(signal_date)
    side = instruction_side(direction, change)
    if not series or not start_date or side == 0:
        for label, _days in WINDOWS:
            result[f"基金收益率_{label}"] = None
            result[f"方向收益_{label}"] = None
            result[f"评价_{label}"] = "不可评价"
            result[f"收益开始日期_{label}"] = None
            result[f"收益结束日期_{label}"] = None
        return result
    start = nearest_on_or_after(series, start_date)
    for label, days in WINDOWS:
        end = nearest_on_or_before(series, start_date + timedelta(days=days))
        if not start or not end or end[0] <= start[0]:
            result[f"基金收益率_{label}"] = None
            result[f"方向收益_{label}"] = None
            result[f"评价_{label}"] = "不可评价"
            result[f"收益开始日期_{label}"] = start[0] if start else None
            result[f"收益结束日期_{label}"] = end[0] if end else None
            continue
        ret = (end[1] / start[1] - 1.0) * 100.0
        directional = ret * side
        if directional > 0.1:
            rating = "胜"
        elif directional < -0.1:
            rating = "负"
        else:
            rating = "平"
        result[f"基金收益率_{label}"] = round_or_none(ret)
        result[f"方向收益_{label}"] = round_or_none(directional)
        result[f"评价_{label}"] = rating
        result[f"收益开始日期_{label}"] = start[0]
        result[f"收益结束日期_{label}"] = end[0]
    return result


def stable_id(parts: list[Any]) -> str:
    raw = "|".join(clean(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def summarize_event(instructions: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "指令数": len(instructions),
        "买入指令数": 0,
        "卖出指令数": 0,
        "加仓指令数": 0,
        "减仓指令数": 0,
        "净买入权重_百分点": 0.0,
        "总调整强度_百分点": 0.0,
    }
    for row in instructions:
        direction = clean(row.get("指令方向"))
        change = to_float(row.get("权重变化_百分点")) or 0.0
        if direction == "买入":
            summary["买入指令数"] += 1
        elif direction == "卖出":
            summary["卖出指令数"] += 1
        elif direction == "加仓":
            summary["加仓指令数"] += 1
        elif direction == "减仓":
            summary["减仓指令数"] += 1
        summary["净买入权重_百分点"] += change
        summary["总调整强度_百分点"] += abs(change)
    summary["净买入权重_百分点"] = round_or_none(summary["净买入权重_百分点"])
    summary["总调整强度_百分点"] = round_or_none(summary["总调整强度_百分点"])

    for label, _days in WINDOWS:
        evaluated = [
            row for row in instructions
            if clean(row.get(f"评价_{label}")) in {"胜", "负", "平"} and to_float(row.get("指令强度_百分点")) is not None
        ]
        wins = sum(1 for row in evaluated if row.get(f"评价_{label}") == "胜")
        weight_sum = sum(to_float(row.get("指令强度_百分点")) or 0.0 for row in evaluated)
        weighted = sum((to_float(row.get("指令强度_百分点")) or 0.0) * (to_float(row.get(f"方向收益_{label}")) or 0.0) for row in evaluated)
        summary[f"可评价指令数_{label}"] = len(evaluated)
        summary[f"胜率_{label}"] = round_or_none(wins / len(evaluated) * 100.0) if evaluated else None
        summary[f"加权方向收益_{label}"] = round_or_none(weighted / weight_sum) if weight_sum > 0 else None
    conclusion_bits = []
    if summary.get("胜率_3月") is not None:
        conclusion_bits.append(f'3月胜率 {summary["胜率_3月"]}%')
    if summary.get("加权方向收益_3月") is not None:
        conclusion_bits.append(f'3月加权方向收益 {summary["加权方向收益_3月"]}%')
    summary["信号评价结论"] = "；".join(conclusion_bits) if conclusion_bits else "净值或观察期不足，暂不可评价"
    return summary


def build_signal_rows(conn: sqlite3.Connection, raw_root: Path, project_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    strategies = load_signal_strategies(conn)
    raw_events_by_strategy: dict[str, list[dict[str, Any]]] = {}
    used_files_by_strategy: dict[str, list[str]] = {}
    all_fund_codes: set[str] = set()

    for strategy in strategies:
        if clean(strategy.get("渠道ID")) != "ttfund":
            continue
        sid = clean(strategy.get("统一策略ID"))
        source_sid = clean(strategy.get("渠道策略ID"))
        files = candidate_signal_files(raw_root, source_sid)
        events, used_files = load_events_from_files(files, project_root)
        raw_events_by_strategy[sid] = events
        used_files_by_strategy[sid] = used_files
        for event in events:
            for instruction in iter_fund_instructions(event):
                code = clean(instruction.get("基金代码"))
                if code:
                    all_fund_codes.add(code)

    nav_by_code = load_fund_nav(conn, all_fund_codes)
    generated_at = now_iso()
    event_rows: list[dict[str, Any]] = []
    instruction_rows: list[dict[str, Any]] = []

    strategy_by_id = {clean(row.get("统一策略ID")): row for row in strategies}
    for sid, events in raw_events_by_strategy.items():
        strategy = strategy_by_id.get(sid, {})
        for index, event in enumerate(events):
            signal_date = parse_date(event.get("dateStr") or event.get("adjustDate") or event.get("date"))
            signal_time = clean(event.get("timeStr") or event.get("adjustTime") or event.get("time"))
            reason = clean(event.get("reason") or event.get("adjustReason") or event.get("title"))
            raw_instructions = iter_fund_instructions(event)
            if not signal_date and not raw_instructions:
                continue
            event_id = "signal_" + stable_id([sid, signal_date, signal_time, reason, event.get("_raw_index")])
            instructions = []
            for instruction_index, instruction in enumerate(raw_instructions):
                code = clean(instruction.get("基金代码"))
                change = to_float(instruction.get("权重变化_百分点")) or 0.0
                instruction_id = "signal_ins_" + stable_id([event_id, instruction_index, code, instruction.get("基金名称"), change])
                eval_result = evaluate_instruction(nav_by_code.get(code, []), signal_date, clean(instruction.get("指令方向")), change)
                row = {
                    "信号指令ID": instruction_id,
                    "信号事件ID": event_id,
                    "统一策略ID": sid,
                    "渠道ID": clean(strategy.get("渠道ID")),
                    "渠道策略ID": clean(strategy.get("渠道策略ID")),
                    "策略名称": clean(strategy.get("策略名称")),
                    "信号日期": signal_date,
                    "信号时间": signal_time,
                    "基金代码": code,
                    "基金名称": clean(instruction.get("基金名称")),
                    "分组名称": clean(instruction.get("分组名称")),
                    "天天基金资产类型": clean(instruction.get("天天基金资产类型")),
                    "指令方向": clean(instruction.get("指令方向")),
                    "调前权重_百分比": round_or_none(instruction.get("调前权重_百分比")),
                    "调后权重_百分比": round_or_none(instruction.get("调后权重_百分比")),
                    "权重变化_百分点": round_or_none(change),
                    "指令强度_百分点": round_or_none(abs(change)),
                    "原始动作码": instruction.get("原始动作码"),
                    "数据状态": "已评价" if any(clean(eval_result.get(f"评价_{label}")) in {"胜", "负", "平"} for label, _ in WINDOWS) else "净值或观察期不足",
                    "生成时间": generated_at,
                    **eval_result,
                }
                instructions.append(row)
                instruction_rows.append(row)
            event_summary = summarize_event(instructions)
            event_rows.append(
                {
                    "信号事件ID": event_id,
                    "统一策略ID": sid,
                    "渠道ID": clean(strategy.get("渠道ID")),
                    "渠道策略ID": clean(strategy.get("渠道策略ID")),
                    "策略名称": clean(strategy.get("策略名称")),
                    "投顾机构": clean(strategy.get("投顾机构")),
                    "信号日期": signal_date,
                    "信号时间": signal_time,
                    "信号标题": reason[:40] if reason else "信号调整",
                    "信号原因": reason,
                    "原始快照路径": clean(event.get("_raw_file")),
                    "原始事件序号": int(event.get("_raw_index") or index),
                    "生成时间": generated_at,
                    **event_summary,
                }
            )

    event_rows.sort(key=lambda row: (clean(row.get("统一策略ID")), clean(row.get("信号日期")), clean(row.get("信号时间"))), reverse=True)
    instruction_rows.sort(key=lambda row: (clean(row.get("统一策略ID")), clean(row.get("信号日期")), abs(to_float(row.get("权重变化_百分点")) or 0.0)), reverse=True)
    summary = {
        "generated_at": generated_at,
        "signal_strategy_count": len(strategies),
        "strategies_with_raw_events": sum(1 for rows in raw_events_by_strategy.values() if rows),
        "signal_event_count": len(event_rows),
        "signal_instruction_count": len(instruction_rows),
        "fund_codes_seen": len(all_fund_codes),
        "fund_codes_with_nav": sum(1 for code in all_fund_codes if nav_by_code.get(code)),
        "used_files_by_strategy": used_files_by_strategy,
    }
    return event_rows, instruction_rows, summary


def replace_channel_rows(
    conn: sqlite3.Connection,
    table: str,
    rows: list[dict[str, Any]],
    channel_id: str,
) -> None:
    """Rebuild one channel without erasing signal entities loaded by other channels."""

    conn.execute(f'DELETE FROM "{table}" WHERE "渠道ID"=?', (channel_id,))
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ",".join("?" for _ in columns)
    column_sql = ",".join(f'"{col}"' for col in columns)
    conn.executemany(
        f'INSERT OR REPLACE INTO "{table}" ({column_sql}) VALUES ({placeholders})',
        [[row.get(col) for col in columns] for row in rows],
    )


def sync_signal_governance(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, T_GOV) or not table_exists(conn, T_SIGNAL_EVENT):
        return
    columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{T_GOV}")')}
    assignments: list[str] = []
    if "是否信号类组合" in columns:
        assignments.append('"是否信号类组合"=1')
    if "是否纳入常规排名" in columns:
        assignments.append('"是否纳入常规排名"=0')
    if "是否单独分析" in columns:
        assignments.append('"是否单独分析"=1')
    if "分析分组" in columns:
        assignments.append('"分析分组"=\'信号服务\'')
    if "治理状态" in columns:
        assignments.append('"治理状态"=\'信号类组合\'')
    if "持仓处理方式" in columns:
        assignments.append(
            '"持仓处理方式"=\'按官方完整调前调后仓位或基金指令展示；新增资金分配比例不等同存量仓位\''
        )
    if "调仓展示方式" in columns:
        assignments.append('"调仓展示方式"=\'按买入、卖出、加仓、减仓信号时间线单独展示\'')
    if not assignments:
        return
    conn.execute(
        f'''UPDATE "{T_GOV}" SET {", ".join(assignments)}
            WHERE "统一策略ID" IN (
                SELECT DISTINCT "统一策略ID" FROM "{T_SIGNAL_EVENT}"
            )'''
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def output_dir(root: Path) -> Path:
    now = datetime.now().astimezone()
    return root / now.strftime("%Y-%m-%d") / now.strftime("%Y%m%dT%H%M%S%z")


def main() -> None:
    parser = argparse.ArgumentParser(description="从天天接口缓存构建信号类策略事件和基金买卖指令表。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dry-run", action="store_true", help="只生成输出文件，不写入 SQLite。")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)
    event_rows, instruction_rows, summary = build_signal_rows(conn, args.raw_root, PROJECT_ROOT)
    out_dir = output_dir(args.output_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "signal_events.csv", event_rows)
    write_csv(out_dir / "signal_instructions.csv", instruction_rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.dry_run:
        replace_channel_rows(conn, T_SIGNAL_EVENT, event_rows, "ttfund")
        replace_channel_rows(conn, T_SIGNAL_INSTRUCTION, instruction_rows, "ttfund")
        sync_signal_governance(conn)
        conn.commit()
    console_summary = {key: value for key, value in summary.items() if key != "used_files_by_strategy"}
    console_summary["used_file_count_by_strategy"] = {
        key: len(value) for key, value in summary.get("used_files_by_strategy", {}).items()
    }
    print(json.dumps({**console_summary, "output_dir": str(out_dir), "dry_run": args.dry_run}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
