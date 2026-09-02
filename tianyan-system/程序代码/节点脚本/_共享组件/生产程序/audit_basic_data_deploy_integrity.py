from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "site"

K_STRATEGY_ID = "\u7edf\u4e00\u7b56\u7565ID"
K_REBALANCE_DATE = "\u8c03\u4ed3\u65e5\u671f"
K_REBALANCE_EVENT_ID = "\u8c03\u4ed3\u4e8b\u4ef6ID"
K_PREV_POSITION_DATE = "\u4e0a\u6b21\u4ed3\u4f4d\u65e5\u671f"
K_NEW_POSITION_DATE = "\u672c\u6b21\u4ed3\u4f4d\u65e5\u671f"
K_EVENT_TITLE = "\u8c03\u4ed3\u6807\u9898"
K_EVENT_REASON = "\u8c03\u4ed3\u539f\u56e0"
K_EVENT_SEQUENCE = "\u4e8b\u4ef6\u5e8f\u53f7"
K_EVENT_TIME = "\u4e8b\u4ef6\u65f6\u95f4"
K_STRATEGY_POINTS = "\u7b56\u7565\u8868\u73b0\u70b9"
K_REBALANCE_EVENTS = "\u8c03\u4ed3\u4e8b\u4ef6"
T_REBALANCE_EVENT = "\u7b56\u7565\u8c03\u4ed3\u4e8b\u4ef6"
T_REBALANCE_DETAIL = "\u7b56\u7565\u8c03\u4ed3\u660e\u7ec6"
K_FUND_CODE = "\u57fa\u91d1\u4ee3\u7801"
K_FUND_NAME = "\u57fa\u91d1\u540d\u79f0"
K_GROUP_NAME = "\u5206\u7ec4\u540d\u79f0"
K_BEFORE_WEIGHT = "\u8c03\u524d\u6743\u91cd_\u767e\u5206\u6bd4"
K_AFTER_WEIGHT = "\u8c03\u540e\u6743\u91cd_\u767e\u5206\u6bd4"
K_WEIGHT_DELTA = "\u6743\u91cd\u53d8\u5316_\u767e\u5206\u6bd4"
K_ACTION = "\u8c03\u4ed3\u52a8\u4f5c"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit deployed basic_data output against the analysis DB.")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--recent-days", type=int, default=30)
    parser.add_argument("--min-strategies", type=int, default=100)
    return parser.parse_args()


def load_summary(summary_path: Path) -> dict[str, Any]:
    if summary_path.suffix == ".gz":
        with gzip.open(summary_path, "rt", encoding="utf-8") as handle:
            text = handle.read()
    else:
        text = summary_path.read_text(encoding="utf-8")
    match = re.search(r"window\.__BASIC_DATA__\.summary\s*=\s*(.*);\s*$", text, re.S)
    if not match:
        raise ValueError(f"cannot parse basic summary JS: {summary_path}")
    return json.loads(match.group(1))


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def rows_by_date(rows: list[dict[str, Any]], date_key: str) -> dict[str, int]:
    counts = Counter(str(row.get(date_key) or "") for row in rows if row.get(date_key))
    return dict(sorted(counts.items()))


def fetch_recent_source_events(conn: sqlite3.Connection, recent_days: int) -> tuple[str | None, str | None, list[dict[str, Any]], list[list[str]]]:
    latest = conn.execute(f'SELECT MAX("{K_REBALANCE_DATE}") FROM "{T_REBALANCE_EVENT}"').fetchone()[0]
    latest_date = parse_iso_date(latest)
    if latest_date is None:
        return latest, None, [], []
    cutoff = latest_date - timedelta(days=max(1, recent_days) - 1)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f'''
        SELECT
          "{K_REBALANCE_EVENT_ID}" AS event_id,
          "{K_STRATEGY_ID}" AS strategy_id,
          "{K_REBALANCE_DATE}" AS event_date,
          COALESCE("{K_PREV_POSITION_DATE}", '') AS prev_date,
          COALESCE("{K_NEW_POSITION_DATE}", '') AS new_date,
          COALESCE("{K_EVENT_TITLE}", '') AS title,
          COALESCE("{K_EVENT_REASON}", '') AS reason,
          COALESCE("{K_EVENT_SEQUENCE}", -9999) AS seq,
          COALESCE("{K_EVENT_TIME}", '') AS event_time
        FROM "{T_REBALANCE_EVENT}"
        WHERE "{K_REBALANCE_DATE}" >= ?
          AND COALESCE(TRIM("{K_REBALANCE_EVENT_ID}"), '') <> ''
        ORDER BY "{K_REBALANCE_DATE}", "{K_REBALANCE_EVENT_ID}"
        ''',
        (cutoff.isoformat(),),
    ).fetchall()
    source_events = [dict(row) for row in rows]
    duplicate_groups = recent_duplicate_signature_groups(conn, source_events)
    return str(latest), cutoff.isoformat(), source_events, duplicate_groups


