# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_RAW_INDEX_DB = PROJECT_ROOT / "data" / "advisor_monitor.sqlite"
DEFAULT_NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "incremental_sample_quality"
CHANNELS = ("ttfund", "gffunds")


T = {
    "strategy": "策略信息",
    "daily": "策略日度业绩",
    "interval": "策略区间业绩",
    "holding": "策略当前持仓",
    "event": "策略调仓事件",
    "delta": "策略调仓明细",
    "status": "策略基准费率状态",
    "source": "数据来源清单",
    "quality": "策略模拟净值质量",
    "fund_nav": "基金日度净值",
}

C = {
    "uid": "统一策略ID",
    "channel": "渠道ID",
    "source_sid": "渠道策略ID",
    "name": "策略名称",
    "inst": "投顾机构",
    "type": "策略类型",
    "risk": "风险等级",
    "launch": "成立日期",
    "hold_period": "建议持有时长",
    "min_amount": "起投金额",
    "fee": "投顾费率",
    "benchmark": "业绩基准",
    "tags": "标签JSON",
    "status": "策略状态",
    "desc": "策略描述",
    "url": "原始来源URL",
    "snapshot": "原始快照ID",
    "first_seen": "首次入库时间",
    "last_seen": "最近入库时间",
    "trade_date": "交易日期",
    "nav": "单位净值",
    "daily_return": "日收益率_百分比",
    "cum_return": "累计收益率_百分比",
    "bench_return": "基准收益率_百分比",
    "index_return": "指数收益率_百分比",
    "max_drawdown": "最大回撤_百分比",
    "section_name": "业绩区段名称",
    "section_type": "业绩区段类型",
    "as_of": "统计日期",
    "interval_code": "区间代码",
    "interval_name": "区间名称",
    "interval_return": "策略收益率_百分比",
    "holding_date": "持仓日期",
    "disclosure_date": "披露日期",
    "fund_code": "基金代码",
    "fund_name": "基金名称",
    "asset_type": "资产类型",
    "group_name": "分组名称",
    "fund_weight": "基金权重_百分比",
    "group_weight": "分组权重_百分比",
    "is_precise": "是否精确权重",
    "confidence": "置信度",
    "access": "访问级别",
    "event_id": "调仓事件ID",
    "rebalance_date": "调仓日期",
    "event_title": "调仓标题",
    "event_reason": "调仓原因",
    "payload_type": "载荷类型",
    "delta_id": "调仓明细ID",
    "before_weight": "调前权重_百分比",
    "after_weight": "调后权重_百分比",
    "weight_delta": "权重变化_百分比",
    "action": "调仓动作",
    "file_type": "文件类型",
    "file_path": "文件路径",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit incremental update quality by channel/institution samples and normalized-source reconciliation."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--raw-index-db", type=Path, default=DEFAULT_RAW_INDEX_DB)
    parser.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--sample-per-institution", type=int, default=5)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params)]


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def norm_date(value: Any) -> str | None:
    text = norm_text(value)
    if not text:
        return None
    return text[:10]


def values_match(db_value: Any, source_value: Any, tolerance: float = 1e-6) -> bool:
    left_num = to_float(db_value)
    right_num = to_float(source_value)
    if left_num is not None or right_num is not None:
        return left_num is not None and right_num is not None and abs(left_num - right_num) <= tolerance
    return (norm_text(db_value) or "") == (norm_text(source_value) or "")


def canonical_tags(value: Any) -> str:
    if value is None or value == "":
        return "[]"
    if isinstance(value, list):
        data = value
    else:
        try:
            data = json.loads(str(value))
        except json.JSONDecodeError:
            data = [str(value)]
    return json.dumps(data or [], ensure_ascii=False, sort_keys=True)


def latest_entity_file(root: Path, channel: str, entity: str, suffix: str = ".jsonl") -> Path | None:
    base = root / channel / entity
    if not base.exists():
        return None
    files = [path for path in base.glob(f"*/*{suffix}") if path.is_file()]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def entity_files(root: Path, channel: str, entity: str, suffix: str = ".jsonl") -> list[Path]:
    base = root / channel / entity
    if not base.exists():
        return []
    return sorted((path for path in base.glob(f"*/*{suffix}") if path.is_file()), key=lambda path: path.stat().st_mtime)


