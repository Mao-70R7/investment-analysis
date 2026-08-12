from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
SCRIPT_DIR = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "positive_stock_holding_gap_repair"


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_id() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair latest funds whose asset allocation has stock exposure but same-quarter stock holdings are missing."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--chunk-size", type=int, default=80)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--stock-threshold", type=float, default=0.5)
    parser.add_argument("--subprocess-timeout", type=int, default=1800)
    parser.add_argument("--fail-on-unrepaired", action="store_true")
    return parser.parse_args()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def query_gaps(db_path: Path, stock_threshold: float) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not table_exists(conn, "基金季度资产配置") or not table_exists(conn, "基金季度股票持仓"):
            return []
        name_join = ""
        name_select = "'' AS 基金名称"
        if table_exists(conn, "基金分类快照"):
            name_join = """
            LEFT JOIN (
              SELECT "基金代码", MAX("基金名称") AS "基金名称"
              FROM "基金分类快照"
              GROUP BY "基金代码"
            ) name_map ON name_map."基金代码" = a."基金代码"
            """
            name_select = "COALESCE(name_map.\"基金名称\", '') AS 基金名称"
        rows = conn.execute(
            f"""
            WITH latest_asset AS (
              SELECT "基金代码", MAX("报告期") AS report
              FROM "基金季度资产配置"
              GROUP BY "基金代码"
            ),
            a AS (
              SELECT x.*
              FROM "基金季度资产配置" x
              JOIN latest_asset l
                ON x."基金代码" = l."基金代码" AND x."报告期" = l.report
            ),
            st AS (
              SELECT DISTINCT "基金代码", "报告期" FROM "基金季度股票持仓"
            )
            SELECT a."基金代码" AS 基金代码,
                   {name_select},
                   a."报告期" AS 报告期,
                   ROUND(COALESCE(a."股票占比_百分比", 0), 4) AS 股票占比
            FROM a
            LEFT JOIN st
              ON a."基金代码" = st."基金代码" AND a."报告期" = st."报告期"
            {name_join}
            WHERE COALESCE(a."股票占比_百分比", 0) > ?
              AND st."基金代码" IS NULL
            ORDER BY 股票占比 DESC, a."基金代码"
            """,
            (stock_threshold,),
        ).fetchall()
        return [dict(row) for row in rows]


def chunks(items: list[str], chunk_size: int) -> list[list[str]]:
    size = max(1, chunk_size)
    return [items[index : index + size] for index in range(0, len(items), size)]


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def collect_stock_holdings_for_codes(
    codes: list[str],
    args: argparse.Namespace,
    output_root: Path,
    log_dir: Path,
    round_index: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for chunk_index, code_chunk in enumerate(chunks(codes, args.chunk_size), start=1):
        command = [
            sys.executable,
            "-X",
            "utf8",
            str(SCRIPT_DIR / "采集基金季报持仓明细.py"),
            "--db-path",
            str(args.db_path),
            "--output-root",
            str(output_root / "collection"),
            "--workers",
            str(max(1, args.workers)),
            "--force-refresh",
            "--skip-bond",
            "--progress-every",
            "20",
        ]
        for code in code_chunk:
            command.extend(["--fund-code", code])
        started = time.time()
        proc = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(60, args.subprocess_timeout),
        )
        elapsed = round(time.time() - started, 3)
        log_path = log_dir / f"round_{round_index}_chunk_{chunk_index}.log"
        log_path.write_text(proc.stdout or "", encoding="utf-8")
        results.append(
            {
                "round": round_index,
                "chunk": chunk_index,
                "fund_count": len(code_chunk),
                "returncode": proc.returncode,
                "elapsed_seconds": elapsed,
                "log_path": display_path(log_path),
                "fund_codes": code_chunk,
            }
        )
    return results


def main() -> None:
    args = parse_args()
    output_dir = args.output_root / run_id()
    log_dir = output_dir / "logs"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    before = query_gaps(args.db_path, args.stock_threshold)
    rounds: list[dict[str, Any]] = []
    current_gaps = before
    child_results: list[dict[str, Any]] = []

    for round_index in range(1, max(0, args.max_rounds) + 1):
        if not current_gaps:
            break
        codes = sorted({str(row["基金代码"]) for row in current_gaps if row.get("基金代码")})
        if not codes:
            break
        if round_index > 1 and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)
        round_started = time.time()
        round_children = collect_stock_holdings_for_codes(codes, args, output_dir, log_dir, round_index)
        child_results.extend(round_children)
        current_gaps = query_gaps(args.db_path, args.stock_threshold)
        rounds.append(
            {
                "round": round_index,
                "attempted_fund_count": len(codes),
                "remaining_gap_count": len(current_gaps),
                "elapsed_seconds": round(time.time() - round_started, 3),
                "children": round_children,
                "remaining_examples": current_gaps[:20],
            }
        )

    after = query_gaps(args.db_path, args.stock_threshold)
    child_failures = [row for row in child_results if int(row.get("returncode") or 0) != 0]
    summary = {
        "status": "completed" if not after else "unrepaired_gaps_remaining",
        "generated_at": now_text(),
        "db_path": str(args.db_path),
        "stock_threshold": args.stock_threshold,
        "before_gap_count": len(before),
        "after_gap_count": len(after),
        "before_examples": before[:50],
        "after_examples": after[:50],
        "rounds": rounds,
        "child_failure_count": len(child_failures),
        "child_failures": child_failures[:20],
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": summary["status"], "before_gap_count": len(before), "after_gap_count": len(after), "summary": str(summary_path)}, ensure_ascii=False, indent=2))

    if after and args.fail_on_unrepaired:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
