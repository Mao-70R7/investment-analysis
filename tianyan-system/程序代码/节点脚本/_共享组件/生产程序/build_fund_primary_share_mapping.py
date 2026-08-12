from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
TABLE_NAME = "基金主份额映射"
INVALID_FULL_NAMES = {"", "-", "--", "---", "无", "未知", "N/A", "NONE"}


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def compact(value: Any) -> str:
    return re.sub(r"\s+", "", clean(value)).upper()


def share_class(name: str) -> str:
    text = clean(name).upper().replace("（", "(").replace("）", ")")
    if "A/B" in text or "A-B" in text:
        return "A/B"
    patterns = [
        r"([ABCDEFIHRY])([1-9]?)(?:类|份额)",
        r"([ABCDEFIHRY])([1-9]?)(?=人民币|美元|港元|现汇|现钞|\(|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return f"{match.group(1)}{match.group(2)}"
    return ""


def currency(name: str) -> str:
    text = clean(name)
    if "人民币" in text:
        return "人民币"
    if any(token in text for token in ("美元", "现汇", "现钞")):
        return "美元"
    if "港元" in text:
        return "港元"
    return "未标识"


def primary_score(row: dict[str, Any]) -> tuple[Any, ...]:
    cls = share_class(row["基金名称"])
    if cls in {"A", "A/B"}:
        class_score = (0, 0)
    elif cls.startswith("A") and cls[1:].isdigit():
        class_score = (0, int(cls[1:]))
    elif not cls:
        class_score = (1, 0)
    else:
        order = {"I": 2, "R": 3, "E": 4, "B": 5, "D": 6, "Y": 7, "C": 8, "F": 9, "H": 10}
        class_score = (order.get(cls[:1], 20), int(cls[1:]) if cls[1:].isdigit() else 0)
    currency_score = {"人民币": 0, "未标识": 1, "美元": 2, "港元": 3}.get(currency(row["基金名称"]), 9)
    backend = 1 if "后端" in row["基金名称"] else 0
    return (*class_score, currency_score, backend, row["基金代码"])


def family_id(company: str, full_name: str, code: str) -> str:
    key = f"{compact(company)}|{compact(full_name)}" if compact(full_name) not in INVALID_FULL_NAMES else f"CODE|{code}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def build_mapping(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT d."基金代码", d."标准基金名称" AS "基金名称",
                       COALESCE(NULLIF(TRIM(f."基金公司"), ''), NULLIF(TRIM(d."基金公司"), '')) AS "基金公司",
                       f."F10基金全称", f."F10成立日期"
                FROM "基金标准分类字典" d
                LEFT JOIN "基金F10基准" f ON f."基金代码" = d."基金代码"
                WHERE d."基金代码" IS NOT NULL AND TRIM(d."基金代码") <> ''
                ORDER BY d."基金代码"
                '''
            )
        ]
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            fid = family_id(clean(row.get("基金公司")), clean(row.get("F10基金全称")), clean(row.get("基金代码")))
            groups[fid].append(row)

        generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        output: list[tuple[Any, ...]] = []
        for fid, members in groups.items():
            ordered = sorted(members, key=primary_score)
            primary = ordered[0]
            merged_codes = [clean(item["基金代码"]) for item in ordered]
            valid_full_name = compact(primary.get("F10基金全称")) not in INVALID_FULL_NAMES
            source = "F10基金全称+基金公司" if valid_full_name else "无可靠基金全称，保留单份额"
            confidence = "高" if valid_full_name else "未合并"
            for item in members:
                code = clean(item["基金代码"])
                output.append(
                    (
                        code,
                        clean(item["基金名称"]),
                        fid,
                        clean(primary["基金代码"]),
                        clean(primary["基金名称"]),
                        clean(item.get("F10基金全称")),
                        clean(item.get("基金公司")),
                        share_class(clean(item["基金名称"])),
                        currency(clean(item["基金名称"])),
                        1 if code == clean(primary["基金代码"]) else 0,
                        json.dumps(merged_codes, ensure_ascii=False),
                        len(merged_codes),
                        source,
                        confidence,
                        "A/A1/A-B优先；同级人民币或无币种优先；后端靠后；不按数据完整度选主份额",
                        generated_at,
                    )
                )

        conn.execute(f'DROP TABLE IF EXISTS "{TABLE_NAME}"')
        conn.execute(
            f'''
            CREATE TABLE "{TABLE_NAME}" (
              "基金代码" TEXT PRIMARY KEY,
              "基金名称" TEXT,
              "基金家族ID" TEXT,
              "主基金代码" TEXT,
              "主基金名称" TEXT,
              "F10基金全称" TEXT,
              "基金公司" TEXT,
              "份额类别" TEXT,
              "计价币种" TEXT,
              "是否主份额" INTEGER,
              "合并份额代码JSON" TEXT,
              "合并份额数" INTEGER,
              "映射来源" TEXT,
              "映射置信度" TEXT,
              "主份额选择规则" TEXT,
              "生成时间" TEXT
            )
            '''
        )
        conn.executemany(f'INSERT INTO "{TABLE_NAME}" VALUES ({",".join("?" for _ in range(16))})', output)
        conn.execute(f'CREATE INDEX "idx_基金主份额映射_家族" ON "{TABLE_NAME}"("基金家族ID")')
        conn.execute(f'CREATE INDEX "idx_基金主份额映射_主基金" ON "{TABLE_NAME}"("主基金代码")')
        conn.commit()
        return {
            "share_rows": len(output),
            "family_count": len(groups),
            "primary_count": sum(1 for row in output if row[9] == 1),
            "merged_family_count": sum(1 for members in groups.values() if len(members) > 1),
            "generated_at": generated_at,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the all-market public fund primary-share mapping table.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(json.dumps(build_mapping(args.db), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