def load_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def index_by(rows: list[dict[str, Any]], *keys: str) -> dict[Any, dict[str, Any]]:
    result: dict[Any, dict[str, Any]] = {}
    for row in rows:
        if len(keys) == 1:
            result[row.get(keys[0])] = row
        else:
            result[tuple(row.get(key) for key in keys)] = row
    return result


def group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[str(row.get(key) or "")].append(row)
    return result


def unified_id(channel: str, source_sid: str) -> str:
    return f"{channel}__{source_sid}"


def select_samples(conn: sqlite3.Connection, sample_per_institution: int) -> list[dict[str, Any]]:
    channel_col = qident(C["channel"])
    inst_col = qident(C["inst"])
    sid_col = qident(C["source_sid"])
    table = qident(T["strategy"])
    sql = f"""
    WITH ranked AS (
      SELECT *,
             ROW_NUMBER() OVER (
               PARTITION BY {channel_col}, COALESCE(NULLIF(TRIM({inst_col}), ''), '未披露机构')
               ORDER BY {sid_col}
             ) AS sample_rank,
             COUNT(*) OVER (
               PARTITION BY {channel_col}, COALESCE(NULLIF(TRIM({inst_col}), ''), '未披露机构')
             ) AS institution_strategy_total
      FROM {table}
      WHERE {channel_col} IN ({','.join('?' for _ in CHANNELS)})
    )
    SELECT * FROM ranked
    WHERE sample_rank <= ?
    ORDER BY {channel_col}, {inst_col}, sample_rank
    """
    return fetch_all(conn, sql, (*CHANNELS, sample_per_institution))


def add_check(
    checks: list[dict[str, Any]],
    sample: dict[str, Any],
    area: str,
    field: str,
    db_value: Any,
    source_value: Any,
    status: str | None = None,
    note: str = "",
) -> None:
    if status is None:
        status = "pass" if values_match(db_value, source_value) else "fail"
    checks.append(
        {
            "渠道ID": sample.get(C["channel"]),
            "投顾机构": sample.get(C["inst"]),
            "渠道策略ID": sample.get(C["source_sid"]),
            "策略名称": sample.get(C["name"]),
            "核对范围": area,
            "字段": field,
            "最终库值": "" if db_value is None else db_value,
            "来源值": "" if source_value is None else source_value,
            "状态": status,
            "说明": note,
        }
    )


def compare_strategy_master(
    checks: list[dict[str, Any]],
    sample: dict[str, Any],
    base_master: dict[str, dict[str, Any]],
    metadata_master: dict[str, dict[str, Any]],
) -> None:
    sid = str(sample[C["source_sid"]])
    base = base_master.get(sid)
    meta = metadata_master.get(sid)
    if not base:
        add_check(checks, sample, "策略基础信息", "normalized_strategy_master", "exists", "missing", "fail", "最新基础采集文件中找不到该策略")
        return

    mappings = [
        ("strategy_name", C["name"]),
        ("advisor_name", C["inst"]),
        ("strategy_type", C["type"]),
        ("risk_level", C["risk"]),
        ("launch_date", C["launch"]),
        ("suggested_holding_period", C["hold_period"]),
        ("minimum_amount", C["min_amount"]),
        ("status", C["status"]),
        ("strategy_description", C["desc"]),
        ("source_url", C["url"]),
    ]
    for source_key, db_col in mappings:
        source_value = norm_date(base.get(source_key)) if source_key == "launch_date" else base.get(source_key)
        if source_value is None and sample.get(db_col) not in (None, ""):
            add_check(
                checks,
                sample,
                "策略基础信息",
                db_col,
                sample.get(db_col),
                source_value,
                "pass",
                "最终库保留历史采集或分类增强字段，本批基础源为空",
            )
        else:
            add_check(checks, sample, "策略基础信息", db_col, sample.get(db_col), source_value)

    add_check(
        checks,
        sample,
        "策略基础信息",
        C["tags"],
        canonical_tags(sample.get(C["tags"])),
        canonical_tags(base.get("tags")),
    )

    if sample.get(C["channel"]) == "gffunds" and meta:
        fee_source = meta.get("advisory_fee_rate")
        benchmark_source = meta.get("benchmark")
        source_note = "广发费率/基准来自低频 metadata/策略说明书补采"
    else:
        fee_source = base.get("advisory_fee_rate")
        benchmark_source = base.get("benchmark")
        source_note = ""

    for db_col, source_value in [(C["fee"], fee_source), (C["benchmark"], benchmark_source)]:
        if source_value is None and sample.get(db_col) not in (None, ""):
            add_check(
                checks,
                sample,
                "策略基础信息",
                db_col,
                sample.get(db_col),
                source_value,
                "pass",
                source_note or "最终库保留历史/低频补采字段，本批基础源为空",
            )
        else:
            add_check(checks, sample, "策略基础信息", db_col, sample.get(db_col), source_value, note=source_note)

    source_snapshot = base.get("source_snapshot_id")
    status = "pass" if norm_text(sample.get(C["snapshot"])) == norm_text(source_snapshot) else "warn"
    note = ""
    if sample.get(C["channel"]) == "gffunds" and meta:
        note = "策略行融合了基础接口与低频 metadata；单一原始快照ID只能代表行级最近来源，不能代表费率/基准字段来源"
    add_check(checks, sample, "策略基础信息", C["snapshot"], sample.get(C["snapshot"]), source_snapshot, status=status, note=note)


