from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_INPUT = PROJECT_ROOT / "outputs" / "fof_h1_strategy_ranking" / "20260630_recheck_20260702" / "fof_h1_strategy_ranking_data.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "raw" / "fof_f10_benchmark"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
TZ = timezone(timedelta(hours=8))


def now_text() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def default_run_id() -> str:
    return datetime.now(TZ).strftime("%Y%m%dT%H%M%S%z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<script\b.*?</script>", "", text, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", "", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def table_value(page_text: str, label: str) -> str:
    pattern = rf"<th[^>]*>\s*{re.escape(label)}\s*</th>\s*<td[^>]*>(.*?)</td>"
    match = re.search(pattern, page_text, flags=re.I | re.S)
    return clean_text(match.group(1)) if match else ""


def inception_date_value(page_text: str) -> str:
    candidates = [table_value(page_text, "成立日期"), table_value(page_text, "成立日期/规模")]
    label_match = re.search(r"<label[^>]*>\s*成立日期[：:]\s*<span[^>]*>(.*?)</span>", page_text, flags=re.I | re.S)
    if label_match:
        candidates.append(clean_text(label_match.group(1)))
    for value in candidates:
        match = re.search(r"\b(19|20)\d{2}[-年/]\d{1,2}[-月/]\d{1,2}", value)
        if not match:
            continue
        parts = [int(item) for item in re.findall(r"\d+", match.group(0))[:3]]
        if len(parts) == 3:
            try:
                return date(*parts).isoformat()
            except ValueError:
                continue
    return ""


def decode_response(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "业绩比较基准" in text or "基金类型" in text:
            return text
    return data.decode("utf-8", "ignore")


def extract_page_fields(page_text: str) -> dict[str, str]:
    return {
        "F10基金类型": table_value(page_text, "基金类型"),
        "业绩比较基准": table_value(page_text, "业绩比较基准"),
        "跟踪标的": table_value(page_text, "跟踪标的"),
        "基金简称": table_value(page_text, "基金简称"),
        "基金全称": table_value(page_text, "基金全称"),
        "成立日期": inception_date_value(page_text),
    }


@dataclass(frozen=True)
class FundTask:
    code: str
    name: str
    public_class: str
    current_bucket: str
    company: str


def load_fof_tasks(input_data: Path, limit: int | None = None, codes: set[str] | None = None) -> list[FundTask]:
    data = read_json(input_data)
    rows = data.get("fofRows") or []
    tasks: list[FundTask] = []
    seen: set[str] = set()
    for row in rows:
        code = str(row.get("基金代码") or "").strip()
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            continue
        if codes and code not in codes:
            continue
        seen.add(code)
        tasks.append(
            FundTask(
                code=code,
                name=str(row.get("基金名称") or ""),
                public_class=str(row.get("天天基金细分类") or ""),
                current_bucket=str(row.get("FOF可比分类") or ""),
                company=str(row.get("基金公司") or ""),
            )
        )
        if limit and len(tasks) >= limit:
            break
    return tasks


def load_fof_tasks_from_db(db_path: Path, limit: int | None = None, codes: set[str] | None = None) -> list[FundTask]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT d."基金代码" AS code,
                   COALESCE(
                     NULLIF(TRIM(p."基金名称"), ''),
                     NULLIF(TRIM(d."标准基金名称"), ''),
                     d."基金代码"
                   ) AS name,
                   COALESCE(
                     NULLIF(TRIM(p."FOF公开分类"), ''),
                     NULLIF(TRIM(d."天天基金细分类"), '')
                   ) AS public_class,
                   COALESCE(
                     NULLIF(TRIM(p."FOF基准细分分类"), ''),
                     NULLIF(TRIM(p."FOF公开分类"), ''),
                     NULLIF(TRIM(d."天天基金细分类"), '')
                   ) AS current_bucket,
                   COALESCE(
                     NULLIF(TRIM(p."基金公司"), ''),
                     NULLIF(TRIM(d."基金公司"), '')
                   ) AS company
            FROM "基金标准分类字典" d
            LEFT JOIN "FOF产品绩效快照" p ON p."基金代码" = d."基金代码"
            WHERE d."是否FOF" = 1
            ORDER BY d."基金代码"
            """
        ).fetchall()
    finally:
        conn.close()
    tasks: list[FundTask] = []
    seen: set[str] = set()
    for row in rows:
        code = str(row["code"] or "").strip()
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            continue
        if codes and code not in codes:
            continue
        seen.add(code)
        tasks.append(
            FundTask(
                code=code,
                name=str(row["name"] or ""),
                public_class=str(row["public_class"] or ""),
                current_bucket=str(row["current_bucket"] or ""),
                company=str(row["company"] or ""),
            )
        )
        if limit and len(tasks) >= limit:
            break
    return tasks


def request_f10_page(code: str, timeout: int) -> tuple[str, str, int]:
    errors: list[str] = []
    for scheme in ("http", "https"):
        url = f"{scheme}://fundf10.eastmoney.com/jbgk_{code}.html"
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 advisor-monitor/fof-f10-benchmark",
                "Referer": "https://fund.eastmoney.com/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
                "Connection": "close",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                try:
                    data = response.read()
                except IncompleteRead as exc:
                    if not exc.partial:
                        raise
                    data = exc.partial
                    status = 206
            return url, decode_response(data), status
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors))


def collect_one(task: FundTask, html_dir: Path, timeout: int, retries: int, force: bool) -> dict[str, Any]:
    raw_path = html_dir / f"{task.code}.html"
    started_at = now_text()
    page_text = ""
    page_url = f"http://fundf10.eastmoney.com/jbgk_{task.code}.html"
    status_code: int | None = None
    errors: list[str] = []

    if raw_path.exists() and not force:
        page_text = raw_path.read_text(encoding="utf-8", errors="ignore")
        status = "cached"
    else:
        status = "failed"
        for attempt in range(1, retries + 2):
            try:
                page_url, page_text, status_code = request_f10_page(task.code, timeout)
                if "业绩比较基准" in page_text or "基金类型" in page_text:
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_text(page_text, encoding="utf-8")
                    status = "success"
                    break
                errors.append(f"attempt {attempt}: page missing expected labels")
            except Exception as exc:  # noqa: BLE001 - network collection needs error text.
                errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            time.sleep(min(2.0 * attempt, 6.0))

    fields = extract_page_fields(page_text) if page_text else {}
    if status in {"success", "cached"} and not (fields.get("业绩比较基准") or fields.get("F10基金类型")):
        status = "parsed_empty"

    return {
        "基金代码": task.code,
        "基金名称": task.name,
        "FOF公开分类": task.public_class,
        "原FOF可比分类": task.current_bucket,
        "基金公司": task.company,
        "F10基金类型": fields.get("F10基金类型", ""),
        "业绩比较基准": fields.get("业绩比较基准", ""),
        "跟踪标的": fields.get("跟踪标的", ""),
        "F10基金简称": fields.get("基金简称", ""),
        "F10基金全称": fields.get("基金全称", ""),
        "F10成立日期": fields.get("成立日期", ""),
        "F10页面URL": page_url,
        "原始HTML路径": str(raw_path.relative_to(PROJECT_ROOT)) if raw_path.exists() else "",
        "采集状态": status,
        "HTTP状态": status_code,
        "采集开始时间": started_at,
        "采集完成时间": now_text(),
        "错误信息": "; ".join(errors[-3:]),
    }


def write_sqlite(db_path: Path, rows: list[dict[str, Any]], run_id: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS "FOF基金F10基准" (
              "基金代码" TEXT PRIMARY KEY,
              "基金名称" TEXT,
              "FOF公开分类" TEXT,
              "原FOF可比分类" TEXT,
              "基金公司" TEXT,
              "F10基金类型" TEXT,
              "业绩比较基准" TEXT,
              "跟踪标的" TEXT,
              "F10基金简称" TEXT,
              "F10基金全称" TEXT,
              "F10成立日期" TEXT,
              "F10页面URL" TEXT,
              "原始HTML路径" TEXT,
              "采集状态" TEXT,
              "HTTP状态" INTEGER,
              "错误信息" TEXT,
              "采集批次" TEXT,
              "更新时间" TEXT
            )
            """
        )
        now = now_text()
        for row in rows:
            conn.execute(
                """
                INSERT INTO "FOF基金F10基准" (
                  "基金代码","基金名称","FOF公开分类","原FOF可比分类","基金公司",
                  "F10基金类型","业绩比较基准","跟踪标的","F10基金简称","F10基金全称",
                  "F10成立日期","F10页面URL","原始HTML路径","采集状态","HTTP状态",
                  "错误信息","采集批次","更新时间"
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT("基金代码") DO UPDATE SET
                  "基金名称"=excluded."基金名称",
                  "FOF公开分类"=excluded."FOF公开分类",
                  "原FOF可比分类"=excluded."原FOF可比分类",
                  "基金公司"=excluded."基金公司",
                  "F10基金类型"=excluded."F10基金类型",
                  "业绩比较基准"=excluded."业绩比较基准",
                  "跟踪标的"=excluded."跟踪标的",
                  "F10基金简称"=excluded."F10基金简称",
                  "F10基金全称"=excluded."F10基金全称",
                  "F10成立日期"=excluded."F10成立日期",
                  "F10页面URL"=excluded."F10页面URL",
                  "原始HTML路径"=excluded."原始HTML路径",
                  "采集状态"=excluded."采集状态",
                  "HTTP状态"=excluded."HTTP状态",
                  "错误信息"=excluded."错误信息",
                  "采集批次"=excluded."采集批次",
                  "更新时间"=excluded."更新时间"
                """,
                [
                    row.get("基金代码"),
                    row.get("基金名称"),
                    row.get("FOF公开分类"),
                    row.get("原FOF可比分类"),
                    row.get("基金公司"),
                    row.get("F10基金类型"),
                    row.get("业绩比较基准"),
                    row.get("跟踪标的"),
                    row.get("F10基金简称"),
                    row.get("F10基金全称"),
                    row.get("F10成立日期"),
                    row.get("F10页面URL"),
                    row.get("原始HTML路径"),
                    row.get("采集状态"),
                    row.get("HTTP状态"),
                    row.get("错误信息"),
                    run_id,
                    now,
                ],
            )
        conn.commit()
    finally:
        conn.close()


def summarize(rows: list[dict[str, Any]], started_at: str, run_id: str) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    public_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row.get("采集状态") or ""] = status_counts.get(row.get("采集状态") or "", 0) + 1
        public_counts[row.get("FOF公开分类") or "未分类"] = public_counts.get(row.get("FOF公开分类") or "未分类", 0) + 1
    benchmark_rows = sum(1 for row in rows if row.get("业绩比较基准"))
    type_rows = sum(1 for row in rows if row.get("F10基金类型"))
    return {
        "runId": run_id,
        "采集开始时间": started_at,
        "采集完成时间": now_text(),
        "FOF总数": len(rows),
        "F10基金类型覆盖数": type_rows,
        "业绩比较基准覆盖数": benchmark_rows,
        "业绩比较基准覆盖率": round(benchmark_rows / len(rows), 6) if rows else None,
        "采集状态分布": dict(sorted(status_counts.items())),
        "FOF公开分类分布": dict(sorted(public_counts.items())),
        "失败样例": [
            {
                "基金代码": row.get("基金代码"),
                "基金名称": row.get("基金名称"),
                "采集状态": row.get("采集状态"),
                "错误信息": row.get("错误信息"),
            }
            for row in rows
            if row.get("采集状态") not in {"success", "cached"}
        ][:50],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Eastmoney/Tiantian F10 benchmark text for the all-market FOF universe.")
    parser.add_argument("--input-data", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--source", choices=["db", "json"], default="db", help="Task source. db uses 基金标准分类字典 是否FOF=1.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=18)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--codes", help="Comma-separated fund codes for targeted repair.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args()

    started_at = now_text()
    codes = {item.strip() for item in (args.codes or "").split(",") if item.strip()}
    tasks = (
        load_fof_tasks_from_db(args.db_path, args.limit, codes or None)
        if args.source == "db"
        else load_fof_tasks(args.input_data, args.limit, codes or None)
    )
    run_dir = args.output_root / args.run_id
    html_dir = run_dir / "html"
    rows: list[dict[str, Any]] = []

    print(f"[collect] run_id={args.run_id} tasks={len(tasks)} workers={args.workers}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(collect_one, task, html_dir, args.timeout, args.retries, args.force): task
            for task in tasks
        }
        for idx, future in enumerate(concurrent.futures.as_completed(future_map), 1):
            task = future_map[future]
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001 - keep the fund in the output.
                row = {
                    "基金代码": task.code,
                    "基金名称": task.name,
                    "FOF公开分类": task.public_class,
                    "原FOF可比分类": task.current_bucket,
                    "基金公司": task.company,
                    "采集状态": "failed",
                    "错误信息": f"{type(exc).__name__}: {exc}",
                    "采集开始时间": started_at,
                    "采集完成时间": now_text(),
                }
            rows.append(row)
            if idx == len(tasks) or idx % 25 == 0:
                ok = sum(1 for item in rows if item.get("业绩比较基准"))
                failed = sum(1 for item in rows if item.get("采集状态") not in {"success", "cached"})
                print(f"[collect] progress={idx}/{len(tasks)} benchmark={ok} non_success={failed}", flush=True)

    output_json = run_dir / "fof_f10_benchmarks.json"
    if codes and output_json.exists():
        existing = read_json(output_json).get("rows") or []
        merged = {str(row.get("基金代码") or ""): row for row in existing}
        for row in rows:
            merged[str(row.get("基金代码") or "")] = row
        rows = list(merged.values())

    rows.sort(key=lambda item: item.get("基金代码") or "")
    summary = summarize(rows, started_at, args.run_id)
    payload = {"meta": summary, "rows": rows}
    write_json(output_json, payload)
    write_json(run_dir / "fof_f10_benchmarks_summary.json", summary)
    latest = args.output_root / "latest_fof_f10_benchmarks.json"
    write_json(latest, payload)
    if not args.skip_db:
        write_sqlite(args.db_path, rows, args.run_id)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
