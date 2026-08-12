from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from business_naming import canonical_advisor_institution


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized" / "gffunds"
CHANNEL_ID = "gffunds"
RULE_VERSION = "gffunds_parent_child_v2_20260807"
MIN_CURVE_OVERLAP = 120
MIN_CURVE_MATCH_RATIO = 0.99
CURVE_TOLERANCE = 1e-6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="识别并保存可追溯的策略母子关系。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    parser.add_argument("--result-path", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-relationships", action="store_true", help="在控制台输出完整关系明细；默认仅写结果文件。")
    return parser.parse_args()


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def normalize_family_name(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"第?[零〇一二三四五六七八九十百千万\d]{1,5}期", "", text)
    return re.sub(r"\d{1,5}期", "", text)


def relationship_advisor_key(value: Any) -> str:
    """Use the approved business name at the relationship matching boundary."""

    return normalize_text(canonical_advisor_institution(value))


def load_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def latest_master_rows(normalized_root: Path) -> dict[str, dict[str, Any]]:
    master_root = normalized_root / "strategy_master"
    latest: dict[str, tuple[tuple[str, str, str], dict[str, Any]]] = {}
    if not master_root.is_dir():
        return {}
    for path in sorted(master_root.rglob("*.jsonl")):
        for row in load_jsonl(path):
            strategy_id = str(row.get("source_strategy_id") or "").strip()
            if not strategy_id:
                continue
            rank = (
                str(row.get("last_seen_at") or row.get("first_seen_at") or ""),
                str(row.get("run_id") or ""),
                str(path),
            )
            if strategy_id not in latest or rank > latest[strategy_id][0]:
                latest[strategy_id] = (rank, row)
    return {strategy_id: value[1] for strategy_id, value in latest.items()}


