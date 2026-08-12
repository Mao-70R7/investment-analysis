# -*- coding: utf-8 -*-
"""Build a mixed-performance workbook with a broad-equity bucket.

This script does not change the official benchmark-equity bucket.  It adds a
separate "broad equity" view where commodity and alternative benchmark weights
are counted together with equity for an additional classification field.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def zh(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import build_advisor_public_fund_mixed_performance_workbook as workbook_writer  # noqa: E402


REPORT_PACK_DIR = "advisor_public_fund_mixed_performance_20260630"
AS_OF = "20260630"

FIELD = zh(r"\u5b57\u6bb5")
NOTE = zh(r"\u8bf4\u660e")
ITEM = zh(r"\u9879\u76ee")
VALUE = zh(r"\u503c")

EQUITY_WEIGHT = zh(r"\u57fa\u51c6\u6743\u76ca\u6743\u91cd")
COMMODITY_WEIGHT = zh(r"\u57fa\u51c6\u5546\u54c1\u6743\u91cd")
ALTERNATIVE_WEIGHT = zh(r"\u57fa\u51c6\u53e6\u7c7b\u6743\u91cd")
UNKNOWN_WEIGHT = zh(r"\u57fa\u51c6\u672a\u77e5\u6743\u91cd")
EQUITY_BUCKET = zh(r"\u57fa\u51c6\u6743\u76ca\u5206\u6863")
EQUITY_BUCKET_SOURCE = zh(r"\u57fa\u51c6\u6743\u76ca\u5206\u6863\u6765\u6e90")

BROAD_WEIGHT = zh(r"\u5e7f\u4e49\u6743\u76ca\u6743\u91cd")
BROAD_BUCKET = zh(r"\u5e7f\u4e49\u6743\u76ca\u5206\u6863")
BROAD_BUCKET_DESC = zh(r"\u5e7f\u4e49\u6743\u76ca\u5206\u6863\u8bf4\u660e")
BROAD_METHOD = zh(r"\u5e7f\u4e49\u6743\u76ca\u53e3\u5f84\u8bf4\u660e")

UNKNOWN_TOLERANCE_DECIMAL = 0.0001
WEIGHT_TOLERANCE_DECIMAL = 0.000001


def find_default_source() -> Path:
    root = PROJECT_ROOT / "site"
    candidates = [root / "reports" / REPORT_PACK_DIR / "workbook_source.json"]
    candidates = [item for item in candidates if item.is_file()]
    if not candidates:
        raise FileNotFoundError(f"Cannot find reports/{REPORT_PACK_DIR}/workbook_source.json under {root}")
    return candidates[0]


def default_output_path(source_path: Path) -> Path:
    reports_dir = source_path.parents[1]
    name = zh(
        r"\u6295\u987e\u7b56\u7565_\u5168\u91cf\u57fa\u91d1\u4ea7\u54c1\u4e1a\u7ee9"
        rf"\u6df7\u6392\u699c_\u622a\u81f3{AS_OF}_\u5e7f\u4e49\u6743\u76ca\u5206\u6863\u7248.xlsx"
    )
    return reports_dir / name


def as_number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def bucket_for_decimal(weight: float | None) -> str:
    if weight is None:
        return ""
    percent = weight * 100.0
    if percent <= 0:
        return "L0"
    return f"L{min(10, max(1, math.ceil(percent / 10.0)))}"


def bucket_desc(bucket: str, weight: float | None) -> str:
    if not bucket or weight is None:
        return zh(r"\u672a\u8f93\u51fa\u786e\u5b9a\u5e7f\u4e49\u5206\u6863")
    pct = weight * 100.0
    return zh(r"\u5e7f\u4e49\u6743\u76ca=") + f"{pct:.2f}%" + zh(
        r"\uff0c\u6309\u6743\u76ca+\u5546\u54c1+\u53e6\u7c7b\u8ba1\u7b97"
    )


def compute_broad_fields(row: dict[str, Any]) -> None:
    equity = as_number(row.get(EQUITY_WEIGHT)) or 0.0
    commodity = as_number(row.get(COMMODITY_WEIGHT)) or 0.0
    alternative = as_number(row.get(ALTERNATIVE_WEIGHT)) or 0.0
    unknown = as_number(row.get(UNKNOWN_WEIGHT)) or 0.0

    if unknown > UNKNOWN_TOLERANCE_DECIMAL:
        row[BROAD_WEIGHT] = None
        row[BROAD_BUCKET] = ""
        row[BROAD_BUCKET_DESC] = zh(r"\u57fa\u51c6\u672a\u77e5\u6743\u91cd\u8d85\u8fc70.01%\uff0c\u4e0d\u8f93\u51fa\u786e\u5b9a\u5e7f\u4e49\u5206\u6863")
    else:
        broad = equity + commodity + alternative
        if broad > 1.0 and broad <= 1.0 + WEIGHT_TOLERANCE_DECIMAL:
            broad = 1.0
        row[BROAD_WEIGHT] = broad
        row[BROAD_BUCKET] = bucket_for_decimal(broad)
        row[BROAD_BUCKET_DESC] = bucket_desc(row[BROAD_BUCKET], broad)

    row[BROAD_METHOD] = zh(
        r"\u5e7f\u4e49\u6743\u76ca\u6743\u91cd=\u57fa\u51c6\u6743\u76ca\u6743\u91cd+"
        r"\u57fa\u51c6\u5546\u54c1\u6743\u91cd+\u57fa\u51c6\u53e6\u7c7b\u6743\u91cd\uff1b"
        r"\u6e2f\u80a1/\u6d77\u5916\u6743\u76ca\u4e3a\u6743\u76ca\u5b50\u9879\uff0c\u4e0d\u91cd\u590d\u52a0\u603b"
    )


def insert_headers() -> None:
    headers = workbook_writer.MIXED_HEADERS
    new_headers = [BROAD_WEIGHT, BROAD_BUCKET, BROAD_BUCKET_DESC, BROAD_METHOD]
    for header in new_headers:
        if header in headers:
            headers.remove(header)
    anchor = EQUITY_BUCKET_SOURCE if EQUITY_BUCKET_SOURCE in headers else EQUITY_BUCKET
    insert_at = headers.index(anchor) + 1 if anchor in headers else len(headers)
    headers[insert_at:insert_at] = new_headers


def append_notes(data: dict[str, Any]) -> None:
    notes = data.setdefault("fieldNotes", [])
    existing = {row.get(FIELD) for row in notes if isinstance(row, dict)}
    new_notes = [
        {
            FIELD: BROAD_WEIGHT,
            NOTE: zh(r"\u57fa\u51c6\u6743\u76ca\u6743\u91cd+\u57fa\u51c6\u5546\u54c1\u6743\u91cd+\u57fa\u51c6\u53e6\u7c7b\u6743\u91cd\uff0c\u4ec5\u7528\u4e8e\u8fd9\u4e00\u7248\u62a5\u8868\u7684\u6269\u5c55\u5206\u6790\u53e3\u5f84\u3002"),
        },
        {
            FIELD: BROAD_BUCKET,
            NOTE: zh(r"\u6309\u5e7f\u4e49\u6743\u76ca\u6743\u91cd\u5212\u5206L0-L10\uff1b\u57fa\u51c6\u672a\u77e5\u6743\u91cd\u8d85\u8fc70.01%\u65f6\u4e0d\u786c\u8f93\u51fa\u5206\u6863\u3002"),
        },
    ]
    notes.extend(row for row in new_notes if row[FIELD] not in existing)


def append_coverage_rows(data: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    coverage_rows = data.setdefault("coverageRows", [])
    counter = Counter(row.get(BROAD_BUCKET) or zh(r"\u672a\u8f93\u51fa") for row in rows)
    coverage_rows.extend(
        [
            {
                ITEM: zh(r"\u5e7f\u4e49\u6743\u76ca\u5206\u6863\u6709\u6548\u4ea7\u54c1\u6570"),
                VALUE: sum(1 for row in rows if row.get(BROAD_BUCKET)),
                NOTE: zh(r"\u672a\u77e5\u6743\u91cd\u8d85\u9608\u503c\u7684\u4ea7\u54c1\u4e0d\u8f93\u51fa\u5e7f\u4e49\u5206\u6863"),
            },
            {
                ITEM: zh(r"\u5e7f\u4e49\u6743\u76ca\u5206\u6863\u5206\u5e03"),
                VALUE: json.dumps(dict(counter), ensure_ascii=False),
                NOTE: zh(r"\u8be5\u5206\u5e03\u4ec5\u7528\u4e8e\u6743\u76ca+\u5546\u54c1+\u53e6\u7c7b\u53e3\u5f84\u89c2\u5bdf\uff0c\u4e0d\u66ff\u4ee3\u539f\u57fa\u51c6\u6743\u76ca\u5206\u6863"),
            },
        ]
    )


def build(source_path: Path, source_out: Path, output_xlsx: Path) -> dict[str, Any]:
    data = json.loads(source_path.read_text(encoding="utf-8-sig"))
    rows = data.get("rows") or []
    for row in rows:
        compute_broad_fields(row)

    meta = data.setdefault("meta", {})
    meta["broadEquityMethod"] = zh(r"\u5e7f\u4e49\u6743\u76ca=\u6743\u76ca+\u5546\u54c1+\u53e6\u7c7b")
    meta["broadEquityBucketCount"] = sum(1 for row in rows if row.get(BROAD_BUCKET))

    append_notes(data)
    append_coverage_rows(data, rows)
    source_out.parent.mkdir(parents=True, exist_ok=True)
    source_out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    insert_headers()
    result = workbook_writer.build_workbook(source_out, output_xlsx)
    result["sourceOut"] = str(source_out)
    result["broadEquityBucketCount"] = meta["broadEquityBucketCount"]
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    default_source = find_default_source()
    parser.add_argument("--source-json", type=Path, default=default_source)
    parser.add_argument("--source-out", type=Path, default=default_source.with_name("workbook_source_broad_equity.json"))
    parser.add_argument("--output-xlsx", type=Path, default=default_output_path(default_source))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build(args.source_json, args.source_out, args.output_xlsx)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