def compare_daily(
    conn: sqlite3.Connection,
    checks: list[dict[str, Any]],
    sample: dict[str, Any],
    normalized_daily: dict[str, list[dict[str, Any]]],
) -> None:
    uid = sample[C["uid"]]
    sid = sample[C["source_sid"]]
    rows = fetch_all(
        conn,
        f"""
        SELECT * FROM {qident(T["daily"])}
        WHERE {qident(C["uid"])} = ?
        ORDER BY {qident(C["trade_date"])}
        """,
        (uid,),
    )
    source_rows = normalized_daily.get(str(sid), [])
    add_check(checks, sample, "日度业绩", "最终库行数", len(rows), len(rows), "pass" if rows else "warn", "最终库无日度业绩时通常是渠道未返回单日净值/曲线")
    add_check(
        checks,
        sample,
        "日度业绩",
        "最新normalized行数",
        len(source_rows),
        len(source_rows),
        "pass" if source_rows else "warn",
        "最新增量文件中无该策略日度记录时，可能由历史官方曲线保留",
    )
    if not rows:
        return
    latest = rows[-1]
    source_latest = max(source_rows, key=lambda row: str(row.get("trade_date") or "")) if source_rows else None
    if source_latest:
        db_by_date = {str(row.get(C["trade_date"]) or ""): row for row in rows}
        aligned = db_by_date.get(str(source_latest.get("trade_date") or ""))
        add_check(
            checks,
            sample,
            "日度业绩",
            "最终库最新交易日",
            latest.get(C["trade_date"]),
            source_latest.get("trade_date"),
            "pass" if str(latest.get(C["trade_date"]) or "") >= str(source_latest.get("trade_date") or "") else "fail",
            "最终库可能同时包含 App 详情曲线，允许比本批 quote 更新",
        )
        if not aligned:
            add_check(checks, sample, "日度业绩", C["trade_date"], "missing", source_latest.get("trade_date"), "fail", "最终库缺少本批 normalized 对应交易日")
            return
        metric_map = [
            ("nav", C["nav"]),
            ("daily_return", C["daily_return"]),
            ("cumulative_return", C["cum_return"]),
            ("benchmark_return", C["bench_return"]),
            ("index_return", C["index_return"]),
        ]
        for source_key, db_col in metric_map:
            source_value = source_latest.get(source_key)
            if source_value is None:
                add_check(checks, sample, "日度业绩", db_col, aligned.get(db_col), source_value, "skip", "来源为空，最终库可能由历史记录或修复逻辑补齐")
            else:
                add_check(checks, sample, "日度业绩", db_col, aligned.get(db_col), source_value)
        add_check(checks, sample, "日度业绩", C["snapshot"], aligned.get(C["snapshot"]), source_latest.get("source_snapshot_id"))