def latest_performance_series(
    normalized_root: Path,
    strategy_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    performance_root = normalized_root / "strategy_performance_daily"
    unresolved = set(strategy_ids)
    result: dict[str, list[dict[str, Any]]] = {}
    if not performance_root.is_dir() or not unresolved:
        return result
    paths = sorted(
        performance_root.rglob("*.jsonl"),
        key=lambda path: (path.parent.name, path.name),
        reverse=True,
    )
    for path in paths:
        if not unresolved:
            break
        rows_in_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in load_jsonl(path):
            strategy_id = str(row.get("source_strategy_id") or "").strip()
            if strategy_id in unresolved:
                rows_in_file[strategy_id].append(row)
        for strategy_id, rows in rows_in_file.items():
            if rows:
                result[strategy_id] = rows
                unresolved.discard(strategy_id)
    return result


def curve_signature(rows: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, float] = {}
    for row in rows:
        trade_date = str(row.get("trade_date") or "")[:10]
        raw_value = row.get("cumulative_return")
        if raw_value in (None, ""):
            raw_value = row.get("nav")
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if trade_date:
            values[trade_date] = value
    return values


def compare_curves(child_rows: list[dict[str, Any]], parent_rows: list[dict[str, Any]]) -> dict[str, Any]:
    child = curve_signature(child_rows)
    parent = curve_signature(parent_rows)
    overlap = sorted(set(child) & set(parent))
    matched = sum(1 for day in overlap if abs(child[day] - parent[day]) <= CURVE_TOLERANCE)
    recent_overlap = overlap[-20:]
    recent_matched = sum(1 for day in recent_overlap if abs(child[day] - parent[day]) <= CURVE_TOLERANCE)
    overlap_ratio = len(overlap) / min(len(child), len(parent)) if child and parent else 0.0
    match_ratio = matched / len(overlap) if overlap else 0.0
    recent_match_ratio = recent_matched / len(recent_overlap) if recent_overlap else 0.0
    is_alias = (
        len(overlap) >= MIN_CURVE_OVERLAP
        and overlap_ratio >= 0.8
        and match_ratio >= MIN_CURVE_MATCH_RATIO
        and recent_match_ratio == 1.0
    )
    digest = ""
    if overlap:
        payload = "|".join(f"{day}:{child[day]:.8f}" for day in overlap)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return {
        "overlapCount": len(overlap),
        "overlapRatio": round(overlap_ratio, 6),
        "matchRatio": round(match_ratio, 6),
        "recentMatchRatio": round(recent_match_ratio, 6),
        "curveHash": digest,
        "isAlias": is_alias,
    }


def latest_holding_fingerprints(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        '''
        SELECT "统一策略ID", "持仓日期", COALESCE("基金代码", ''), COALESCE("基金名称", ''),
               "基金权重_百分比"
        FROM "策略当前持仓"
        WHERE "渠道ID"=? AND "基金权重_百分比" IS NOT NULL AND "基金权重_百分比">0
        ORDER BY "统一策略ID", "持仓日期", "基金代码", "基金名称"
        ''',
        (CHANNEL_ID,),
    ).fetchall()
    latest_date: dict[str, str] = {}
    grouped: dict[str, dict[str, list[tuple[str, str, float]]]] = defaultdict(lambda: defaultdict(list))
    for strategy_id, holding_date, fund_code, fund_name, weight in rows:
        sid = str(strategy_id)
        day = str(holding_date or "")
        latest_date[sid] = max(latest_date.get(sid, ""), day)
        grouped[sid][day].append((str(fund_code), str(fund_name), round(float(weight), 6)))
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, day in latest_date.items():
        payload = json.dumps(grouped[strategy_id][day], ensure_ascii=False, separators=(",", ":"))
        result[strategy_id] = {
            "date": day,
            "count": len(grouped[strategy_id][day]),
            "hash": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
        }
    return result


def rebalance_fingerprints(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        '''
        SELECT "统一策略ID", "调仓日期"
        FROM "策略调仓事件"
        WHERE "渠道ID"=? AND "调仓日期" IS NOT NULL
        GROUP BY "统一策略ID", "调仓日期"
        ORDER BY "统一策略ID", "调仓日期"
        ''',
        (CHANNEL_ID,),
    ).fetchall()
    grouped: dict[str, list[str]] = defaultdict(list)
    for strategy_id, trade_date in rows:
        grouped[str(strategy_id)].append(str(trade_date))
    return {
        strategy_id: {
            "count": len(dates),
            "hash": hashlib.sha256("|".join(dates).encode("utf-8")).hexdigest()[:16],
        }
        for strategy_id, dates in grouped.items()
    }


def ensure_table(conn: sqlite3.Connection, temporary: bool = False) -> None:
    table_prefix = "CREATE TEMP TABLE" if temporary else "CREATE TABLE IF NOT EXISTS"
    foreign_keys = "" if temporary else ''',
            FOREIGN KEY ("子策略ID") REFERENCES "策略信息"("统一策略ID"),
            FOREIGN KEY ("母策略ID") REFERENCES "策略信息"("统一策略ID")
    '''
    conn.execute(
        f'''
        {table_prefix} "策略关系" (
            "子策略ID" TEXT PRIMARY KEY,
            "母策略ID" TEXT NOT NULL,
            "渠道ID" TEXT NOT NULL,
            "关系类型" TEXT NOT NULL,
            "官方业绩策略ID" TEXT,
            "持仓策略ID" TEXT,
            "调仓策略ID" TEXT,
            "置信度" TEXT NOT NULL,
            "置信分" REAL NOT NULL,
            "关系状态" TEXT NOT NULL,
            "证据JSON" TEXT NOT NULL,
            "规则版本" TEXT NOT NULL,
            "连续不一致次数" INTEGER NOT NULL DEFAULT 0,
            "首次识别时间" TEXT NOT NULL,
            "最近复核时间" TEXT NOT NULL
            {foreign_keys}
        )
        '''
    )
    if not temporary:
        conn.execute('CREATE INDEX IF NOT EXISTS "idx_策略关系_母策略" ON "策略关系"("母策略ID", "关系状态")')
        conn.execute('CREATE INDEX IF NOT EXISTS "idx_策略关系_官方业绩" ON "策略关系"("官方业绩策略ID", "关系状态")')


def build_relationships(
    conn: sqlite3.Connection,
    normalized_root: Path,
) -> list[dict[str, Any]]:
    master = latest_master_rows(normalized_root)
    parents_by_protocol: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    children: list[str] = []
    for strategy_id, row in master.items():
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        strategy_type = str(extra.get("ds_adv_type") or "")
        protocol_name = normalize_text(extra.get("protocol_name"))
        key = (str(row.get("channel_id") or CHANNEL_ID), relationship_advisor_key(row.get("advisor_name")), protocol_name)
        if strategy_type == "2" and protocol_name:
            parents_by_protocol[key].append(strategy_id)
        elif strategy_type == "3" and protocol_name:
            children.append(strategy_id)

    candidate_pairs: list[tuple[str, str]] = []
    for child_id in children:
        row = master[child_id]
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        key = (
            str(row.get("channel_id") or CHANNEL_ID),
            relationship_advisor_key(row.get("advisor_name")),
            normalize_text(extra.get("protocol_name")),
        )
        for parent_id in parents_by_protocol.get(key, []):
            if parent_id != child_id:
                candidate_pairs.append((child_id, parent_id))

    performance_ids = {strategy_id for pair in candidate_pairs for strategy_id in pair}
    performance = latest_performance_series(normalized_root, performance_ids)
    holdings = latest_holding_fingerprints(conn)
    rebalances = rebalance_fingerprints(conn)
    by_child: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for child_id, parent_id in candidate_pairs:
        child = master[child_id]
        parent = master[parent_id]
        child_extra = child.get("extra") if isinstance(child.get("extra"), dict) else {}
        parent_extra = parent.get("extra") if isinstance(parent.get("extra"), dict) else {}
        curve = compare_curves(performance.get(child_id, []), performance.get(parent_id, []))
        holding_match = bool(
            holdings.get(f"{CHANNEL_ID}__{child_id}")
            and holdings.get(f"{CHANNEL_ID}__{parent_id}")
            and holdings[f"{CHANNEL_ID}__{child_id}"]["hash"] == holdings[f"{CHANNEL_ID}__{parent_id}"]["hash"]
        )
        rebalance_match = bool(
            rebalances.get(f"{CHANNEL_ID}__{child_id}")
            and rebalances.get(f"{CHANNEL_ID}__{parent_id}")
            and rebalances[f"{CHANNEL_ID}__{child_id}"]["hash"] == rebalances[f"{CHANNEL_ID}__{parent_id}"]["hash"]
        )
        score = 55.0  # ds_adv_type=3 -> 2 and identical non-empty protocol name
        if curve["isAlias"]:
            score += 30.0
        if holding_match:
            score += 10.0
        if rebalance_match:
            score += 10.0
        if normalize_family_name(child.get("strategy_name")) in normalize_family_name(parent.get("strategy_name")):
            score += 5.0
        score = min(score, 100.0)
        evidence = {
            "childSourceStrategyId": child_id,
            "parentSourceStrategyId": parent_id,
            "childDsAdvType": child_extra.get("ds_adv_type"),
            "parentDsAdvType": parent_extra.get("ds_adv_type"),
            "childAdvisorName": child.get("advisor_name"),
            "parentAdvisorName": parent.get("advisor_name"),
            "canonicalAdvisorName": canonical_advisor_institution(child.get("advisor_name")),
            "protocolName": child_extra.get("protocol_name"),
            "protocolNameMatched": True,
            "curve": curve,
            "holdingMatched": holding_match,
            "holdingChild": holdings.get(f"{CHANNEL_ID}__{child_id}"),
            "holdingParent": holdings.get(f"{CHANNEL_ID}__{parent_id}"),
            "rebalanceMatched": rebalance_match,
            "rebalanceChild": rebalances.get(f"{CHANNEL_ID}__{child_id}"),
            "rebalanceParent": rebalances.get(f"{CHANNEL_ID}__{parent_id}"),
        }
        by_child[child_id].append(
            {
                "child": f"{CHANNEL_ID}__{child_id}",
                "parent": f"{CHANNEL_ID}__{parent_id}",
                "score": score,
                "official_alias": bool(curve["isAlias"]),
                "holding_alias": holding_match,
                "rebalance_alias": rebalance_match,
                "evidence": evidence,
            }
        )

    detected: list[dict[str, Any]] = []
    for child_id, candidates in by_child.items():
        candidates.sort(key=lambda item: (-item["score"], item["parent"]))
        best = candidates[0]
        tied = len(candidates) > 1 and candidates[1]["score"] == best["score"]
        strong = best["official_alias"] or (best["holding_alias"] and best["rebalance_alias"])
        if tied or best["score"] < 75 or not strong:
            continue
        detected.append(best)
    return detected


def persist_relationships(conn: sqlite3.Connection, detected: list[dict[str, Any]]) -> dict[str, int]:
    timestamp = now_text()
    existing = {
        row[0]: {
            "parent": row[1],
            "official": row[2],
            "mismatches": int(row[3] or 0),
            "first": row[4],
        }
        for row in conn.execute(
            'SELECT "子策略ID", "母策略ID", "官方业绩策略ID", "连续不一致次数", "首次识别时间" FROM "策略关系"'
        )
    }
    detected_children: set[str] = set()
    for item in detected:
        child = item["child"]
        parent = item["parent"]
        detected_children.add(child)
        previous = existing.get(child, {})
        same_parent = previous.get("parent") == parent
        official_alias = bool(item["official_alias"])
        mismatch_count = 0 if official_alias else ((previous.get("mismatches", 0) + 1) if same_parent else 1)
        official_source = parent if official_alias else (
            previous.get("official") if same_parent and mismatch_count < 2 else None
        )
        confidence = "high" if item["score"] >= 90 else "medium"
        conn.execute(
            '''
            INSERT INTO "策略关系" (
                "子策略ID", "母策略ID", "渠道ID", "关系类型", "官方业绩策略ID", "持仓策略ID", "调仓策略ID",
                "置信度", "置信分", "关系状态", "证据JSON", "规则版本", "连续不一致次数", "首次识别时间", "最近复核时间"
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            ON CONFLICT("子策略ID") DO UPDATE SET
                "母策略ID"=excluded."母策略ID",
                "渠道ID"=excluded."渠道ID",
                "关系类型"=excluded."关系类型",
                "官方业绩策略ID"=excluded."官方业绩策略ID",
                "持仓策略ID"=excluded."持仓策略ID",
                "调仓策略ID"=excluded."调仓策略ID",
                "置信度"=excluded."置信度",
                "置信分"=excluded."置信分",
                "关系状态"=excluded."关系状态",
                "证据JSON"=excluded."证据JSON",
                "规则版本"=excluded."规则版本",
                "连续不一致次数"=excluded."连续不一致次数",
                "最近复核时间"=excluded."最近复核时间"
            ''',
            (
                child,
                parent,
                CHANNEL_ID,
                "母策略期次",
                official_source,
                parent if item["holding_alias"] else None,
                parent if item["rebalance_alias"] else None,
                confidence,
                item["score"],
                json.dumps(item["evidence"], ensure_ascii=False, sort_keys=True),
                RULE_VERSION,
                mismatch_count,
                previous.get("first") or timestamp,
                timestamp,
            ),
        )

    for child, previous in existing.items():
        if child in detected_children:
            continue
        mismatch_count = int(previous.get("mismatches") or 0) + 1
        status = "review" if mismatch_count >= 2 else "active"
        conn.execute(
            'UPDATE "策略关系" SET "连续不一致次数"=?, "关系状态"=?, "最近复核时间"=? WHERE "子策略ID"=?',
            (mismatch_count, status, timestamp, child),
        )
    return {
        "detected": len(detected),
        "officialAliases": sum(1 for item in detected if item["official_alias"]),
        "holdingAliases": sum(1 for item in detected if item["holding_alias"]),
        "rebalanceAliases": sum(1 for item in detected if item["rebalance_alias"]),
    }


def atomic_json(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    args = parse_args()
    if not args.db_path.is_file():
        raise SystemExit(f"database not found: {args.db_path}")
    with sqlite3.connect(args.db_path, timeout=120) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=120000")
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='策略关系'"
        ).fetchone() is not None
        ensure_table(conn, temporary=bool(args.dry_run and not table_exists))
        detected = build_relationships(conn, args.normalized_root)
        conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
        counts = persist_relationships(conn, detected)
        rows = [
            dict(zip([column[0] for column in cursor.description], row))
            for cursor in [conn.execute('SELECT * FROM "策略关系" ORDER BY "子策略ID"')]
            for row in cursor.fetchall()
        ]
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    result = {
        "generatedAt": now_text(),
        "dryRun": bool(args.dry_run),
        "database": str(args.db_path.resolve()),
        "normalizedRoot": str(args.normalized_root.resolve()),
        "ruleVersion": RULE_VERSION,
        "counts": counts,
        "relationships": rows,
    }
    atomic_json(args.result_path, result)
    console_result = result if args.print_relationships else {
        key: value for key, value in result.items() if key != "relationships"
    }
    if not args.print_relationships:
        console_result["relationshipSample"] = rows[:3]
    print(json.dumps(console_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
