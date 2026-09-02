from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))


def configured_root(environment_key: str, fallback: Path) -> Path:
    configured = str(os.environ.get(environment_key) or "").strip()
    return Path(configured).resolve() if configured else fallback.resolve()


RAW_ROOT = configured_root("ADVISOR_RAW_ROOT", PROJECT_ROOT / "data" / "raw")
NORMALIZED_ROOT = configured_root(
    "ADVISOR_NORMALIZED_ROOT",
    PROJECT_ROOT / "data" / "normalized",
)
DATABASE_ROOT = configured_root("ADVISOR_DATABASE_ROOT", PROJECT_ROOT / "data")

from advisor_monitor.collectors.gffunds_public import (  # noqa: E402
    CHANNEL_ID,
    CHANNEL_NAME,
    H5_BASE,
    extract_protocol_fields,
    holding_period_text,
    parse_amount,
    safe_name,
)
from advisor_monitor.gffunds_public_jobs import post_public_json  # noqa: E402
from advisor_monitor.progress import ConsoleProgress  # noqa: E402

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None


DEFAULT_DB_PATH = DATABASE_ROOT / "analysis_zh_current.sqlite"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"


def project_arg(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh GFFunds strategy fee and benchmark metadata from public interfaces.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--strategy-id", action="append", default=[], help="GFJJ strategy id. Repeatable.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--pdf-retries", type=int, default=4)
    parser.add_argument("--refresh-all", action="store_true", help="Download and re-parse protocol PDFs even when local cache exists.")
    parser.add_argument(
        "--stale-days",
        type=int,
        default=7,
        help="Low-frequency strategy metadata refresh period. Missing fee/benchmark or never-collected strategies are still refreshed. Use 0 to refresh all.",
    )
    parser.add_argument("--skip-pdf", action="store_true", help="Only refresh JSON metadata and protocol URLs; do not parse PDFs.")
    parser.add_argument(
        "--pdf-on-missing-only",
        action="store_true",
        help="Parse protocol PDF only when existing fee or benchmark metadata is missing, instead of filling PDF text cache for stale-but-complete rows.",
    )
    parser.add_argument("--skip-db-update", action="store_true")
    parser.add_argument("--skip-status-rebuild", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--result-summary-path", type=Path)
    return parser.parse_args()


def load_strategy_ids(args: argparse.Namespace) -> list[str]:
    if args.strategy_id:
        ids = [str(item).strip().upper() for item in args.strategy_id if str(item).strip()]
    else:
        ids = list(db_existing_metadata(args.db_path))
    deduped: list[str] = []
    for item in ids:
        if item.startswith("GFJJ") and item not in deduped:
            deduped.append(item)
    return deduped[: args.limit] if args.limit and args.limit > 0 else deduped


def db_existing_metadata(db_path: Path) -> dict[str, dict[str, Any]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''
        SELECT "渠道策略ID", "投顾费率", "业绩基准"
        FROM "策略信息"
        WHERE "渠道ID" = ?
        ''',
        (CHANNEL_ID,),
    ).fetchall()
    conn.close()
    return {str(row["渠道策略ID"]): dict(row) for row in rows}


def latest_metadata_capture_by_strategy() -> dict[str, datetime]:
    root = RAW_ROOT / CHANNEL_ID / "strategy_metadata"
    latest: dict[str, datetime] = {}
    if not root.exists():
        return latest
    for path in root.rglob("get_investadvisor_operate_config_byids.json"):
        strategy_id = path.parent.name.strip().upper()
        if not strategy_id.startswith("GFJJ"):
            continue
        try:
            captured = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).astimezone()
        except OSError:
            continue
        current = latest.get(strategy_id)
        if current is None or captured > current:
            latest[strategy_id] = captured
    return latest


def missing_core_metadata(existing: dict[str, dict[str, Any]], strategy_id: str) -> bool:
    current = existing.get(strategy_id, {})
    return not current.get("投顾费率") or not current.get("业绩基准")


def select_strategy_ids_for_refresh(
    strategy_ids: list[str],
    existing: dict[str, dict[str, Any]],
    *,
    stale_days: int,
    refresh_all: bool,
) -> tuple[list[str], dict[str, Any]]:
    if refresh_all or stale_days <= 0:
        return strategy_ids, {
            "mode": "all",
            "stale_days": stale_days,
            "selected_total": len(strategy_ids),
            "skipped_fresh_total": 0,
            "missing_core_total": sum(1 for strategy_id in strategy_ids if missing_core_metadata(existing, strategy_id)),
            "never_collected_total": 0,
            "stale_total": len(strategy_ids),
        }
    latest_by_strategy = latest_metadata_capture_by_strategy()
    now = now_local()
    cutoff = now - timedelta(days=max(1, stale_days))
    selected: list[str] = []
    missing_core_total = 0
    never_collected_total = 0
    stale_total = 0
    skipped_fresh_total = 0
    for strategy_id in strategy_ids:
        if missing_core_metadata(existing, strategy_id):
            missing_core_total += 1
            selected.append(strategy_id)
            continue
        captured = latest_by_strategy.get(strategy_id)
        if captured is None:
            never_collected_total += 1
            selected.append(strategy_id)
            continue
        if captured <= cutoff:
            stale_total += 1
            selected.append(strategy_id)
            continue
        skipped_fresh_total += 1
    return selected, {
        "mode": "missing_or_stale",
        "stale_days": stale_days,
        "selected_total": len(selected),
        "skipped_fresh_total": skipped_fresh_total,
        "missing_core_total": missing_core_total,
        "never_collected_total": never_collected_total,
        "stale_total": stale_total,
        "cutoff": cutoff.isoformat(timespec="seconds"),
    }


def digest_payload(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def protocol_type3(payload: dict[str, Any]) -> dict[str, Any]:
    protocols = payload.get("protocol_list") or []
    return next((item for item in protocols if str(item.get("protocol_type")) == "3"), {}) or {}


def cache_paths(protocol_url: str) -> tuple[Path, Path, Path]:
    digest = hashlib.sha256(protocol_url.encode("utf-8")).hexdigest()[:24]
    cache_dir = RAW_ROOT / CHANNEL_ID / "protocol_cache"
    return cache_dir / f"{digest}.pdf", cache_dir / f"{digest}.txt", cache_dir / f"{digest}.json"


def validate_pdf_bytes(raw_bytes: bytes) -> None:
    if len(raw_bytes) < 1024:
        raise ValueError(f"protocol PDF is too small: {len(raw_bytes)} bytes")
    if not raw_bytes.lstrip().startswith(b"%PDF-"):
        raise ValueError("protocol response is not a PDF")
    if b"%%EOF" not in raw_bytes[-8192:]:
        raise ValueError("protocol PDF is incomplete: EOF marker missing")


def download_bytes(url: str, timeout: int, retries: int) -> tuple[bytes, int]:
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/pdf,*/*",
                "Connection": "close",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw_bytes = response.read()
            validate_pdf_bytes(raw_bytes)
            return raw_bytes, attempt
        except (IncompleteRead, OSError, ValueError) as exc:
            last_error = exc
            if attempt < max(1, retries):
                time.sleep(min(0.75 * (2 ** (attempt - 1)), 5.0))
    raise RuntimeError(f"protocol PDF download failed after {max(1, retries)} attempts: {last_error}")


def extract_pdf_text(raw_bytes: bytes) -> str:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber is not installed; cannot parse GFFunds protocol PDFs.")
    pages: list[str] = []
    with pdfplumber.open(BytesIO(raw_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def parse_protocol_pdf(
    *,
    protocol_url: str,
    protocol_name: str | None,
    strategy_name: str,
    timeout: int,
    pdf_retries: int,
    refresh_all: bool,
    skip_pdf: bool,
    pdf_on_missing_only: bool,
    missing_core_fields: bool,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "protocol_name": protocol_name,
        "protocol_url": protocol_url,
    }
    if not protocol_url or skip_pdf:
        return meta
    pdf_path, text_path, meta_path = cache_paths(protocol_url)
    should_parse = refresh_all or missing_core_fields or ((not pdf_on_missing_only) and not text_path.exists())
    if not should_parse:
        try:
            cached = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except json.JSONDecodeError:
            cached = {}
        protocol_cache_meta = {"pdf_parse_skipped_reason": "existing_core_metadata_complete"}
        if text_path.exists():
            protocol_cache_meta["protocol_text_path"] = str(text_path)
        if pdf_path.exists():
            protocol_cache_meta["protocol_pdf_path"] = str(pdf_path)
        return {**meta, **cached, **protocol_cache_meta}

    download_attempts = 0
    if not pdf_path.exists() or refresh_all:
        raw_bytes, download_attempts = download_bytes(protocol_url, timeout, pdf_retries)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = pdf_path.with_suffix(f".pdf.{os.getpid()}.tmp")
        temp_path.write_bytes(raw_bytes)
        temp_path.replace(pdf_path)
    else:
        raw_bytes = pdf_path.read_bytes()
        try:
            validate_pdf_bytes(raw_bytes)
        except ValueError:
            raw_bytes, download_attempts = download_bytes(protocol_url, timeout, pdf_retries)
            temp_path = pdf_path.with_suffix(f".pdf.{os.getpid()}.tmp")
            temp_path.write_bytes(raw_bytes)
            temp_path.replace(pdf_path)

    text = extract_pdf_text(raw_bytes)
    text_path.write_text(text, encoding="utf-8")
    fields = extract_protocol_fields(text)
    parsed_meta = {
        **meta,
        **fields,
        "protocol_text_path": str(text_path),
        "protocol_pdf_path": str(pdf_path),
        "protocol_cache_key": pdf_path.stem,
        "protocol_download_attempts": download_attempts,
        "strategy_name_for_cache": strategy_name,
    }
    write_json(meta_path, parsed_meta)
    return parsed_meta


def collect_one(
    strategy_id: str,
    *,
    raw_dir: Path,
    existing: dict[str, dict[str, Any]],
    timeout: int,
    pdf_retries: int,
    refresh_all: bool,
    skip_pdf: bool,
    pdf_on_missing_only: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    config_payload = post_public_json(
        "get_investadvisor_operate_config_byids",
        {"session_id": "", "adv_ids": strategy_id},
        timeout=timeout,
    )
    protocol_payload = post_public_json(
        "get_investadvisor_protocol_list",
        {"session_id": "", "adv_id": strategy_id},
        timeout=timeout,
    )
    item_dir = raw_dir / strategy_id
    write_json(item_dir / "get_investadvisor_operate_config_byids.json", config_payload)
    write_json(item_dir / "get_investadvisor_protocol_list.json", protocol_payload)

    if config_payload.get("RETCODE") != "0000" or protocol_payload.get("RETCODE") != "0000":
        return None, {
            "strategy_id": strategy_id,
            "error": f"config_ret={config_payload.get('RETCODE')} protocol_ret={protocol_payload.get('RETCODE')}",
        }

    config_list = config_payload.get("config_list") or []
    config = config_list[0] if config_list else {}
    strategy_name = str(config.get("adv_name") or strategy_id)
    protocol = protocol_type3(protocol_payload)
    protocol_url = str(protocol.get("protocol_url") or "").strip()
    current = existing.get(strategy_id, {})
    missing_core_fields = not current.get("投顾费率") or not current.get("业绩基准")
    try:
        protocol_meta = parse_protocol_pdf(
            protocol_url=protocol_url,
            protocol_name=protocol.get("protocol_name"),
            strategy_name=strategy_name,
            timeout=timeout,
            pdf_retries=pdf_retries,
            refresh_all=refresh_all,
            skip_pdf=skip_pdf,
            pdf_on_missing_only=pdf_on_missing_only,
            missing_core_fields=missing_core_fields,
        )
    except Exception as error:
        protocol_meta = {
            "protocol_name": protocol.get("protocol_name"),
            "protocol_url": protocol_url,
            "protocol_parse_error": str(error),
        }

    snapshot_payload = {"config": config, "protocol": protocol, "protocol_meta": protocol_meta}
    captured_at = now_local().isoformat(timespec="seconds")
    tag_candidates = [
        config.get("adv_type"),
        config.get("adv_risk_level"),
        protocol_meta.get("strategy_target"),
    ]
    tags = []
    for item in tag_candidates:
        text = str(item or "").strip()
        if text and len(text) <= 40 and text not in tags:
            tags.append(text)

    row = {
        "channel_id": CHANNEL_ID,
        "source_strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "advisor_name": CHANNEL_NAME,
        "strategy_type": config.get("adv_type"),
        "risk_level": protocol_meta.get("risk_level") or config.get("adv_risk_level"),
        "launch_date": None,
        "suggested_holding_period": protocol_meta.get("suggested_holding_period")
        or holding_period_text(config.get("recommend_hold_time_value"), config.get("recommend_hold_time_unit")),
        "minimum_amount": parse_amount(protocol_meta.get("minimum_amount_text")),
        "advisory_fee_rate": protocol_meta.get("advisory_fee_rate"),
        "benchmark": protocol_meta.get("benchmark"),
        "tags": tags,
        "strategy_description": protocol_meta.get("strategy_description")
        or protocol_meta.get("strategy_idea")
        or config.get("risk_perfer")
        or config.get("adv_desc"),
        "status": "public",
        "source_url": f"{H5_BASE}/invest-advisor/strategy-detail?showNavBar=true&source_page=other_page&advId={strategy_id}",
        "first_seen_at": captured_at,
        "last_seen_at": captured_at,
        "run_id": None,
        "source_snapshot_id": f"gffunds-strategy_metadata-{strategy_id}-{digest_payload(snapshot_payload)[:16]}",
        "extra": {
            "target_year_min": config.get("target_year_min"),
            "target_year_max": config.get("target_year_max"),
            "target_withdrawal": config.get("target_withdrawal"),
            "risk_center": config.get("risk_center"),
            "adv_risk_score": config.get("adv_risk_score"),
            "protocol_name": protocol_meta.get("protocol_name"),
            "protocol_url": protocol_meta.get("protocol_url"),
            "protocol_text_path": protocol_meta.get("protocol_text_path"),
            "protocol_pdf_path": protocol_meta.get("protocol_pdf_path"),
            "protocol_parse_error": protocol_meta.get("protocol_parse_error"),
            "protocol_download_attempts": protocol_meta.get("protocol_download_attempts"),
            "rebalance_frequency": protocol_meta.get("rebalance_frequency"),
            "strategy_target": protocol_meta.get("strategy_target"),
            "investment_scope": protocol_meta.get("investment_scope"),
        },
    }
    write_json(item_dir / f"parsed_{safe_name(strategy_name)}.json", row)
    return row, None


def update_sqlite(db_path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows or not db_path.exists():
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA busy_timeout=120000")
        conn.execute("BEGIN IMMEDIATE")
        now = now_local().isoformat(timespec="seconds")
        for row in rows:
            conn.execute(
                '''
                UPDATE "策略信息"
                SET "策略名称" = COALESCE(?, "策略名称"),
                    "投顾机构" = COALESCE(?, "投顾机构"),
                    "策略类型" = COALESCE(?, "策略类型"),
                    "风险等级" = COALESCE(?, "风险等级"),
                    "建议持有时长" = COALESCE(?, "建议持有时长"),
                    "起投金额" = COALESCE(?, "起投金额"),
                    "投顾费率" = COALESCE(?, "投顾费率"),
                    "业绩基准" = COALESCE(?, "业绩基准"),
                    "策略描述" = COALESCE(?, "策略描述"),
                    "原始快照ID" = COALESCE(?, "原始快照ID"),
                    "最近入库时间" = ?
                WHERE "渠道ID" = ? AND "渠道策略ID" = ?
                ''',
                [
                    row.get("strategy_name"),
                    row.get("advisor_name"),
                    row.get("strategy_type"),
                    row.get("risk_level"),
                    row.get("suggested_holding_period"),
                    row.get("minimum_amount"),
                    row.get("advisory_fee_rate"),
                    row.get("benchmark"),
                    row.get("strategy_description"),
                    row.get("source_snapshot_id"),
                    now,
                    CHANNEL_ID,
                    row.get("source_strategy_id"),
                ],
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def rebuild_status_table() -> None:
    subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            project_arg(PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_strategy_benchmark_fee_status.py"),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main() -> None:
    args = parse_args()
    all_strategy_ids = load_strategy_ids(args)
    if not all_strategy_ids:
        raise SystemExit("No GFFunds strategy ids found.")

    run_at = now_local()
    day = run_at.strftime("%Y-%m-%d")
    run_id = args.run_id or run_at.strftime("%Y%m%dT%H%M%S%z")
    raw_dir = RAW_ROOT / CHANNEL_ID / "strategy_metadata" / day / run_id
    normalized_path = NORMALIZED_ROOT / CHANNEL_ID / "strategy_master" / day / f"{run_id}.jsonl"
    summary_path = NORMALIZED_ROOT / CHANNEL_ID / "collection_summary" / day / f"{run_id}.json"

    existing = db_existing_metadata(args.db_path)
    strategy_ids, selection = select_strategy_ids_for_refresh(
        all_strategy_ids,
        existing,
        stale_days=args.stale_days,
        refresh_all=args.refresh_all,
    )
    print(f"[INFO] GFFunds strategy metadata update run_id={run_id}")
    print(
        f"[INFO] strategy ids={len(all_strategy_ids)} selected={len(strategy_ids)} "
        f"skipped_fresh={selection.get('skipped_fresh_total')} stale_days={selection.get('stale_days')} workers={args.workers}"
    )
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    progress = ConsoleProgress("广发基金基准及费率更新", len(strategy_ids))
    progress.emit(
        0,
        success=0,
        failed=0,
        extra=f"待刷新 {len(strategy_ids)} | 7天内无需刷新 {selection.get('skipped_fresh_total', 0)} | 并发数 {max(1, args.workers)}",
    )
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                collect_one,
                strategy_id,
                raw_dir=raw_dir,
                existing=existing,
                timeout=args.timeout,
                pdf_retries=args.pdf_retries,
                refresh_all=args.refresh_all,
                skip_pdf=args.skip_pdf,
                pdf_on_missing_only=args.pdf_on_missing_only,
            ): strategy_id
            for strategy_id in strategy_ids
        }
        for index, future in enumerate(as_completed(futures), start=1):
            strategy_id = futures[future]
            row, failure = future.result()
            if row:
                row["run_id"] = run_id
                rows.append(row)
                progress.emit(
                    index,
                    success=len(rows),
                    failed=len(failures),
                    current=strategy_id,
                    extra=(
                        f"费率 {'已取得' if row.get('advisory_fee_rate') else '缺失'} | "
                        f"基准 {'已取得' if row.get('benchmark') else '缺失'}"
                    ),
                )
            else:
                failures.append(failure or {"strategy_id": strategy_id, "error": "unknown"})
                print(f"[WARN] {index}/{len(strategy_ids)} {strategy_id} failed")
                progress.emit(
                    index,
                    success=len(rows),
                    failed=len(failures),
                    current=strategy_id,
                    extra="本策略元数据获取失败",
                )

    rows.sort(key=lambda item: item["source_strategy_id"])
    write_jsonl(normalized_path, rows)
    if not args.skip_db_update:
        update_sqlite(args.db_path, rows)
        if not args.skip_status_rebuild:
            rebuild_status_table()

    summary = {
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "run_id": run_id,
        "captured_at": run_at.isoformat(timespec="seconds"),
        "collector": "update_gffunds_strategy_metadata",
        "strategy_total": len(all_strategy_ids),
        "selected_strategy_total": len(strategy_ids),
        "skipped_fresh_total": selection.get("skipped_fresh_total"),
        "selection": selection,
        "pdf_on_missing_only": bool(args.pdf_on_missing_only),
        "pdf_retries": max(1, args.pdf_retries),
        "success_total": len(rows),
        "failure_total": len(failures),
        "fee_ready_total": sum(1 for row in rows if row.get("advisory_fee_rate")),
        "benchmark_text_ready_total": sum(1 for row in rows if row.get("benchmark")),
        "raw_dir": str(raw_dir),
        "normalized_path": str(normalized_path),
        "failures": failures,
    }
    write_json(summary_path, summary)
    if args.result_summary_path:
        write_json(args.result_summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
