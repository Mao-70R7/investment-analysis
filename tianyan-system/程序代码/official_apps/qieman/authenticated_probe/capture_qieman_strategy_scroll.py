from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from probe_qieman_device import (
    PROBE_ROOT,
    acquire_device_lock,
    active_locks,
    now_local,
    redact_sensitive_text,
    release_device_lock,
    run_adb,
    write_json,
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_ocr_result(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return rows
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        box, text, score = item[0], item[1], item[2]
        clean = redact_sensitive_text(str(text).strip())
        if not clean:
            continue
        rows.append({"box": box, "text": clean, "score": float(score)})
    return rows


def assess_visible_text(texts: list[str]) -> dict[str, Any]:
    joined = "\n".join(texts)
    fund_codes = sorted(set(re.findall(r"(?<!\d)\d{6}(?!\d)", joined)))
    percentage_texts = sorted(set(re.findall(r"[-+]?\d+(?:\.\d+)?%", joined)))
    return {
        "benchmark_keyword_seen": any(term in joined for term in ("业绩基准", "比较基准", "基准")),
        "launch_date_keyword_seen": any(term in joined for term in ("成立日期", "成立日", "上线日期")),
        "performance_keyword_seen": any(term in joined for term in ("累计收益", "年化收益", "近一年", "最大回撤", "净值")),
        "holding_keyword_seen": any(term in joined for term in ("持仓", "成分基金", "当前配置", "组合配置")),
        "rebalance_keyword_seen": any(term in joined for term in ("调仓", "调整记录", "动态调整")),
        "fee_keyword_seen": any(term in joined for term in ("投顾服务费", "投顾费", "服务费")),
        "minimum_amount_keyword_seen": any(term in joined for term in ("起购", "起投")),
        "fund_codes": fund_codes,
        "percentage_texts": percentage_texts,
        "quality_note": "OCR 只证明当前可见页面文字，不能单独形成正式日度曲线、精确持仓或调仓明细。",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture and OCR a Qieman strategy detail page by safe upward swipes.")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--adb-path", default=str(PROBE_ROOT.parents[2] / "tools" / "platform-tools" / "adb.exe"))
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--strategy-name", required=True)
    parser.add_argument("--screen-count", type=int, default=7)
    parser.add_argument("--wait-sec", type=float, default=2.0)
    parser.add_argument("--tap-x", type=int)
    parser.add_argument("--tap-y", type=int)
    parser.add_argument("--scroll-direction", choices=("page_down", "page_up"), default="page_down")
    parser.add_argument("--output-root", type=Path, default=PROBE_ROOT / "runs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    locks = active_locks()
    if locks:
        raise SystemExit("active production lock; capture aborted: " + ", ".join(locks))
    started = now_local()
    run_id = started.strftime("%Y%m%dT%H%M%S%z") + "-strategy-scroll"
    run_dir = args.output_root.resolve() / run_id / args.strategy_id
    screen_dir = run_dir / "screens"
    screen_dir.mkdir(parents=True, exist_ok=True)

    lock_path, lock_token = acquire_device_lock(run_id)
    screenshots: list[dict[str, Any]] = []
    try:
        if args.tap_x is not None or args.tap_y is not None:
            if args.tap_x is None or args.tap_y is None:
                raise ValueError("--tap-x and --tap-y must be provided together")
            tapped = run_adb(
                args.adb_path,
                args.device_id,
                "shell",
                "input",
                "tap",
                str(args.tap_x),
                str(args.tap_y),
                timeout=20,
            )
            if tapped.returncode != 0:
                raise RuntimeError(tapped.stderr or tapped.stdout or "ADB tap failed")
            time.sleep(max(0.5, args.wait_sec))
        for index in range(max(1, args.screen_count)):
            if index:
                start_y, end_y = ("1780", "620") if args.scroll_direction == "page_down" else ("620", "1780")
                swipe = run_adb(
                    args.adb_path,
                    args.device_id,
                    "shell",
                    "input",
                    "swipe",
                    "540",
                    start_y,
                    "540",
                    end_y,
                    "650",
                    timeout=20,
                )
                if swipe.returncode != 0:
                    raise RuntimeError(swipe.stderr or swipe.stdout or "ADB swipe failed")
                time.sleep(max(0.2, args.wait_sec))
            screen = run_adb(
                args.adb_path,
                args.device_id,
                "exec-out",
                "screencap",
                "-p",
                timeout=30,
                binary=True,
            )
            if screen.returncode != 0 or not screen.stdout:
                raise RuntimeError("ADB screencap failed")
            path = screen_dir / f"{index:02d}.png"
            path.write_bytes(screen.stdout)
            screenshots.append(
                {
                    "index": index,
                    "path": str(path),
                    "sha256": sha256_bytes(screen.stdout),
                    "size": len(screen.stdout),
                }
            )
    finally:
        release_device_lock(lock_path, lock_token)

    ocr_error = None
    ocr_pages: list[dict[str, Any]] = []
    all_texts: list[str] = []
    try:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        for screenshot in screenshots:
            result, elapsed = engine(screenshot["path"])
            rows = normalize_ocr_result(result)
            texts = [row["text"] for row in rows]
            all_texts.extend(texts)
            ocr_pages.append(
                {
                    "index": screenshot["index"],
                    "screenshot_sha256": screenshot["sha256"],
                    "elapsed": elapsed,
                    "rows": rows,
                }
            )
    except Exception as exc:  # pragma: no cover - optional OCR runtime path
        ocr_error = f"{type(exc).__name__}: {exc}"

    deduplicated_texts = list(dict.fromkeys(all_texts))
    assessment = assess_visible_text(deduplicated_texts)
    write_json(run_dir / "ocr.json", {"pages": ocr_pages, "error": ocr_error})
    write_json(run_dir / "visible_text_assessment.json", assessment)
    summary = {
        "state": "strategy_scroll_capture_complete",
        "captured_at": started.isoformat(timespec="seconds"),
        "run_id": run_id,
        "device_id": args.device_id,
        "strategy_id": args.strategy_id,
        "strategy_name": args.strategy_name,
        "pre_capture_tap": [args.tap_x, args.tap_y] if args.tap_x is not None else None,
        "scroll_direction": args.scroll_direction,
        "screenshot_count": len(screenshots),
        "unique_ocr_text_count": len(deduplicated_texts),
        "ocr_error": ocr_error,
        "screenshots": screenshots,
        "visible_text_assessment": assessment,
        "evidence_level": "authenticated_ui_ocr_partial",
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
