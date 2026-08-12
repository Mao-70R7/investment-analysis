from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized" / "ttfund_fund_nav"

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover normalized ttfund fund NAV files from an already collected raw run directory.")
    parser.add_argument("--raw-run-dir", type=Path, required=True)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    parser.add_argument("--run-id", default="")
    return parser.parse_args()


def clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in {"None", "nan", "null", "--", "-"} else text


def to_float(value: Any) -> float | None:
    text = clean(value).replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def parse_ymd(value: Any) -> str | None:
    text = clean(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return None


def strip_html(value: str) -> str:
    import html

    return html.unescape(TAG_RE.sub("", value)).replace("\xa0", " ").strip()


def parse_apidata_payload(text: str) -> dict[str, Any]:
    marker = 'content:"'
    marker_index = text.find(marker)
    if marker_index < 0:
        raise ValueError("response missing content marker")
    start_index = marker_index + len(marker)
    end_marker = '",records:'
    end_index = text.find(end_marker, start_index)
    if end_index < 0:
        raise ValueError("response missing records marker")
    content = text[start_index:end_index]
    trailer = text[end_index + len('",') :]
    match = re.search(r"records:(?P<records>\d+),pages:(?P<pages>\d+),curpage:(?P<curpage>\d+)", trailer)
    if not match:
        raise ValueError("response missing records/pages metadata")
    return {
        "content": content.replace('\\"', '"').replace("\\/", "/"),
        "records": int(match.group("records")),
        "pages": int(match.group("pages")),
        "curpage": int(match.group("curpage")),
    }


def parse_table_html(table_html: str) -> tuple[list[str], list[list[str]]]:
    thead_match = re.search(r"<thead[^>]*>(.*?)</thead>", table_html, re.IGNORECASE | re.DOTALL)
    tbody_match = re.search(r"<tbody[^>]*>(.*?)</tbody>", table_html, re.IGNORECASE | re.DOTALL)
    if not thead_match or not tbody_match:
        raise ValueError("response content missing table head/body")
    headers = [strip_html(cell) for cell in CELL_RE.findall(thead_match.group(1))]
    rows: list[list[str]] = []
    for row_html in ROW_RE.findall(tbody_match.group(1)):
        cells = [strip_html(cell) for cell in CELL_RE.findall(row_html)]
        if cells:
            rows.append(cells)
    return headers, rows


def value_type_from_headers(headers: list[str]) -> str:
    if "单位净值" in headers:
        return "nav"
    if "每万份收益" in headers:
        return "money_market"
    return "unknown"


def row_as_map(headers: list[str], cells: list[str]) -> dict[str, str | None]:
    return {header: (cells[index].strip() if index < len(cells) else None) for index, header in enumerate(headers)}


def source_url(fund_code: str, page_no: int, per_page: int = 2000) -> str:
    params = {"type": "lsjz", "code": fund_code, "page": page_no, "per": per_page}
    return f"https://fundf10.eastmoney.com/F10DataApi.aspx?{urlencode(params)}"


def snapshot_id(raw_bytes: bytes, fund_code: str, page_no: int) -> str:
    content_hash = hashlib.sha256(raw_bytes).hexdigest()[:12]
    return f"ttfund_fund_nav-recovered-{content_hash}-{fund_code}-{page_no:04d}"


def normalize_rows(
    *,
    fund_code: str,
    metadata: dict[str, Any],
    headers: list[str],
    rows: list[list[str]],
    raw_bytes: bytes,
    page_no: int,
    captured_at: str,
    run_id: str,
) -> list[dict[str, Any]]:
    value_type = value_type_from_headers(headers)
    sid = snapshot_id(raw_bytes, fund_code, page_no)
    normalized: list[dict[str, Any]] = []
    for cells in rows:
        record = row_as_map(headers, cells)
        per_10k_yield = to_float(record.get("每万份收益"))
        if value_type == "nav":
            daily_return = to_float(record.get("日增长率"))
        elif value_type == "money_market" and per_10k_yield is not None:
            daily_return = round(per_10k_yield / 100, 8)
        else:
            daily_return = None
        normalized.append(
            {
                "fund_code": fund_code,
                "fund_name": metadata.get("fund_name"),
                "fund_type": metadata.get("fund_type"),
                "fund_company": metadata.get("fund_company"),
                "trade_date": parse_ymd(record.get("净值日期")),
                "nav": to_float(record.get("单位净值")),
                "accumulated_nav": to_float(record.get("累计净值")),
                "daily_return": daily_return,
                "per_10k_yield": per_10k_yield,
                "seven_day_annualized": to_float(record.get("7日年化收益率（%）")),
                "purchase_status": clean(record.get("申购状态")) or None,
                "redemption_status": clean(record.get("赎回状态")) or None,
                "dividend_info": clean(record.get("分红送配")) or None,
                "value_type": value_type,
                "is_money_market": value_type == "money_market",
                "source_api_url": source_url(fund_code, page_no),
                "source_snapshot_id": sid,
                "run_id": run_id,
                "captured_at": captured_at,
            }
        )
    return normalized


def load_fof_metadata(db_path: Path) -> dict[str, dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT d."基金代码", d."标准基金名称", d."天天基金细分类", d."基金公司"
            FROM "基金标准分类字典" d
            WHERE d."是否FOF" = 1
            """
        ).fetchall()
    finally:
        conn.close()
    return {
        str(row["基金代码"]): {
            "fund_code": str(row["基金代码"]),
            "fund_name": clean(row["标准基金名称"]) or None,
            "fund_type": clean(row["天天基金细分类"]) or None,
            "fund_company": clean(row["基金公司"]) or None,
            "source_channels": ["基金标准分类字典"],
            "source_entities": ["fof_universe"],
        }
        for row in rows
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> None:
    args = parse_args()
    raw_run_dir = args.raw_run_dir.resolve()
    funds_dir = raw_run_dir / "funds"
    if not funds_dir.is_dir():
        raise SystemExit(f"missing raw funds directory: {funds_dir}")
    run_id = args.run_id or f"{raw_run_dir.name}_recovered"
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    day = captured_at[:10]
    metadata_by_code = load_fof_metadata(args.db_path)

    daily_rows: list[dict[str, Any]] = []
    meta_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    recovered_codes: list[str] = []

    for fund_dir in sorted(path for path in funds_dir.iterdir() if path.is_dir()):
        fund_code = fund_dir.name
        metadata = metadata_by_code.get(fund_code) or {"fund_code": fund_code}
        fund_rows: list[dict[str, Any]] = []
        pages_total = 0
        records_total = 0
        headers_seen: list[str] = []
        try:
            for page_path in sorted(fund_dir.glob("page_*.js")):
                match = re.search(r"page_(\d+)", page_path.name)
                page_no = int(match.group(1)) if match else 1
                raw_bytes = page_path.read_bytes()
                payload = parse_apidata_payload(raw_bytes.decode("utf-8", errors="replace"))
                headers, rows = parse_table_html(payload["content"])
                headers_seen = headers_seen or headers
                pages_total = max(pages_total, int(payload.get("pages") or 0), page_no)
                records_total = max(records_total, int(payload.get("records") or 0))
                fund_rows.extend(
                    normalize_rows(
                        fund_code=fund_code,
                        metadata=metadata,
                        headers=headers,
                        rows=rows,
                        raw_bytes=raw_bytes,
                        page_no=page_no,
                        captured_at=captured_at,
                        run_id=run_id,
                    )
                )
            fund_rows.sort(key=lambda row: row.get("trade_date") or "", reverse=True)
            trade_dates = [row["trade_date"] for row in fund_rows if row.get("trade_date")]
            latest_row = fund_rows[0] if fund_rows else {}
            value_type = value_type_from_headers(headers_seen)
            meta_rows.append(
                {
                    "fund_code": fund_code,
                    "fund_name": metadata.get("fund_name"),
                    "fund_type": metadata.get("fund_type"),
                    "fund_company": metadata.get("fund_company"),
                    "source_channels": metadata.get("source_channels"),
                    "source_entities": metadata.get("source_entities"),
                    "value_type": value_type,
                    "records_total": records_total or len(fund_rows),
                    "pages_total": pages_total,
                    "row_total": len(fund_rows),
                    "first_trade_date": min(trade_dates) if trade_dates else None,
                    "last_trade_date": max(trade_dates) if trade_dates else None,
                    "latest_nav": latest_row.get("nav"),
                    "latest_accumulated_nav": latest_row.get("accumulated_nav"),
                    "latest_daily_return": latest_row.get("daily_return"),
                    "latest_per_10k_yield": latest_row.get("per_10k_yield"),
                    "latest_seven_day_annualized": latest_row.get("seven_day_annualized"),
                    "latest_purchase_status": latest_row.get("purchase_status"),
                    "latest_redemption_status": latest_row.get("redemption_status"),
                    "headers": headers_seen,
                    "source_snapshot_ids": sorted({row["source_snapshot_id"] for row in fund_rows}),
                    "run_id": run_id,
                    "captured_at": captured_at,
                }
            )
            daily_rows.extend(fund_rows)
            recovered_codes.append(fund_code)
        except Exception as exc:  # noqa: BLE001
            failures.append({"fund_code": fund_code, "error": f"{type(exc).__name__}: {exc}"})

    history_path = args.normalized_root / "fund_nav_history_daily" / day / f"{run_id}.jsonl"
    meta_path = args.normalized_root / "fund_nav_history_meta" / day / f"{run_id}.jsonl"
    summary_path = args.normalized_root / "collection_summary" / day / f"{run_id}.json"
    write_jsonl(history_path, daily_rows)
    write_jsonl(meta_path, meta_rows)

    target_codes = set(metadata_by_code)
    summary = {
        "channel_id": "ttfund_fund_nav",
        "channel_name": "天天基金/基金历史净值",
        "run_id": run_id,
        "captured_at": captured_at,
        "raw_run_dir": str(raw_run_dir),
        "history_path": str(history_path.resolve()),
        "meta_path": str(meta_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "target_fund_total": len(target_codes),
        "raw_fund_dir_total": len(recovered_codes) + len(failures),
        "successful_fund_total": len(meta_rows),
        "failed_fund_total": len(failures),
        "history_row_total": len(daily_rows),
        "missing_raw_fund_total": len(target_codes - set(recovered_codes)),
        "missing_raw_funds": sorted(target_codes - set(recovered_codes))[:200],
        "failed_funds": failures[:200],
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