def compare_interval(
    conn: sqlite3.Connection,
    checks: list[dict[str, Any]],
    sample: dict[str, Any],
    normalized_interval: dict[str, list[dict[str, Any]]],
) -> None:
    if sample.get(C["channel"]) != "ttfund":
        return
    uid = sample[C["uid"]]
    sid = sample[C["source_sid"]]
    rows = fetch_all(conn, f'SELECT * FROM {qident(T["interval"])} WHERE {qident(C["uid"])} = ?', (uid,))
    source_rows = normalized_interval.get(str(sid), [])
    add_check(checks, sample, "区间业绩", "最终库行数", len(rows), len(source_rows), "pass" if len(rows) >= len(source_rows) and source_rows else "warn")
    by_key = {(r.get(C["as_of"]), r.get(C["interval_code"])): r for r in rows}
    for source in source_rows[:8]:
        key = (norm_date(source.get("as_of_date")), source.get("interval_code"))
        db_row = by_key.get(key)
        if not db_row:
            add_check(checks, sample, "区间业绩", f"{key}", "missing", "exists", "fail")
            continue
        add_check(checks, sample, "区间业绩", f"{source.get('interval_label')}-策略收益", db_row.get(C["interval_return"]), source.get("return_value"))
        if source.get("benchmark_return") is not None:
            add_check(checks, sample, "区间业绩", f"{source.get('interval_label')}-基准收益", db_row.get(C["bench_return"]), source.get("benchmark_return"))


def latest_position_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    latest_date = max(str(row.get("position_date") or "") for row in rows)
    return [row for row in rows if str(row.get("position_date") or "") == latest_date]


def compare_holdings(
    conn: sqlite3.Connection,
    checks: list[dict[str, Any]],
    sample: dict[str, Any],
    normalized_holdings: dict[str, list[dict[str, Any]]],
) -> None:
    uid = sample[C["uid"]]
    sid = sample[C["source_sid"]]
    source_rows = latest_position_rows(normalized_holdings.get(str(sid), []))
    if any(row.get("fund_weight") is not None for row in source_rows):
        source_rows = [row for row in source_rows if (to_float(row.get("fund_weight")) or 0.0) > 0.0]
    db_rows = fetch_all(
        conn,
        f"""
        SELECT * FROM {qident(T["holding"])}
        WHERE {qident(C["uid"])} = ?
        ORDER BY {qident(C["holding_date"])}, {qident(C["fund_code"])}, {qident(C["fund_name"])}
        """,
        (uid,),
    )
    latest_db_date = max((str(row.get(C["holding_date"]) or "") for row in db_rows), default="")
    latest_db_rows = [row for row in db_rows if str(row.get(C["holding_date"]) or "") == latest_db_date]
    if not source_rows and not latest_db_rows:
        add_check(checks, sample, "当前持仓", "最新持仓日期", "", "", "warn", "该策略最新采集未披露当前持仓")
        add_check(checks, sample, "当前持仓", "最新持仓基金数", 0, 0, "warn", "该策略最新采集未披露当前持仓")
        return
    add_check(checks, sample, "当前持仓", "最新持仓日期", latest_db_date, source_rows[0].get("position_date") if source_rows else None, "pass" if source_rows and latest_db_date == source_rows[0].get("position_date") else "warn")
    add_check(checks, sample, "当前持仓", "最新持仓基金数", len(latest_db_rows), len(source_rows), "pass" if len(latest_db_rows) == len(source_rows) and source_rows else "fail")
    if not source_rows or not latest_db_rows:
        return
    db_by_fund = {(row.get(C["fund_code"]) or "", row.get(C["fund_name"]) or ""): row for row in latest_db_rows}
    for source in source_rows[:10]:
        key = (source.get("fund_code") or "", source.get("fund_name") or "")
        db_row = db_by_fund.get(key)
        if not db_row:
            add_check(checks, sample, "当前持仓", f"基金存在:{key}", "missing", "exists", "fail")
            continue
        add_check(checks, sample, "当前持仓", f"{key}-基金权重", db_row.get(C["fund_weight"]), source.get("fund_weight"), "pass" if values_match(db_row.get(C["fund_weight"]), source.get("fund_weight")) or source.get("fund_weight") is None else "fail")
        if source.get("group_weight") is not None:
            add_check(checks, sample, "当前持仓", f"{key}-分组权重", db_row.get(C["group_weight"]), source.get("group_weight"))
    precise = any(int(row.get(C["is_precise"]) or 0) == 1 for row in latest_db_rows)
    if precise:
        weight_sum = sum(to_float(row.get(C["fund_weight"])) or 0.0 for row in latest_db_rows)
        status = "pass" if abs(weight_sum - 100.0) <= 1.0 else "fail"
        add_check(checks, sample, "当前持仓", "精确权重合计", round(weight_sum, 6), 100.0, status)
    else:
        add_check(checks, sample, "当前持仓", "精确权重合计", "非精确权重", "不适用", "skip", "天天基金部分持仓只披露分组权重，不按基金精确闭合")