def detail_value(value: Any) -> Any:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    return round(number, 6)


def recent_duplicate_signature_groups(conn: sqlite3.Connection, events: list[dict[str, Any]]) -> list[list[str]]:
    event_ids = [str(row["event_id"]) for row in events if row.get("event_id")]
    if not event_ids:
        return []
    details_by_event: dict[str, list[tuple[Any, ...]]] = {event_id: [] for event_id in event_ids}
    for start in range(0, len(event_ids), 800):
        chunk = event_ids[start : start + 800]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f'''
            SELECT
              "{K_REBALANCE_EVENT_ID}" AS event_id,
              COALESCE("{K_FUND_CODE}", '') AS fund_code,
              COALESCE("{K_FUND_NAME}", '') AS fund_name,
              COALESCE("{K_GROUP_NAME}", '') AS group_name,
              "{K_BEFORE_WEIGHT}" AS before_weight,
              "{K_AFTER_WEIGHT}" AS after_weight,
              "{K_WEIGHT_DELTA}" AS weight_delta,
              COALESCE("{K_ACTION}", '') AS action
            FROM "{T_REBALANCE_DETAIL}"
            WHERE "{K_REBALANCE_EVENT_ID}" IN ({placeholders})
            ''',
            chunk,
        ).fetchall()
        for row in rows:
            details_by_event[str(row["event_id"])].append(
                (
                    str(row["fund_code"]).strip(),
                    str(row["fund_name"]).strip(),
                    str(row["group_name"]).strip(),
                    detail_value(row["before_weight"]),
                    detail_value(row["after_weight"]),
                    detail_value(row["weight_delta"]),
                    str(row["action"]).strip(),
                )
            )
    grouped: dict[tuple[Any, ...], list[str]] = {}
    for event in events:
        event_id = str(event["event_id"])
        detail_rows = details_by_event.get(event_id) or []
        if not detail_rows:
            continue
        detail_payload = json.dumps(sorted(detail_rows), ensure_ascii=False, separators=(",", ":"))
        detail_sig = hashlib.sha1(detail_payload.encode("utf-8")).hexdigest()
        key = (
            event.get("strategy_id") or "",
            event.get("event_date") or "",
            event.get("prev_date") or "",
            event.get("new_date") or "",
            event.get("title") or "",
            event.get("reason") or "",
            str(event.get("seq") or ""),
            event.get("event_time") or "",
            detail_sig,
        )
        grouped.setdefault(key, []).append(event_id)
    return sorted([sorted(ids) for ids in grouped.values() if len(ids) > 1])


def find_duplicates(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if value and count > 1)


