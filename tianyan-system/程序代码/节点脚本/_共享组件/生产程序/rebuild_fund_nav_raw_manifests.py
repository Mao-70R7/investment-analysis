from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "ttfund_fund_nav" / "eastmoney"
RAW_CHANNEL_ID = "ttfund_fund_nav"
RAW_CHANNEL_NAME = "天天基金/基金历史净值"
PINGZHONGDATA_URL = "https://fund.eastmoney.com/pingzhongdata/{fund_code}.js"
DIVIDEND_URL = "https://fundf10.eastmoney.com/fhsp_{fund_code}.html"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild missing ttfund_fund_nav raw manifests from local raw files.")
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT, help="ttfund_fund_nav/eastmoney raw root.")
    parser.add_argument(
        "--run-id",
        action="append",
        default=[],
        help="Only rebuild selected run_id. Can be passed multiple times.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Rewrite existing _manifest.json files. Default only rebuilds missing manifests.",
    )
    return parser.parse_args()


def legacy_snapshot_id(channel_id: str, collector_name: str, raw_bytes: bytes, raw_path: Path) -> str:
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    hint_hash = hashlib.sha1(f"{raw_path.name}-1".encode("utf-8")).hexdigest()
    return f"{channel_id}-{collector_name}-{content_hash[:12]}-{hint_hash[:6]}"


def run_id_to_captured_at(run_id: str) -> str:
    return datetime.strptime(run_id, "%Y%m%dT%H%M%S%z").isoformat(timespec="seconds")


def snapshot_from_file(
    *,
    fund_code: str,
    raw_path: Path,
    collector_name: str,
    source_url: str,
    captured_at: str,
    content_type: str,
) -> dict[str, Any]:
    raw_bytes = raw_path.read_bytes()
    return {
        "snapshot_id": legacy_snapshot_id(RAW_CHANNEL_ID, collector_name, raw_bytes, raw_path),
        "channel_id": RAW_CHANNEL_ID,
        "collector_name": collector_name,
        "access_level": "public",
        "captured_at": captured_at,
        "source_url": source_url.format(fund_code=fund_code),
        "http_status": 200,
        "raw_path": str(raw_path.resolve()),
        "content_type": content_type,
        "content_hash": hashlib.sha256(raw_bytes).hexdigest(),
        "parse_status": "parsed",
    }


def rebuild_manifest(run_dir: Path, overwrite: bool) -> dict[str, Any] | None:
    manifest_path = run_dir / "_manifest.json"
    if manifest_path.exists() and not overwrite:
        return None
    funds_root = run_dir / "funds"
    if not funds_root.exists():
        return None

    run_id = run_dir.name
    captured_at = run_id_to_captured_at(run_id)
    raw_snapshots: list[dict[str, Any]] = []
    for fund_dir in sorted(path for path in funds_root.iterdir() if path.is_dir()):
        fund_code = fund_dir.name
        history_path = fund_dir / "pingzhongdata.js"
        dividend_path = fund_dir / "fhsp.html"
        if history_path.exists():
            raw_snapshots.append(
                snapshot_from_file(
                    fund_code=fund_code,
                    raw_path=history_path,
                    collector_name="pingzhongdata_history",
                    source_url=PINGZHONGDATA_URL,
                    captured_at=captured_at,
                    content_type="application/javascript",
                )
            )
        if dividend_path.exists():
            raw_snapshots.append(
                snapshot_from_file(
                    fund_code=fund_code,
                    raw_path=dividend_path,
                    collector_name="fhsp_dividend",
                    source_url=DIVIDEND_URL,
                    captured_at=captured_at,
                    content_type="text/html",
                )
            )

    if not raw_snapshots:
        return None
    payload = {
        "channel_id": RAW_CHANNEL_ID,
        "channel_name": RAW_CHANNEL_NAME,
        "run_id": run_id,
        "captured_at": captured_at,
        "raw_snapshots": raw_snapshots,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"manifest_path": str(manifest_path), "raw_snapshot_count": len(raw_snapshots)}


def main() -> None:
    args = parse_args()
    selected_runs = set(args.run_id)
    rebuilt: list[dict[str, Any]] = []
    for run_dir in sorted(path for path in args.raw_root.glob("*/*") if path.is_dir()):
        if selected_runs and run_dir.name not in selected_runs:
            continue
        result = rebuild_manifest(run_dir, args.overwrite)
        if result:
            rebuilt.append(result)
    print(json.dumps({"rebuilt_manifest_count": len(rebuilt), "rebuilt": rebuilt}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
