from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import random
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "public_fund_company_mapping"
COMPANY_INDEX_URL = "https://fund.eastmoney.com/Company/default.html"
COMPANY_DETAIL_URL = "https://fund.eastmoney.com/Company/{company_id}.html"
CN_TZ = timezone(timedelta(hours=8))
HTTP_LOCAL = threading.local()
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"


@dataclass(frozen=True)
class CompanyTarget:
    company_id: str
    company_name: str


class FundCodeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_fund_cell = False
        self.in_code_anchor = False
        self.code_buffer: list[str] = []
        self.codes: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "td" and "fund-name-code" in classes:
            self.in_fund_cell = True
        elif self.in_fund_cell and tag == "a" and "code" in classes:
            self.in_code_anchor = True
            self.code_buffer = []

    def handle_data(self, data: str) -> None:
        if self.in_code_anchor:
            self.code_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.in_code_anchor:
            code = "".join(self.code_buffer).strip()
            if re.fullmatch(r"\d{6}", code):
                self.codes.add(code)
            self.in_code_anchor = False
            self.code_buffer = []
        elif tag == "td" and self.in_fund_cell:
            self.in_fund_cell = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect public-fund company mappings from Eastmoney company fund lists.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--timeout-sec", type=int, default=45)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--status-interval-sec", type=int, default=300)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--company-id", action="append", default=[])
    return parser.parse_args()


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def http_session() -> requests.Session:
    session = getattr(HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=12, pool_maxsize=12, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
        )
        HTTP_LOCAL.session = session
    return session


def fetch_text(url: str, *, timeout: int, retries: int, referer: str = "") -> tuple[str, int, float]:
    last_error: Exception | None = None
    elapsed = 0.0
    for attempt in range(1, retries + 1):
        started = time.perf_counter()
        try:
            response = http_session().get(
                url,
                timeout=timeout,
                headers={"Referer": referer} if referer else None,
            )
            elapsed += time.perf_counter() - started
            response.raise_for_status()
            response.encoding = "utf-8"
            if len(response.content) < 500:
                raise RuntimeError(f"response too short: {len(response.content)} bytes")
            return response.text, attempt, elapsed
        except Exception as error:  # pragma: no cover - network dependent
            elapsed += time.perf_counter() - started
            last_error = error
            if attempt < retries:
                time.sleep(min(4.0, 0.7 * 2 ** (attempt - 1)) + random.uniform(0.0, 0.4))
    raise RuntimeError(f"request failed after {retries} attempts: {last_error}")


def parse_companies(text: str) -> list[CompanyTarget]:
    companies: dict[str, str] = {}
    pattern = re.compile(r'<a\s+href="/Company/(\d+)\.html"[^>]*>([^<]+)</a>', re.I)
    for company_id, raw_name in pattern.findall(text):
        company_name = html.unescape(raw_name).strip()
        if company_name:
            companies.setdefault(company_id, company_name)
    return [CompanyTarget(company_id, companies[company_id]) for company_id in sorted(companies)]


def parse_fund_codes(text: str) -> list[str]:
    parser = FundCodeParser()
    parser.feed(text)
    return sorted(parser.codes)