def compare_rebalance(
    conn: sqlite3.Connection,
    checks: list[dict[str, Any]],
    sample: dict[str, Any],
    normalized_events: dict[str, list[dict[str, Any]]],
    normalized_deltas_by_event: dict[str, list[dict[str, Any]]],
) -> None:
    uid = sample[C["uid"]]
    sid = sample[C["source_sid"]]
    db_events = fetch_all(conn, f'SELECT * FROM {qident(T["event"])} WHERE {qident(C["uid"])} = ?', (uid,))
    src_events = normalized_events.get(str(sid), [])
    add_check(checks, sample, "调仓事件", "事件数", len(db_events), len(src_events), "pass" if len(db_events) >= len(src_events) and src_events else "warn")
    db_by_event = {row[C["event_id"]]: row for row in db_events}
    for source in src_events[:5]:
        event_id = source.get("rebalance_event_id")
        db_event = db_by_event.get(event_id)
        if not db_event:
            add_check(checks, sample, "调仓事件", f"事件存在:{event_id}", "missing", "exists", "fail")
            continue
        add_check(checks, sample, "调仓事件", f"{event_id}-调仓日期", db_event.get(C["rebalance_date"]), source.get("rebalance_date"))
        add_check(checks, sample, "调仓事件", f"{event_id}-调仓标题", db_event.get(C["event_title"]), source.get("event_title"))
        src_deltas = normalized_deltas_by_event.get(str(event_id), [])
        db_deltas = fetch_all(conn, f'SELECT * FROM {qident(T["delta"])} WHERE {qident(C["event_id"])} = ?', (event_id,))
        add_check(checks, sample, "调仓明细", f"{event_id}-基金明细数", len(db_deltas), len(src_deltas), "pass" if len(db_deltas) == len(src_deltas) and src_deltas else "warn")
        if src_deltas:
            after_sum = sum(to_float(row.get(C["after_weight"])) or 0.0 for row in db_deltas)
            status = "pass" if abs(after_sum - 100.0) <= 1.5 else "warn"
            add_check(checks, sample, "调仓明细", f"{event_id}-调后权重合计", round(after_sum, 6), 100.0, status)


def raw_snapshot_exists(raw_conn: sqlite3.Connection, snapshot_id: str | None) -> bool:
    if not snapshot_id:
        return False
    row = raw_conn.execute("SELECT 1 FROM raw_snapshot WHERE snapshot_id = ? AND parse_status = 'success'", (snapshot_id,)).fetchone()
    return row is not None


def compare_source_traceability(
    raw_conn: sqlite3.Connection,
    checks: list[dict[str, Any]],
    sample: dict[str, Any],
) -> None:
    snapshot = norm_text(sample.get(C["snapshot"]))
    add_check(
        checks,
        sample,
        "来源追溯",
        "策略行原始快照ID",
        snapshot,
        "raw_snapshot success",
        "pass" if raw_snapshot_exists(raw_conn, snapshot) else "fail",
        "核对 advisor_monitor.sqlite raw_snapshot",
    )


