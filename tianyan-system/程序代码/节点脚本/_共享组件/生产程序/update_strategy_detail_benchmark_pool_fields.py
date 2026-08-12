# -*- coding: utf-8 -*-
"""Refresh benchmark peer-pool fields in generated strategy detail shards."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_DETAIL_DIR = ROOT / "site" / "basic_data" / "data" / "details"
FIELDS = [
    "基准风险资产权重",
    "基准结构类型",
    "非权益比较轨道",
    "正式可比池",
    "可比池样本资格",
    "可比池说明",
    "基准互斥权重合计_百分比",
    "基准港股权益权重",
    "基准海外权益权重",
    "是否多元策略",
    "多元策略标签",
    "基准映射置信度",
    "基准资产已映射权重",
    "基准资产未映射权重",
    "基准资产大类-权益",
    "基准资产大类-债券",
    "基准资产大类-现金",
    "基准资产大类-商品",
    "基准资产大类-另类",
    "基准资产大类-其他",
    "基准资产类别-A股",
    "基准资产类别-港股",
    "基准资产类别-海外权益",
    "基准资产类别-债券",
    "基准资产类别-商品",
    "基准资产类别-现金",
    "基准资产类别-其他",
]


def load_assets(db_path: Path) -> dict[str, dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return {str(row["统一策略ID"]): dict(row) for row in conn.execute('SELECT * FROM "策略基准资产配置"')}
    finally:
        conn.close()


def update_detail(path: Path, assets: dict[str, dict[str, Any]]) -> tuple[bool, bool]:
    text = path.read_text(encoding="utf-8-sig")
    marker = " = "
    if marker not in text:
        return False, False
    prefix, payload_text = text.rsplit(marker, 1)
    payload = json.loads(payload_text.strip().rstrip(";"))
    strategy_id = str(payload.get("id") or "")
    asset = assets.get(strategy_id)
    if not asset:
        return False, False
    rows = payload.get("classificationFields") or []
    by_name = {str(row.get("字段")): row for row in rows if isinstance(row, dict)}
    for field in FIELDS:
        if field in by_name:
            by_name[field]["值"] = asset.get(field)
        else:
            rows.append({"字段": field, "值": asset.get(field)})
    bucket = asset.get("基准风险资产权重")
    if "基准风险资产权重" in by_name:
        by_name["基准风险资产权重"]["值"] = bucket
    else:
        rows.append({"字段": "基准风险资产权重", "值": bucket})
    payload["classificationFields"] = rows
    path.write_text(f"{prefix}{marker}{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n", encoding="utf-8")
    return True, bool(asset.get("正式可比池"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--detail-dir", type=Path, default=DEFAULT_DETAIL_DIR)
    args = parser.parse_args()
    assets = load_assets(args.db)
    updated = pooled = skipped = 0
    for path in sorted(args.detail_dir.glob("*.js")):
        changed, has_pool = update_detail(path, assets)
        if changed:
            updated += 1
            pooled += int(has_pool)
        else:
            skipped += 1
    print(json.dumps({"updated": updated, "pooled": pooled, "skipped": skipped, "detail_dir": str(args.detail_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
