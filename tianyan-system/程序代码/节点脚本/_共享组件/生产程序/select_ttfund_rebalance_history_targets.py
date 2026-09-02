from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_CACHE_ROOT = PROJECT_ROOT / "data" / "raw" / "device_cache"
CHANNEL_ID = "ttfund"
APP_ID = "funda91a99886abf7e"

T_REBALANCE_EVENT = "\u7b56\u7565\u8c03\u4ed3\u4e8b\u4ef6"
T_STRATEGY_DAILY = "\u7b56\u7565\u65e5\u5ea6\u4e1a\u7ee9"
K_CHANNEL_ID = "\u6e20\u9053ID"
K_SOURCE_STRATEGY_ID = "\u6e20\u9053\u7b56\u7565ID"
K_REBALANCE_DATE = "\u8c03\u4ed3\u65e5\u671f"
K_TRADE_DATE = "\u4ea4\u6613\u65e5\u671f"

LATEST_CACHE_RE = re.compile(r"^adjuseHouseList(?!His)(?P<sid>[A-Za-z0-9]+)_")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select TTFund strategies whose latest adjustment cache is newer than analysis DB."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--strategy-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--report-file", type=Path, required=True)
    parser.add_argument("--reference-date", help="YYYY-MM-DD used to resolve MM-DD dateStr values.")
    parser.add_argument("--include-same-date", action="store_true")
    return parser.parse_args()


def load_strategy_ids(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if value and not value.startswith("#") and value not in ids:
            ids.append(value)
    return ids


def load_json(path: Path) -> Any | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        if isinstance(payload, str):
            return json.loads(payload)
        return payload
    except Exception:
        return None


def parse_ymd(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "--":
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return None


def parse_mmdd_with_reference(value: Any, reference_date: str | None) -> str | None:
    if not reference_date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reference_date):
        return None
    if value is None:
        return None
    text = str(value).strip()
    match = re.search(r"(?P<month>\d{2})-(?P<day>\d{2})", text)
    if not match:
        return None
    base_year = int(reference_date[:4])
    parsed = f"{base_year:04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
    if parsed > reference_date:
        return f"{base_year - 1:04d}{parsed[4:]}"
    return parsed


def latest_payload_date(payload: Any, reference_date: str | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    groups = payload.get("adjustList") or payload.get("arr") or []
    if not groups:
        return None
    return parse_ymd(payload.get("dateStr")) or parse_mmdd_with_reference(payload.get("dateStr"), reference_date)


def cache_latest_by_strategy(cache_root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not cache_root.exists():
        return result
    for path in cache_root.rglob(f"adjuseHouseList*_{APP_ID}.0"):
        if path.name.startswith("adjuseHouseListHis"):
            continue
        match = LATEST_CACHE_RE.match(path.name)
        if not match:
            continue
        strategy_id = match.group("sid")
        previous = result.get(strategy_id)
        if previous is None or path.stat().st_mtime > previous.stat().st_mtime:
            result[strategy_id] = path
    return result


def db_latest_rebalance_dates(conn: sqlite3.Connection) -> dict[str, str]:
    sql = f'''
        SELECT "{K_SOURCE_STRATEGY_ID}", MAX("{K_REBALANCE_DATE}")
        FROM "{T_REBALANCE_EVENT}"
        WHERE "{K_CHANNEL_ID}"=?
        GROUP BY "{K_SOURCE_STRATEGY_ID}"
    '''
    return {str(row[0]): str(row[1]) for row in conn.execute(sql, (CHANNEL_ID,)) if row[0] and row[1]}


def db_reference_date(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute(
            f'SELECT MAX("{K_TRADE_DATE}") FROM "{T_STRATEGY_DAILY}" WHERE "{K_CHANNEL_ID}"=?',
            (CHANNEL_ID,),
        ).fetchone()
        return str(row[0]) if row and row[0] else None
    except sqlite3.Error:
        return None


def main() -> None:
    args = parse_args()
    strategy_ids = load_strategy_ids(args.strategy_file)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.report_file.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(args.db_path) as conn:
        db_dates = db_latest_rebalance_dates(conn)
        reference_date = args.reference_date or db_reference_date(conn) or datetime.now().strftime("%Y-%m-%d")

    latest_cache = cache_latest_by_strategy(args.cache_root)
    targets: list[str] = []
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def count(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    for strategy_id in strategy_ids:
        cache_path = latest_cache.get(strategy_id)
        db_date = db_dates.get(strategy_id)
        payload = load_json(cache_path) if cache_path else None
        latest_date = latest_payload_date(payload, reference_date)
        reason = "no_latest_cache"
        selected = False
        if latest_date:
            if not db_date:
                reason = "latest_exists_db_missing"
                selected = True
            elif latest_date > db_date:
                reason = "latest_newer_than_db"
                selected = True
            elif args.include_same_date and latest_date == db_date:
                reason = "latest_same_as_db_included"
                selected = True
            else:
                reason = "db_current"
        elif cache_path and payload is None:
            reason = "latest_cache_parse_failed"
        elif cache_path:
            reason = "latest_cache_without_adjustment"

        if selected:
            targets.append(strategy_id)
        count(reason)
        rows.append(
            {
                "strategy_id": strategy_id,
                "selected": selected,
                "reason": reason,
                "latest_cache_date": latest_date,
                "db_latest_rebalance_date": db_date,
                "cache_path": str(cache_path) if cache_path else None,
            }
        )

    args.output_file.write_text("\n".join(targets) + ("\n" if targets else ""), encoding="utf-8")
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reference_date": reference_date,
        "strategy_total": len(strategy_ids),
        "latest_cache_strategy_total": len(latest_cache),
        "selected_history_target_total": len(targets),
        "counts": dict(sorted(counts.items())),
        "output_file": str(args.output_file),
        "rows": rows,
    }
    args.report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
