"""Discover TTFund strategy IDs from the logged-in App cache.

The TTFund App has no stable anonymous catalog endpoint in this project.  A
full update therefore performs a best-effort, read-only UI warm-up, pulls the
App's existing cache, and emits a manifest consumed by the incremental plan.
Unknown IDs are still collected in the same run; completeness is explicitly
marked unverified because the App cache does not expose a server total.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
PACKAGE = "com.eastmoney.android.fund"
REMOTE_CACHE = f"/sdcard/Android/data/{PACKAGE}/files/.ttjj_cache"
HOME_PREFIXES = (
    "layout_tougu-scroll-view",
    "saveAllAdvisersInfokey",
    "home-vuex_",
    "EFAppHomeConfigData",
)
STRATEGY_URL_PATTERN = re.compile(
    r"(?:[?&](?:id|strategyId|tgCode)=)(?P<sid>[A-Za-z0-9]+)",
    re.IGNORECASE,
)
ADVISOR_CACHE_PREFIX = "saveAllAdvisersInfokey"
ADVISOR_PAGE_APP_ID = "fund034076731f1e4b"
ADVISOR_STRATEGY_GROUP_PATTERN = re.compile(
    r"strategyId\s*:\s*['\"](?P<ids>[A-Za-z0-9&]+)['\"]",
    re.IGNORECASE,
)
VALID_STRATEGY_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{7}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover TTFund App strategy catalog IDs from cache/UI.")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--adb-path", default="adb")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--warmup-sec", type=int, default=6)
    parser.add_argument("--max-ui-swipes", type=int, default=10)
    parser.add_argument("--swipe-wait-ms", type=int, default=900)
    parser.add_argument("--skip-ui-navigation", action="store_true")
    parser.add_argument(
        "--skip-advisor-catalog",
        action="store_true",
        help="Skip the signed-in advisor institution pages that expose a broader strategy catalog.",
    )
    parser.add_argument(
        "--advisor-wait-sec",
        type=float,
        default=1.8,
        help="Wait after opening each advisor institution page before reading filtered React Native logs.",
    )
    parser.add_argument(
        "--advisor-limit",
        type=int,
        default=0,
        help="Optional advisor page limit for controlled diagnostics; 0 scans every cached advisor.",
    )
    parser.add_argument("--allow-missing-device", action="store_true")
    return parser.parse_args()


def default_cache_dir() -> Path:
    configured = str(os.environ.get("ADVISOR_RAW_ROOT") or "").strip()
    if configured:
        return Path(configured) / "device_cache"
    return PROJECT_ROOT / "data" / "raw" / "device_cache"


def run_adb(adb_path: str, device_id: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [adb_path, "-s", device_id, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def recursive_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from recursive_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_nodes(child)


def strategy_ids_from_node(node: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ("strategyId", "strategyID", "tgCode", "TGCODE"):
        strategy_id = str(node.get(key) or "").strip()
        if strategy_id and strategy_id not in result:
            result.append(strategy_id)
    for value in node.values():
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        if "strategydetail" not in lowered and "tgcode=" not in lowered:
            continue
        for match in STRATEGY_URL_PATTERN.finditer(value):
            strategy_id = match.group("sid").strip()
            if strategy_id and strategy_id not in result:
                result.append(strategy_id)
    return result


def load_json(path: Path) -> Any | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def extract_home_rows(cache_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(cache_dir.glob("*")):
        if not path.is_file() or not path.name.startswith(HOME_PREFIXES):
            continue
        payload = load_json(path)
        if not isinstance(payload, dict):
            continue
        for node in recursive_nodes(payload):
            for strategy_id in strategy_ids_from_node(node):
                row = rows.setdefault(
                    strategy_id,
                    {
                        "source_strategy_id": strategy_id,
                        "strategy_name": None,
                        "partner_id": None,
                        "strategy_type": None,
                        "launch_date": None,
                        "skip_url": None,
                        "source_files": [],
                    },
                )
                for output_key, source_key in {
                    "strategy_name": "strategyName",
                    "partner_id": "partnerId",
                    "strategy_type": "styleName",
                    "launch_date": "establishDate",
                    "skip_url": "skipUrl",
                }.items():
                    if row.get(output_key) in (None, "") and node.get(source_key) not in (None, ""):
                        row[output_key] = node.get(source_key)
                if str(path) not in row["source_files"]:
                    row["source_files"].append(str(path))
    ordered = [rows[key] for key in sorted(rows)]
    return ordered, sorted(rows)


def extract_advisor_rows(cache_dir: Path) -> list[dict[str, Any]]:
    """Return the signed-in institution directory stored by the TTFund App.

    The cache uses ``code`` as the route's ``partnerId``.  ``id`` is only the
    display order and must not be used to open the institution page.
    """

    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(cache_dir.glob(f"{ADVISOR_CACHE_PREFIX}*")):
        if not path.is_file():
            continue
        payload = load_json(path)
        if payload is None:
            continue
        for node in recursive_nodes(payload):
            partner_id = str(node.get("code") or "").strip()
            advisor_name = str(node.get("name") or "").strip()
            if not partner_id or not partner_id.isdigit() or not advisor_name:
                continue
            row = rows.setdefault(
                partner_id,
                {
                    "partner_id": partner_id,
                    "advisor_name": advisor_name,
                    "source_files": [],
                },
            )
            if str(path) not in row["source_files"]:
                row["source_files"].append(str(path))
    return sorted(rows.values(), key=lambda row: (str(row["partner_id"]), str(row["advisor_name"])))


def advisor_page_deep_link(partner_id: str) -> str:
    inner = (
        "fund://mp.1234567.com.cn/weex/"
        f"{ADVISOR_PAGE_APP_ID}/pages/question/index?partnerId={quote(partner_id, safe='')}"
    )
    wrapper = {
        "LinkTo": inner,
        "LinkType": 2,
        "AdId": "0",
        "IsVerifyLogin": False,
        "CloseWeex": False,
    }
    encoded = quote(json.dumps(wrapper, ensure_ascii=False, separators=(",", ":")), safe="")
    return f"eastmoneyjijin://startapp/toPage?type=8&linkto={encoded}"


def strategy_ids_from_advisor_log(log_text: str) -> list[str]:
    strategy_ids: set[str] = set()
    for match in ADVISOR_STRATEGY_GROUP_PATTERN.finditer(log_text):
        for value in match.group("ids").split("&"):
            strategy_id = value.strip()
            if strategy_id != "0" and VALID_STRATEGY_ID_PATTERN.fullmatch(strategy_id):
                strategy_ids.add(strategy_id)
    return sorted(strategy_ids)


def probe_advisor_catalog(
    args: argparse.Namespace,
    advisors: list[dict[str, Any]],
    run_dir: Path,
) -> dict[str, Any]:
    """Open each institution page and retain only strategy-ID evidence lines.

    Full logcat output may contain login context, so it is never written to
    disk or returned in the manifest.
    """

    output_dir = run_dir / "advisor_catalog"
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = advisors[: max(args.advisor_limit, 0)] if args.advisor_limit > 0 else advisors
    results: list[dict[str, Any]] = []
    catalog_ids: set[str] = set()
    for advisor in selected:
        partner_id = str(advisor["partner_id"])
        evidence_path = output_dir / f"partner_{partner_id}_evidence.log"
        cleared = run_adb(args.adb_path, args.device_id, "logcat", "-c", timeout=30)
        if cleared.returncode != 0:
            results.append(
                {
                    **advisor,
                    "state": "log_clear_failed",
                    "strategy_id_total": 0,
                    "strategy_ids": [],
                    "reason": cleared.stderr.strip() or cleared.stdout.strip(),
                }
            )
            continue
        try:
            launch_command = (
                "am start -a android.intent.action.VIEW "
                f"-d '{advisor_page_deep_link(partner_id)}'"
            )
            launched = run_adb(
                args.adb_path,
                args.device_id,
                "shell",
                launch_command,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            results.append(
                {
                    **advisor,
                    "state": "launch_timeout",
                    "strategy_id_total": 0,
                    "strategy_ids": [],
                    "reason": "adb am start timed out",
                }
            )
            continue
        if launched.returncode != 0:
            results.append(
                {
                    **advisor,
                    "state": "launch_failed",
                    "strategy_id_total": 0,
                    "strategy_ids": [],
                    "reason": launched.stderr.strip() or launched.stdout.strip(),
                }
            )
            continue
        if args.advisor_wait_sec > 0:
            time.sleep(args.advisor_wait_sec)
        try:
            captured = run_adb(args.adb_path, args.device_id, "logcat", "-d", "-v", "threadtime", timeout=45)
        except subprocess.TimeoutExpired:
            results.append(
                {
                    **advisor,
                    "state": "log_capture_timeout",
                    "strategy_id_total": 0,
                    "strategy_ids": [],
                    "reason": "adb logcat capture timed out",
                }
            )
            continue
        log_text = captured.stdout if captured.returncode == 0 else ""
        strategy_ids = strategy_ids_from_advisor_log(log_text)
        evidence_lines = [
            line
            for line in log_text.splitlines()
            if "strategyId:" in line
            or ("/pages/question/index" in line and "Running" in line)
        ]
        evidence_path.write_text("\n".join(evidence_lines) + ("\n" if evidence_lines else ""), encoding="utf-8")
        catalog_ids.update(strategy_ids)
        results.append(
            {
                **advisor,
                "state": "ready" if strategy_ids else "empty",
                "strategy_id_total": len(strategy_ids),
                "strategy_ids": strategy_ids,
                "evidence_path": str(evidence_path),
                "filtered_evidence_line_total": len(evidence_lines),
            }
        )
    with_ids = sum(1 for row in results if row.get("strategy_ids"))
    failed = sum(1 for row in results if str(row.get("state")) not in {"ready", "empty"})
    return {
        "state": "ready" if catalog_ids and failed == 0 else ("partial" if catalog_ids else "empty"),
        "mode": "signed_in_advisor_institution_pages",
        "partner_total": len(selected),
        "partner_with_ids_total": with_ids,
        "partner_zero_total": sum(1 for row in results if row.get("state") == "empty"),
        "partner_failed_total": failed,
        "catalog_strategy_total": len(catalog_ids),
        "catalog_strategy_ids": sorted(catalog_ids),
        "results": results,
        "privacy_note": "仅保存机构路由和 strategyId 行；未保存完整 logcat，避免登录上下文泄漏。",
    }


def merge_catalog_rows(
    home_rows: list[dict[str, Any]],
    advisor_result: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = {str(row["source_strategy_id"]): dict(row) for row in home_rows}
    for advisor in advisor_result.get("results") or []:
        for strategy_id in advisor.get("strategy_ids") or []:
            row = rows.setdefault(
                str(strategy_id),
                {
                    "source_strategy_id": str(strategy_id),
                    "strategy_name": None,
                    "partner_id": str(advisor.get("partner_id") or "") or None,
                    "strategy_type": None,
                    "launch_date": None,
                    "skip_url": None,
                    "source_files": [],
                },
            )
            if not row.get("partner_id"):
                row["partner_id"] = str(advisor.get("partner_id") or "") or None
            evidence_path = str(advisor.get("evidence_path") or "")
            if evidence_path and evidence_path not in row["source_files"]:
                row["source_files"].append(evidence_path)
            row["advisor_name"] = row.get("advisor_name") or advisor.get("advisor_name")
    ids = sorted(rows)
    return [rows[strategy_id] for strategy_id in ids], ids


def ui_texts(adb_path: str, device_id: str, run_dir: Path) -> list[dict[str, Any]]:
    remote = "/sdcard/ttfund_catalog_uidump.xml"
    local = run_dir / "last_uidump.xml"
    dump = run_adb(adb_path, device_id, "shell", "uiautomator", "dump", remote, timeout=30)
    if dump.returncode != 0 and "dumped to:" not in f"{dump.stdout}\n{dump.stderr}".lower():
        return []
    pull = run_adb(adb_path, device_id, "pull", remote, str(local), timeout=30)
    if pull.returncode != 0 or not local.is_file():
        return []
    try:
        root = ET.fromstring(local.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ET.ParseError):
        return []
    result: list[dict[str, Any]] = []
    for element in root.iter():
        text = (element.attrib.get("content-desc") or element.attrib.get("text") or "").strip()
        bounds = element.attrib.get("bounds") or ""
        if not text or not bounds:
            continue
        try:
            left, top, right, bottom = [int(item) for item in bounds.replace("][", ",").replace("[", "").replace("]", "").split(",")]
        except ValueError:
            continue
        result.append({
            "text": text,
            "clickable": element.attrib.get("clickable") == "true",
            "center": ((left + right) // 2, (top + bottom) // 2),
        })
    return result


def warm_up_catalog_ui(args: argparse.Namespace, run_dir: Path) -> dict[str, Any]:
    state = run_adb(args.adb_path, args.device_id, "get-state", timeout=15)
    if state.returncode != 0 or state.stdout.strip() != "device":
        return {"state": "device_unavailable", "reason": state.stderr.strip() or state.stdout.strip()}
    run_adb(args.adb_path, args.device_id, "shell", "monkey", "-p", PACKAGE, "1", timeout=30)
    if args.warmup_sec > 0:
        time.sleep(args.warmup_sec)
    if args.skip_ui_navigation:
        return {"state": "app_launched", "ui_navigation": "skipped", "swipe_total": 0}

    keywords = ("全部策略", "投顾组合", "更多", "全部", "投顾", "策略")
    clicked = False
    swipe_total = 0
    visible_samples: list[str] = []
    for _ in range(max(args.max_ui_swipes, 0) + 1):
        nodes = ui_texts(args.adb_path, args.device_id, run_dir)
        visible_samples.extend(str(node["text"]) for node in nodes if node.get("text"))
        if not clicked:
            candidates = [
                node for node in nodes
                if node.get("clickable") and any(keyword in str(node.get("text")) for keyword in keywords)
            ]
            if candidates:
                preferred = sorted(
                    candidates,
                    key=lambda node: next((index for index, keyword in enumerate(keywords) if keyword in str(node.get("text"))), len(keywords)),
                )[0]
                x, y = preferred["center"]
                run_adb(args.adb_path, args.device_id, "shell", "input", "tap", str(x), str(y), timeout=15)
                clicked = True
                time.sleep(max(args.swipe_wait_ms, 300) / 1000)
                continue
        if swipe_total >= max(args.max_ui_swipes, 0):
            break
        run_adb(args.adb_path, args.device_id, "shell", "input", "swipe", "540", "1850", "540", "700", "280", timeout=15)
        swipe_total += 1
        time.sleep(max(args.swipe_wait_ms, 300) / 1000)
    return {
        "state": "ready",
        "ui_navigation": "attempted",
        "clicked_catalog_entry": clicked,
        "swipe_total": swipe_total,
        "visible_text_sample": sorted(dict.fromkeys(visible_samples))[:80],
    }


def pull_home_cache(args: argparse.Namespace, cache_dir: Path, run_dir: Path) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    remote_paths: set[str] = set()
    list_errors: list[str] = []
    for prefix in HOME_PREFIXES:
        listed = run_adb(
            args.adb_path,
            args.device_id,
            "shell",
            "ls",
            "-1",
            f"{REMOTE_CACHE}/{prefix}*",
            timeout=45,
        )
        for line in listed.stdout.splitlines():
            value = line.strip()
            name = Path(value).name
            if value.startswith(REMOTE_CACHE + "/") and name.startswith(prefix):
                remote_paths.add(value)
        if listed.returncode != 0 and listed.stderr.strip():
            list_errors.append(f"{prefix}: {listed.stderr.strip()}")

    pulled: list[str] = []
    pull_errors: list[dict[str, str]] = []
    for remote_path in sorted(remote_paths):
        target = cache_dir / Path(remote_path).name
        pull = run_adb(args.adb_path, args.device_id, "pull", remote_path, str(target), timeout=45)
        if pull.returncode == 0 and target.is_file():
            pulled.append(str(target))
        else:
            pull_errors.append(
                {
                    "remote_path": remote_path,
                    "reason": pull.stderr.strip() or pull.stdout.strip(),
                }
            )
    return {
        "state": "ready" if pulled else "empty",
        "mode": "selective_home_prefix_pull",
        "remote_home_cache_total": len(remote_paths),
        "home_cache_copied": len(pulled),
        "pulled_files": pulled,
        "list_errors": list_errors,
        "pull_errors": pull_errors,
    }


def main() -> None:
    args = parse_args()
    cache_dir = (args.cache_dir or default_cache_dir()).resolve()
    run_dir = (args.run_dir or args.output_path.parent).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    ui_result: dict[str, Any] = {"state": "not_run"}
    if not args.skip_ui_navigation:
        ui_result = warm_up_catalog_ui(args, run_dir)
    else:
        state = run_adb(args.adb_path, args.device_id, "get-state", timeout=15)
        ui_result = {"state": "device_ready" if state.returncode == 0 and state.stdout.strip() == "device" else "device_unavailable"}
    if ui_result.get("state") in {"ready", "app_launched", "device_ready"}:
        pull_result = pull_home_cache(args, cache_dir, run_dir)
    else:
        pull_result = {"state": "skipped", "reason": ui_result.get("reason")}
    home_rows, home_ids = extract_home_rows(cache_dir)
    advisors = extract_advisor_rows(cache_dir)
    advisor_result: dict[str, Any]
    if args.skip_advisor_catalog:
        advisor_result = {
            "state": "skipped",
            "reason": "--skip-advisor-catalog",
            "partner_total": len(advisors),
            "catalog_strategy_ids": [],
            "results": [],
        }
    elif ui_result.get("state") in {"ready", "app_launched", "device_ready"} and advisors:
        advisor_result = probe_advisor_catalog(args, advisors, run_dir)
    else:
        advisor_result = {
            "state": "skipped",
            "reason": "device_unavailable_or_advisor_cache_empty",
            "partner_total": len(advisors),
            "catalog_strategy_ids": [],
            "results": [],
        }
    rows, ids = merge_catalog_rows(home_rows, advisor_result)
    manifest = {
        "state": "ready" if ids else ("cache_only_empty" if ui_result.get("state") == "device_unavailable" else "empty"),
        "channel_id": "ttfund",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "device_id": args.device_id,
        "cache_dir": str(cache_dir),
        "catalog_strategy_ids": ids,
        "catalog_strategy_total": len(ids),
        "catalog_rows": rows,
        "home_catalog_strategy_total": len(home_ids),
        "advisor_catalog_strategy_total": len(advisor_result.get("catalog_strategy_ids") or []),
        "catalog_source": "ttfund_app_home_cache+signed_in_advisor_institution_pages",
        "catalog_complete": False,
        "catalog_completeness": "broad_advisor_directory_without_server_total",
        "catalog_complete_reason": "已合并首页缓存和登录态机构主页目录；部分机构主页可能返回空列表，且 App 不返回服务器总数，因此不宣称绝对全量。新增 ID 仍进入本轮增量采集。",
        "ui": ui_result,
        "pull": pull_result,
        "advisor_catalog": advisor_result,
    }
    if not ids and args.allow_missing_device:
        manifest["state"] = "device_unavailable"
    write_json(args.output_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
