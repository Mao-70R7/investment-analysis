from __future__ import annotations

import argparse
import json
import socket
from datetime import date
from pathlib import Path
from typing import Any

from probe_qieman_device import active_locks, now_local, write_json


PROBE_ROOT = Path(__file__).resolve().parent
DEFAULT_STRATEGY_CODES = ("ZH013136", "ZH112601", "SI000193")
HISTORY_OPERATIONS = ("GetStrategyNavHistory", "GetStrategyAdjustments")


def request_local_proxy(port: int, message: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    chunks: list[bytes] = []
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
        client.settimeout(timeout)
        client.sendall(encoded)
        client.shutdown(socket.SHUT_WR)
        while True:
            chunk = client.recv(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    response = json.loads(b"".join(chunks).decode("utf-8"))
    if not isinstance(response, dict):
        raise RuntimeError("local proxy returned a non-object response")
    return response


def collect_operation(
    *,
    port: int,
    operation_id: str,
    strategy_codes: list[str],
    start_date: str,
    end_date: str,
    page_size: int,
    max_pages: int,
    raw_dir: Path,
) -> dict[str, Any]:
    page_summaries: list[dict[str, Any]] = []
    row_count = 0
    status = "complete"
    error: str | None = None
    for page_num in range(1, max_pages + 1):
        response = request_local_proxy(
            port,
            {
                "action": "probe_documented_history",
                "operationId": operation_id,
                "body": {
                    "strategyCodes": strategy_codes,
                    "startDate": start_date,
                    "endDate": end_date,
                    "pageNum": page_num,
                    "pageSize": page_size,
                },
            },
        )
        if response.get("state") != "ok":
            status = "blocked"
            error = str(response.get("message") or response.get("state") or "unknown proxy error")
            page_summaries.append({"page": page_num, "state": "error", "error": error})
            break
        payload = response.get("payload")
        write_json(raw_dir / operation_id / f"page_{page_num:04d}.json", payload)
        rows = payload.get("rows") if isinstance(payload, dict) and isinstance(payload.get("rows"), list) else []
        has_more = bool(payload.get("hasMoreRows")) if isinstance(payload, dict) else False
        row_count += len(rows)
        page_summaries.append(
            {
                "page": page_num,
                "state": "ok",
                "http_status": response.get("status"),
                "rows": len(rows),
                "has_more": has_more,
            }
        )
        if not has_more or not rows:
            break
    else:
        status = "truncated_at_max_pages"
    return {
        "operation_id": operation_id,
        "status": status,
        "row_count": row_count,
        "error": error,
        "pages": page_summaries,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe documented Qieman history endpoints for a small read-only strategy sample."
    )
    parser.add_argument("--proxy-port", type=int, default=43912)
    parser.add_argument("--strategy-codes", default=",".join(DEFAULT_STRATEGY_CODES))
    parser.add_argument("--start-date", default="1900-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--output-root", type=Path, default=PROBE_ROOT / "runs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    locks = active_locks()
    if locks:
        raise SystemExit("active production lock; Qieman history probe aborted: " + ", ".join(locks))
    strategy_codes = list(
        dict.fromkeys(code.strip() for code in args.strategy_codes.split(",") if code.strip())
    )
    if not strategy_codes:
        raise SystemExit("no strategy codes supplied")
    page_size = max(1, min(100, args.page_size))
    max_pages = max(1, min(1000, args.max_pages))
    started = now_local()
    run_id = started.strftime("%Y%m%dT%H%M%S%z") + "-stargate-history-sample"
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    describe = request_local_proxy(args.proxy_port, {"action": "describe"})
    write_json(
        run_dir / "proxy_capability.json",
        {
            "state": describe.get("state"),
            "operations": describe.get("operations"),
            "missingOperations": describe.get("missingOperations"),
            "documentedHistoryOperations": describe.get("documentedHistoryOperations"),
        },
    )
    results = [
        collect_operation(
            port=args.proxy_port,
            operation_id=operation_id,
            strategy_codes=strategy_codes,
            start_date=args.start_date,
            end_date=args.end_date,
            page_size=page_size,
            max_pages=max_pages,
            raw_dir=run_dir / "raw",
        )
        for operation_id in HISTORY_OPERATIONS
    ]
    summary = {
        "state": (
            "history_sample_collected"
            if all(result["status"] == "complete" for result in results)
            else "history_sample_blocked_or_partial"
        ),
        "run_id": run_id,
        "captured_at": started.isoformat(timespec="seconds"),
        "strategy_codes": strategy_codes,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "results": results,
        "quality_boundary": (
            "Only structured rows returned by official documented endpoints count as history; "
            "current snapshots, chart screenshots and inferred fund returns are excluded."
        ),
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