def ensure_schema(conn: sqlite3.Connection) -> None:
    dictionary_columns = {row[1] for row in conn.execute('PRAGMA table_info("基金标准分类字典")')}
    for column, column_type in [("基金公司来源", "TEXT"), ("基金公司更新时间", "TEXT")]:
        if column not in dictionary_columns:
            conn.execute(f'ALTER TABLE "基金标准分类字典" ADD COLUMN "{column}" {column_type}')
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS "公募基金公司映射采集" (
          "基金公司ID" TEXT PRIMARY KEY,
          "基金公司" TEXT NOT NULL,
          "采集状态" TEXT NOT NULL,
          "旗下基金代码数" INTEGER NOT NULL DEFAULT 0,
          "命中标准字典数" INTEGER NOT NULL DEFAULT 0,
          "本次补齐数" INTEGER NOT NULL DEFAULT 0,
          "原始证据包路径" TEXT,
          "原始响应SHA256" TEXT,
          "失败原因" TEXT,
          "run_id" TEXT NOT NULL,
          "更新时间" TEXT NOT NULL
        )
        '''
    )
    conn.commit()


def collect_company(target: CompanyTarget, *, timeout: int, retries: int) -> dict[str, Any]:
    url = COMPANY_DETAIL_URL.format(company_id=target.company_id)
    try:
        text, attempts, elapsed = fetch_text(
            url,
            timeout=timeout,
            retries=retries,
            referer=COMPANY_INDEX_URL,
        )
        codes = parse_fund_codes(text)
        return {
            "companyId": target.company_id,
            "companyName": target.company_name,
            "url": url,
            "status": "成功" if codes else "无旗下公募基金",
            "codes": codes,
            "attempts": attempts,
            "elapsed": round(elapsed, 4),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text": text,
            "error": "" if codes else "公司页面未列示公募基金代码",
        }
    except Exception as error:  # pragma: no cover - network dependent
        return {
            "companyId": target.company_id,
            "companyName": target.company_name,
            "url": url,
            "status": "失败",
            "codes": [],
            "attempts": retries,
            "elapsed": None,
            "sha256": "",
            "text": "",
            "error": str(error)[:2000],
        }


def write_raw_batch(run_dir: Path, batch_number: int, results: list[dict[str, Any]]) -> Path:
    output = run_dir / f"raw_batch_{batch_number:04d}.jsonl.gz"
    temporary = output.with_suffix(output.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(output)
    return output.resolve()


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def coverage(conn: sqlite3.Connection) -> tuple[int, int]:
    return conn.execute(
        '''
        SELECT COUNT(*),
               SUM(CASE WHEN TRIM(COALESCE("基金公司", '')) <> '' THEN 1 ELSE 0 END)
        FROM "基金标准分类字典"
        '''
    ).fetchone()


def commit_batch(
    conn: sqlite3.Connection,
    *,
    results: list[dict[str, Any]],
    raw_path: Path,
    run_id: str,
    updated_at: str,
) -> tuple[int, int, int]:
    dictionary_codes = {row[0] for row in conn.execute('SELECT "基金代码" FROM "基金标准分类字典"')}
    matched = 0
    filled = 0
    conflicts = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for result in results:
            result_matched = 0
            result_filled = 0
            for code in result["codes"]:
                if code not in dictionary_codes:
                    continue
                result_matched += 1
                existing = conn.execute(
                    'SELECT "基金公司" FROM "基金标准分类字典" WHERE "基金代码"=?',
                    (code,),
                ).fetchone()
                existing_company = str(existing[0] or "").strip() if existing else ""
                if existing_company and existing_company != result["companyName"]:
                    conflicts += 1
                if not existing_company:
                    result_filled += 1
                conn.execute(
                    '''
                    UPDATE "基金标准分类字典"
                    SET "基金公司"=?, "基金公司ID"=?, "基金公司来源"=?, "基金公司更新时间"=?
                    WHERE "基金代码"=?
                    ''',
                    (
                        result["companyName"],
                        result["companyId"],
                        "天天基金公司旗下基金页",
                        updated_at,
                        code,
                    ),
                )
            matched += result_matched
            filled += result_filled
            conn.execute(
                '''
                INSERT INTO "公募基金公司映射采集" (
                  "基金公司ID", "基金公司", "采集状态", "旗下基金代码数", "命中标准字典数", "本次补齐数",
                  "原始证据包路径", "原始响应SHA256", "失败原因", "run_id", "更新时间"
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT("基金公司ID") DO UPDATE SET
                  "基金公司"=excluded."基金公司", "采集状态"=excluded."采集状态",
                  "旗下基金代码数"=excluded."旗下基金代码数", "命中标准字典数"=excluded."命中标准字典数",
                  "本次补齐数"=excluded."本次补齐数", "原始证据包路径"=excluded."原始证据包路径",
                  "原始响应SHA256"=excluded."原始响应SHA256", "失败原因"=excluded."失败原因",
                  "run_id"=excluded."run_id", "更新时间"=excluded."更新时间"
                ''',
                (
                    result["companyId"],
                    result["companyName"],
                    result["status"],
                    len(result["codes"]),
                    result_matched,
                    result_filled,
                    str(raw_path),
                    result["sha256"],
                    result["error"],
                    run_id,
                    updated_at,
                ),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return matched, filled, conflicts


def main() -> int:
    args = parse_args()
    started_at = now_cn()
    run_id = started_at.strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / "summary.json"

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_schema(conn)
    total_funds, before_covered = coverage(conn)

    index_text, index_attempts, index_elapsed = fetch_text(
        COMPANY_INDEX_URL,
        timeout=max(1, args.timeout_sec),
        retries=max(1, args.retries),
    )
    with gzip.open(run_dir / "company_index.html.gz", "wt", encoding="utf-8") as handle:
        handle.write(index_text)
    targets = parse_companies(index_text)
    selected_ids = {str(value).strip() for value in args.company_id if str(value).strip()}
    if selected_ids:
        targets = [target for target in targets if target.company_id in selected_ids]
    elif not args.refresh:
        completed_ids = {
            row[0]
            for row in conn.execute(
                '''
                SELECT "基金公司ID" FROM "公募基金公司映射采集"
                WHERE "采集状态" IN ('成功', '无旗下公募基金')
                '''
            )
        }
        targets = [target for target in targets if target.company_id not in completed_ids]
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]

    summary: dict[str, Any] = {
        "runId": run_id,
        "startedAt": started_at.isoformat(timespec="seconds"),
        "status": "running",
        "dbPath": str(args.db.resolve()),
        "indexUrl": COMPANY_INDEX_URL,
        "indexAttempts": index_attempts,
        "indexElapsedSeconds": round(index_elapsed, 4),
        "fundTotal": total_funds,
        "companyCoveredBefore": before_covered,
        "targets": len(targets),
        "completed": 0,
        "success": 0,
        "noData": 0,
        "failed": 0,
        "matchedFundCodes": 0,
        "filledFundCompanies": 0,
        "conflicts": 0,
        "committedBatches": 0,
        "failures": [],
    }
    write_summary(summary_path, summary)
    started = time.perf_counter()
    last_status_at = started
    batch_size = max(1, args.batch_size)
    try:
        for batch_number, batch_start in enumerate(range(0, len(targets), batch_size), start=1):
            batch = targets[batch_start : batch_start + batch_size]
            batch_results: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
                futures = {
                    executor.submit(
                        collect_company,
                        target,
                        timeout=max(1, args.timeout_sec),
                        retries=max(1, args.retries),
                    ): target
                    for target in batch
                }
                for batch_completed, future in enumerate(as_completed(futures), start=1):
                    result = future.result()
                    batch_results.append(result)
                    observed = summary["completed"] + batch_completed
                    now = time.perf_counter()
                    if now - last_status_at >= max(1, args.status_interval_sec):
                        elapsed = max(0.001, now - started)
                        rate = observed / elapsed * 60.0
                        eta = (len(targets) - observed) / rate if rate > 0 else None
                        print(
                            f"[network {observed}/{len(targets)}] committed={summary['completed']} "
                            f"rate={rate:.2f}/min eta_min={eta:.1f} last={result['companyName']} "
                            f"status={result['status']} codes={len(result['codes'])}",
                            flush=True,
                        )
                        last_status_at = now

            raw_path = write_raw_batch(run_dir, batch_number, batch_results)
            matched, filled, conflicts = commit_batch(
                conn,
                results=batch_results,
                raw_path=raw_path,
                run_id=run_id,
                updated_at=now_cn().isoformat(timespec="seconds"),
            )
            summary["completed"] += len(batch_results)
            summary["success"] += sum(result["status"] == "成功" for result in batch_results)
            summary["noData"] += sum(result["status"] == "无旗下公募基金" for result in batch_results)
            summary["failed"] += sum(result["status"] not in {"成功", "无旗下公募基金"} for result in batch_results)
            summary["matchedFundCodes"] += matched
            summary["filledFundCompanies"] += filled
            summary["conflicts"] += conflicts
            summary["committedBatches"] += 1
            summary["failures"].extend(
                {
                    "companyId": result["companyId"],
                    "companyName": result["companyName"],
                    "error": result["error"],
                }
                for result in batch_results
                if result["status"] not in {"成功", "无旗下公募基金"}
            )
            elapsed = max(0.001, time.perf_counter() - started)
            rate = summary["completed"] / elapsed * 60.0
            eta = (len(targets) - summary["completed"]) / rate if rate > 0 else 0.0
            summary["elapsedSeconds"] = round(elapsed, 3)
            summary["ratePerMinute"] = round(rate, 3)
            summary["etaMinutes"] = round(eta, 2)
            write_summary(summary_path, summary)
            print(
                f"[commit batch={batch_number}] completed={summary['completed']}/{len(targets)} "
                f"success={summary['success']} no_data={summary['noData']} failed={summary['failed']} "
                f"filled={summary['filledFundCompanies']} "
                f"conflicts={summary['conflicts']} rate={rate:.2f}/min eta_min={eta:.1f}",
                flush=True,
            )
    except KeyboardInterrupt:
        summary["status"] = "interrupted"
        summary["finishedAt"] = now_cn().isoformat(timespec="seconds")
        write_summary(summary_path, summary)
        conn.close()
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 130

    total_funds, after_covered = coverage(conn)
    summary.update(
        {
            "status": "completed" if summary["failed"] == 0 else "completed_with_failures",
            "finishedAt": now_cn().isoformat(timespec="seconds"),
            "companyCoveredAfter": after_covered,
            "companyMissingAfter": total_funds - after_covered,
            "coverageRate": round(after_covered / total_funds, 6) if total_funds else 0.0,
            "summaryPath": str(summary_path.resolve()),
        }
    )
    write_summary(summary_path, summary)
    conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