def main() -> None:
    args = parse_args()
    report_root = args.report_root.resolve()
    data_dir = report_root / "basic_data" / "data"
    summary_candidates = (
        data_dir / "basic_summary_core.js.gz",
        data_dir / "basic_summary_core.js",
        data_dir / "basic_summary.js.gz",
        data_dir / "basic_summary.js",
    )
    summary_path = next((path for path in summary_candidates if path.is_file()), summary_candidates[0])
    details_dir = report_root / "basic_data" / "data" / "details"
    assets_path = report_root / "basic_data" / "assets" / "insights.js"
    failures: list[str] = []
    warnings: list[str] = []

    if not summary_path.exists():
        raise SystemExit(
            "missing basic summary pack; checked: "
            + ", ".join(str(path) for path in summary_candidates)
        )
    if not details_dir.is_dir():
        raise SystemExit(f"missing strategy details directory: {details_dir}")
    if not assets_path.exists():
        raise SystemExit(f"missing insights.js: {assets_path}")
    if not args.db_path.exists():
        raise SystemExit(f"missing analysis DB: {args.db_path}")

    summary = load_summary(summary_path)
    strategies = summary.get("strategies") or []
    insight = summary.get("insightData") or {}
    summary_events = summary.get("rebalanceEvents") or []
    insight_events = insight.get(K_REBALANCE_EVENTS) or []
    strategy_points = insight.get(K_STRATEGY_POINTS) or []
    detail_files = [*details_dir.rglob("*.js"), *details_dir.rglob("*.js.gz")]

    if len(strategies) < args.min_strategies:
        failures.append(f"strategy rows below minimum: {len(strategies)} < {args.min_strategies}")
    if len(detail_files) < len(strategies):
        failures.append(f"detail files below strategy rows: {len(detail_files)} < {len(strategies)}")
    if not summary_events:
        failures.append("summary.rebalanceEvents is empty")
    if not insight_events:
        failures.append("insightData rebalance events is empty")
    if not strategy_points:
        failures.append("insightData strategy points is empty")

    with sqlite3.connect(args.db_path) as conn:
        latest_date, cutoff_date, source_events, duplicate_source_groups = fetch_recent_source_events(conn, args.recent_days)

    source_ids = {row["event_id"] for row in source_events}
    display_strategy_ids = {str(row.get(K_STRATEGY_ID) or "") for row in strategy_points}
    expected_insight_ids = {
        row["event_id"]
        for row in source_events
        if str(row.get("strategy_id") or "") in display_strategy_ids
    }
    summary_recent = [
        row for row in summary_events
        if cutoff_date and str(row.get(K_REBALANCE_DATE) or "") >= cutoff_date
    ]
    insight_recent = [
        row for row in insight_events
        if cutoff_date and str(row.get(K_REBALANCE_DATE) or "") >= cutoff_date
    ]
    summary_ids = {str(row.get(K_REBALANCE_EVENT_ID) or "") for row in summary_recent}
    insight_ids = {str(row.get(K_REBALANCE_EVENT_ID) or "") for row in insight_recent}

    missing_summary_ids = sorted(source_ids - summary_ids)
    missing_insight_ids = sorted(expected_insight_ids - insight_ids)
    duplicate_summary_ids = find_duplicates([str(row.get(K_REBALANCE_EVENT_ID) or "") for row in summary_recent])
    duplicate_insight_ids = find_duplicates([str(row.get(K_REBALANCE_EVENT_ID) or "") for row in insight_recent])

    if missing_summary_ids:
        failures.append(f"summary.rebalanceEvents missing {len(missing_summary_ids)} recent source event IDs")
    if missing_insight_ids:
        failures.append(f"insightData rebalance events missing {len(missing_insight_ids)} displayable event IDs")
    if duplicate_summary_ids:
        failures.append(f"summary.rebalanceEvents has {len(duplicate_summary_ids)} duplicate recent event IDs")
    if duplicate_insight_ids:
        failures.append(f"insightData rebalance events has {len(duplicate_insight_ids)} duplicate recent event IDs")
    if duplicate_source_groups:
        failures.append(f"analysis DB has {len(duplicate_source_groups)} duplicate recent rebalance event signature groups")
    if len(source_events) > len(summary_recent):
        warnings.append("summary recent event count is below source count; inspect missing IDs if failures are present")

    result = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "passed" if not failures else "failed",
        "report_root": str(report_root),
        "db_path": str(args.db_path.resolve()),
        "recent_days": args.recent_days,
        "latest_rebalance_date": latest_date,
        "cutoff_date": cutoff_date,
        "strategy_rows": len(strategies),
        "detail_files": len(detail_files),
        "strategy_points": len(strategy_points),
        "summary_rebalance_events": len(summary_events),
        "insight_rebalance_events": len(insight_events),
        "source_recent_events": len(source_events),
        "summary_recent_events": len(summary_recent),
        "insight_recent_events": len(insight_recent),
        "expected_insight_recent_events": len(expected_insight_ids),
        "source_recent_by_date": rows_by_date(
            [{K_REBALANCE_DATE: row["event_date"]} for row in source_events],
            K_REBALANCE_DATE,
        ),
        "summary_recent_by_date": rows_by_date(summary_recent, K_REBALANCE_DATE),
        "insight_recent_by_date": rows_by_date(insight_recent, K_REBALANCE_DATE),
        "missing_summary_event_ids": missing_summary_ids[:50],
        "missing_insight_event_ids": missing_insight_ids[:50],
        "duplicate_summary_event_ids": duplicate_summary_ids[:50],
        "duplicate_insight_event_ids": duplicate_insight_ids[:50],
        "duplicate_source_signature_groups": duplicate_source_groups[:20],
        "failures": failures,
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