def build_gffunds_metadata_master(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in entity_files(root, "gffunds", "strategy_master"):
        for row in load_jsonl(path):
            sid = str(row.get("source_strategy_id") or "")
            source_id = str(row.get("source_snapshot_id") or "")
            if not sid or not source_id.startswith("gffunds-strategy_metadata-"):
                continue
            old = result.get(sid)
            if old is None or str(row.get("last_seen_at") or path.stem) >= str(old.get("last_seen_at") or ""):
                result[sid] = row
    return result


def channel_overview(conn: sqlite3.Connection, raw_conn: sqlite3.Connection, normalized_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    strategy_t = qident(T["strategy"])
    for channel in CHANNELS:
        total = fetch_one(conn, f'SELECT COUNT(*) AS n, COUNT(DISTINCT {qident(C["inst"])}) AS inst_n FROM {strategy_t} WHERE {qident(C["channel"])}=?', (channel,)) or {}
        status = fetch_one(
            conn,
            f"""
            SELECT
              SUM(CASE WHEN NULLIF(TRIM({qident("投顾费率文本")}), '') IS NOT NULL THEN 1 ELSE 0 END) AS fee_ready,
              SUM(CASE WHEN NULLIF(TRIM({qident("业绩基准文本")}), '') IS NOT NULL THEN 1 ELSE 0 END) AS benchmark_text_ready,
              SUM(CASE WHEN {qident("披露净值基准行数")} > 0 OR {qident("日度业绩基准行数")} > 0 OR {qident("区间基准行数")} > 0 THEN 1 ELSE 0 END) AS benchmark_curve_ready
            FROM {qident(T["status"])}
            WHERE {qident(C["channel"])}=?
            """,
            (channel,),
        ) or {}
        daily = fetch_one(conn, f'SELECT COUNT(*) AS rows, COUNT(DISTINCT {qident(C["uid"])}) AS strategies, MIN({qident(C["trade_date"])}) AS min_date, MAX({qident(C["trade_date"])}) AS max_date FROM {qident(T["daily"])} WHERE {qident(C["channel"])}=?', (channel,)) or {}
        interval = fetch_one(conn, f'SELECT COUNT(*) AS rows, COUNT(DISTINCT {qident(C["uid"])}) AS strategies, MAX({qident(C["as_of"])}) AS max_date FROM {qident(T["interval"])} WHERE {qident(C["channel"])}=?', (channel,)) or {}
        holding = fetch_one(conn, f'SELECT COUNT(*) AS rows, COUNT(DISTINCT {qident(C["uid"])}) AS strategies, MAX({qident(C["holding_date"])}) AS max_date FROM {qident(T["holding"])} WHERE {qident(C["channel"])}=?', (channel,)) or {}
        event = fetch_one(conn, f'SELECT COUNT(*) AS rows, COUNT(DISTINCT {qident(C["uid"])}) AS strategies, MAX({qident(C["rebalance_date"])}) AS max_date FROM {qident(T["event"])} WHERE {qident(C["channel"])}=?', (channel,)) or {}
        delta = fetch_one(conn, f'SELECT COUNT(*) AS rows, COUNT(DISTINCT {qident(C["uid"])}) AS strategies FROM {qident(T["delta"])} WHERE {qident(C["channel"])}=?', (channel,)) or {}
        missing_snapshot = {}
        for table_key in ("strategy", "daily", "interval", "holding", "event", "delta"):
            table = T[table_key]
            if table_key == "delta":
                snapshot_col = C["snapshot"]
            else:
                snapshot_col = C["snapshot"]
            count = fetch_one(
                conn,
                f'SELECT COUNT(*) AS n FROM {qident(table)} WHERE {qident(C["channel"])}=? AND ({qident(snapshot_col)} IS NULL OR TRIM({qident(snapshot_col)}) = "")',
                (channel,),
            )
            missing_snapshot[table_key] = int((count or {}).get("n") or 0)

        raw_success = fetch_one(raw_conn, "SELECT COUNT(*) AS n, MAX(captured_at) AS max_at FROM raw_snapshot WHERE channel_id=? AND parse_status='success'", (channel,)) or {}
        normalized_counts = {}
        for entity in ["strategy_master", "strategy_performance_daily", "strategy_performance_interval", "strategy_fund_snapshot", "strategy_rebalance_event", "strategy_rebalance_fund_delta"]:
            path = latest_entity_file(normalized_root, channel, entity)
            normalized_counts[entity] = {"path": str(path) if path else "", "rows": len(load_jsonl(path))}

        rows.append(
            {
                "渠道ID": channel,
                "策略数": int(total.get("n") or 0),
                "机构数": int(total.get("inst_n") or 0),
                "费率文本覆盖": int(status.get("fee_ready") or 0),
                "基准文本覆盖": int(status.get("benchmark_text_ready") or 0),
                "基准曲线覆盖": int(status.get("benchmark_curve_ready") or 0),
                "日度业绩策略数": int(daily.get("strategies") or 0),
                "日度业绩行数": int(daily.get("rows") or 0),
                "日度业绩日期范围": f"{daily.get('min_date')}~{daily.get('max_date')}",
                "区间业绩策略数": int(interval.get("strategies") or 0),
                "区间业绩行数": int(interval.get("rows") or 0),
                "区间业绩最新日期": interval.get("max_date"),
                "当前持仓策略数": int(holding.get("strategies") or 0),
                "当前持仓行数": int(holding.get("rows") or 0),
                "当前持仓最新日期": holding.get("max_date"),
                "调仓事件策略数": int(event.get("strategies") or 0),
                "调仓事件行数": int(event.get("rows") or 0),
                "调仓事件最新日期": event.get("max_date"),
                "调仓明细策略数": int(delta.get("strategies") or 0),
                "调仓明细行数": int(delta.get("rows") or 0),
                "raw_snapshot成功数": int(raw_success.get("n") or 0),
                "raw_snapshot最新采集时间": raw_success.get("max_at"),
                "原始快照ID缺失": missing_snapshot,
                "最新normalized": normalized_counts,
            }
        )
    return rows


def duplicate_checks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    checks = []
    specs = [
        ("策略信息", [C["uid"]]),
        ("策略日度业绩", [C["uid"], C["trade_date"]]),
        ("策略区间业绩", [C["uid"], C["as_of"], C["interval_code"]]),
        ("策略当前持仓", [C["uid"], C["holding_date"], C["fund_name"]]),
        ("策略调仓事件", [C["event_id"]]),
        ("策略调仓明细", [C["delta_id"]]),
    ]
    for table, cols in specs:
        group_expr = ", ".join(qident(col) for col in cols)
        sql = f"""
        SELECT COUNT(*) AS duplicate_groups
        FROM (
          SELECT {group_expr}, COUNT(*) AS n
          FROM {qident(table)}
          WHERE {qident(C["channel"])} IN ({','.join('?' for _ in CHANNELS)}) {'' if table != '策略调仓明细' else ''}
          GROUP BY {group_expr}
          HAVING COUNT(*) > 1
        )
        """
        if table == "策略调仓明细":
            sql = f"""
            SELECT COUNT(*) AS duplicate_groups
            FROM (
              SELECT {group_expr}, COUNT(*) AS n
              FROM {qident(table)}
              GROUP BY {group_expr}
              HAVING COUNT(*) > 1
            )
            """
            params: tuple[Any, ...] = ()
        else:
            params = CHANNELS
        row = fetch_one(conn, sql, params) or {}
        checks.append({"表": table, "主键口径": "+".join(cols), "重复组数": int(row.get("duplicate_groups") or 0)})
    return checks


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize_sample_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    for row in checks:
        key = (row["渠道ID"], row["投顾机构"], row["渠道策略ID"], row["策略名称"])
        grouped[key][row["状态"]] += 1
    result = []
    for key, counts in sorted(grouped.items()):
        result.append(
            {
                "渠道ID": key[0],
                "投顾机构": key[1],
                "渠道策略ID": key[2],
                "策略名称": key[3],
                "pass": counts.get("pass", 0),
                "fail": counts.get("fail", 0),
                "warn": counts.get("warn", 0),
                "skip": counts.get("skip", 0),
            }
        )
    return result


def build_markdown(summary: dict[str, Any], sample_summary: list[dict[str, Any]], checks: list[dict[str, Any]]) -> str:
    lines = [
        "# 增量更新抽样数据质量核验",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 抽样规则：天天基金、广发基金 App 按渠道-机构分层，每个机构最多 {summary['sample_per_institution']} 个策略。",
        f"- 样本策略数：{summary['sample_strategy_total']}",
        "",
        "## 渠道概览",
    ]
    for row in summary["channel_overview"]:
        lines.extend(
            [
                f"### {row['渠道ID']}",
                f"- 策略数/机构数：{row['策略数']} / {row['机构数']}",
                f"- 费率文本覆盖：{row['费率文本覆盖']}；基准文本覆盖：{row['基准文本覆盖']}；基准曲线覆盖：{row['基准曲线覆盖']}",
                f"- 日度业绩：{row['日度业绩策略数']} 策略、{row['日度业绩行数']} 行，日期 {row['日度业绩日期范围']}",
                f"- 区间业绩：{row['区间业绩策略数']} 策略、{row['区间业绩行数']} 行，最新 {row['区间业绩最新日期']}",
                f"- 当前持仓：{row['当前持仓策略数']} 策略、{row['当前持仓行数']} 行，最新 {row['当前持仓最新日期']}",
                f"- 调仓事件/明细：{row['调仓事件行数']} / {row['调仓明细行数']} 行",
                f"- raw_snapshot：成功 {row['raw_snapshot成功数']}，最新 {row['raw_snapshot最新采集时间']}",
            ]
        )
    fail_rows = [row for row in sample_summary if int(row.get("fail") or 0) > 0]
    warn_rows = [row for row in sample_summary if int(row.get("warn") or 0) > 0]
    lines.extend(
        [
            "",
            "## 抽样核对结论",
            f"- 有失败项样本数：{len(fail_rows)}",
            f"- 有警告项样本数：{len(warn_rows)}",
            f"- 逐项明细：`sample_checks.csv`",
            f"- 样本汇总：`sample_summary.csv`",
        ]
    )
    if fail_rows[:20]:
        lines.append("")
        lines.append("### 失败样本示例")
        for row in fail_rows[:20]:
            lines.append(f"- {row['渠道ID']} / {row['投顾机构']} / {row['渠道策略ID']} / {row['策略名称']}：fail={row['fail']} warn={row['warn']}")
    top_warnings = Counter(row["说明"] or row["字段"] for row in checks if row["状态"] == "warn")
    if top_warnings:
        lines.append("")
        lines.append("### 主要警告类型")
        for note, count in top_warnings.most_common(10):
            lines.append(f"- {note}: {count}")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    generated_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = args.output_root / datetime.now().strftime("%Y-%m-%d") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(args.db_path)
    raw_conn = connect(args.raw_index_db)

    latest_master = {
        channel: index_by(load_jsonl(latest_entity_file(args.normalized_root, channel, "strategy_master")), "source_strategy_id")
        for channel in CHANNELS
    }
    metadata_master = build_gffunds_metadata_master(args.normalized_root)
    normalized_daily = {
        channel: group_by(load_jsonl(latest_entity_file(args.normalized_root, channel, "strategy_performance_daily")), "source_strategy_id")
        for channel in CHANNELS
    }
    normalized_interval = group_by(load_jsonl(latest_entity_file(args.normalized_root, "ttfund", "strategy_performance_interval")), "source_strategy_id")
    normalized_holdings = {
        channel: group_by(load_jsonl(latest_entity_file(args.normalized_root, channel, "strategy_fund_snapshot")), "source_strategy_id")
        for channel in CHANNELS
    }
    normalized_events = {
        channel: group_by(load_jsonl(latest_entity_file(args.normalized_root, channel, "strategy_rebalance_event")), "source_strategy_id")
        for channel in CHANNELS
    }
    normalized_deltas_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for channel in CHANNELS:
        for row in load_jsonl(latest_entity_file(args.normalized_root, channel, "strategy_rebalance_fund_delta")):
            normalized_deltas_by_event[str(row.get("rebalance_event_id") or "")].append(row)

    samples = select_samples(conn, args.sample_per_institution)
    checks: list[dict[str, Any]] = []
    for sample in samples:
        channel = str(sample[C["channel"]])
        compare_strategy_master(checks, sample, latest_master.get(channel, {}), metadata_master)
        compare_daily(conn, checks, sample, normalized_daily.get(channel, {}))
        compare_interval(conn, checks, sample, normalized_interval)
        compare_holdings(conn, checks, sample, normalized_holdings.get(channel, {}))
        compare_rebalance(conn, checks, sample, normalized_events.get(channel, {}), normalized_deltas_by_event)
        compare_source_traceability(raw_conn, checks, sample)

    sample_summary = summarize_sample_checks(checks)
    overview = channel_overview(conn, raw_conn, args.normalized_root)
    duplicate_summary = duplicate_checks(conn)
    status_counts = Counter(row["状态"] for row in checks)
    summary = {
        "generated_at": generated_at,
        "db_path": str(args.db_path),
        "sample_per_institution": args.sample_per_institution,
        "sample_strategy_total": len(samples),
        "check_status_counts": dict(status_counts),
        "sample_with_fail_total": sum(1 for row in sample_summary if int(row.get("fail") or 0) > 0),
        "sample_with_warn_total": sum(1 for row in sample_summary if int(row.get("warn") or 0) > 0),
        "channel_overview": overview,
        "duplicate_summary": duplicate_summary,
    }

    write_csv(output_dir / "sample_checks.csv", checks)
    write_csv(output_dir / "sample_summary.csv", sample_summary)
    write_csv(output_dir / "channel_overview.csv", overview)
    write_csv(output_dir / "duplicate_summary.csv", duplicate_summary)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(build_markdown(summary, sample_summary, checks), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), **summary}, ensure_ascii=False, indent=2))

    conn.close()
    raw_conn.close()


if __name__ == "__main__":
    main()
