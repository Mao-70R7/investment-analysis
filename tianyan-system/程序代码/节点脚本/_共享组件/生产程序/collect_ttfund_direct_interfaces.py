from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.progress import ConsoleProgress  # noqa: E402


APP_ID = "funda91a99886abf7e"
FLOW_ROOT = PROJECT_ROOT / "data" / "raw" / "ttfund" / "interface_probe"
DEVICE_CACHE_ROOT = PROJECT_ROOT / "data" / "raw" / "device_cache"

TTFUND_FUND_GROUP_LABELS = {
    "0": "其他",
    "1": "股票型",
    "2": "货币型",
    "3": "混合型",
    "4": "混合型",
    "6": "债券型",
    "7": "混合型",
    "8": "指数型",
    "a": "QDII",
    "A": "QDII",
}


@dataclass(frozen=True)
class FlowTemplate:
    name: str
    method: str
    url: str
    body: str
    content_type: str | None
    user_agent: str


def now_local() -> datetime:
    return datetime.now().astimezone()


def default_flow_path() -> Path:
    candidates = [path for path in FLOW_ROOT.rglob("flows.mitm") if path.is_file() and path.stat().st_size > 0]
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    return FLOW_ROOT / "flows.mitm"


def fund_group_label(group: dict[str, Any]) -> str | None:
    for key in ("newFundTypeName", "fundTypeName", "typeName"):
        value = str(group.get(key) or "").strip()
        if value:
            return value
    for key in ("newFundType", "type"):
        value = str(group.get(key) or "").strip()
        if not value:
            continue
        return TTFUND_FUND_GROUP_LABELS.get(value, value)
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch TTFund adviser strategy detail/holding/rebalance data directly from captured app APIs."
    )
    parser.add_argument("--flow-path", type=Path, default=default_flow_path())
    parser.add_argument("--strategy-id", action="append", default=[], dest="strategy_ids")
    parser.add_argument("--strategy-file", type=Path, default=None)
    parser.add_argument("--use-latest-master", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--skip-existing-success", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--fetch-latest", action="store_true", help="Also call tag=0 latest adjustment API.")
    parser.add_argument("--fetch-news", action="store_true", help="Also call adjustment trend/news API.")
    parser.add_argument(
        "--use-builtin-adjust-templates",
        action="store_true",
        help="Use known public TTFund adjustment endpoints when flows.mitm is unavailable.",
    )
    parser.add_argument(
        "--skip-detail",
        action="store_true",
        help="Only fetch adjustment endpoints; do not require or overwrite strategy detail cache.",
    )
    parser.add_argument(
        "--skip-history",
        action="store_true",
        help="Only fetch detail/latest adjustment endpoints; do not require or overwrite strategy history cache.",
    )
    return parser.parse_args()


def load_strategy_ids_from_file(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip().upper()
        if value and not value.startswith("#"):
            ids.append(value)
    return ids


def load_latest_master_ids() -> list[str]:
    root = PROJECT_ROOT / "data" / "normalized" / "ttfund" / "strategy_master"
    candidates = sorted(root.rglob("*.jsonl"))
    if not candidates:
        raise FileNotFoundError(f"no strategy_master jsonl under {root}")
    ids: list[str] = []
    for line in candidates[-1].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        sid = str(row.get("source_strategy_id") or "").strip().upper()
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        sid = str(value or "").strip().upper()
        if sid and sid not in seen:
            result.append(sid)
            seen.add(sid)
    return result


def message_text(message: Any | None) -> str:
    if message is None:
        return ""
    try:
        text = message.get_text(strict=False)
    except Exception:
        text = None
    if text is None:
        text = (message.raw_content or b"").decode("utf-8", errors="replace")
    return text


def builtin_adjust_templates() -> dict[str, FlowTemplate]:
    base_params = "product=Fund&appVersion=6.6.19&serverversion=6.6.19&version=6.6.19&plat=Android"
    return {
        "adjust_history": FlowTemplate(
            name="adjust_history",
            method="GET",
            url=(
                "https://ibgmarket.tiantianfunds.com/combine/investAdviserInfo/"
                f"getAdjustWarehouse?{base_params}&tgCode=__STRATEGY_ID__&tag=1"
            ),
            body="",
            content_type=None,
            user_agent="okhttp/3.12.13",
        ),
        "adjust_latest": FlowTemplate(
            name="adjust_latest",
            method="GET",
            url=(
                "https://ibgmarket.tiantianfunds.com/combine/investAdviserInfo/"
                f"getAdjustWarehouse?{base_params}&tgCode=__STRATEGY_ID__&tag=0"
            ),
            body="",
            content_type=None,
            user_agent="okhttp/3.12.13",
        ),
        "adjust_news": FlowTemplate(
            name="adjust_news",
            method="GET",
            url=(
                "https://ibgmarket.tiantianfunds.com/combine/investAdviserInfo/"
                f"getAdjustWarehouseNews?{base_params}&tgCode=__STRATEGY_ID__"
            ),
            body="",
            content_type=None,
            user_agent="okhttp/3.12.13",
        ),
    }


def load_templates(
    flow_path: Path,
    *,
    use_builtin_adjust_templates: bool,
    skip_detail: bool,
    skip_history: bool,
) -> dict[str, FlowTemplate]:
    templates: dict[str, FlowTemplate] = {}
    if (not skip_detail) and flow_path.exists() and flow_path.stat().st_size > 0:
        try:
            from mitmproxy import http, io
        except ModuleNotFoundError as error:
            if not use_builtin_adjust_templates:
                raise RuntimeError("mitmproxy is required to parse flows.mitm") from error
            print(
                f"[WARN] mitmproxy unavailable; skip strategy_detail template and use built-in adjustment endpoints: {error}",
                file=sys.stderr,
                flush=True,
            )
        else:
            with flow_path.open("rb") as handle:
                reader = io.FlowReader(handle)
                for flow in reader.stream():
                    if not isinstance(flow, http.HTTPFlow) or flow.request is None:
                        continue
                    url = urlsplit(flow.request.pretty_url)
                    host = url.netloc.lower()
                    path = url.path
                    name: str | None = None
                    if host == "uni-fundts.1234567.com.cn" and path == "/merge/m/api/tgfund":
                        name = "strategy_detail"
                    elif host == "ibgmarket.tiantianfunds.com" and path.endswith("/getAdjustWarehouse"):
                        query = dict(parse_qsl(url.query, keep_blank_values=True))
                        tag = query.get("tag")
                        if tag == "1":
                            name = "adjust_history"
                        elif tag == "0":
                            name = "adjust_latest"
                    elif host == "ibgmarket.tiantianfunds.com" and path.endswith("/getAdjustWarehouseNews"):
                        name = "adjust_news"
                    if not name:
                        continue
                    templates[name] = FlowTemplate(
                        name=name,
                        method=flow.request.method.upper(),
                        url=flow.request.pretty_url,
                        body=message_text(flow.request),
                        content_type=flow.request.headers.get("Content-Type"),
                        user_agent=flow.request.headers.get("User-Agent") or "okhttp/3.12.13",
                    )
    if use_builtin_adjust_templates:
        for name, template in builtin_adjust_templates().items():
            templates.setdefault(name, template)
    allow_detail_missing = use_builtin_adjust_templates and "strategy_detail" not in templates
    required: set[str] = set()
    if not skip_detail and not allow_detail_missing:
        required.add("strategy_detail")
    required.add("adjust_latest" if skip_history else "adjust_history")
    missing = sorted(required - set(templates))
    if missing:
        raise RuntimeError(f"missing templates in flow file: {', '.join(missing)}")
    return templates


def replace_strategy_params(pairs: list[tuple[str, str]], strategy_id: str, *, tag: str | None = None) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for key, value in pairs:
        if value == "__STRATEGY_ID__":
            value = strategy_id
        if key in {"tgCode", "strategyCodes", "strategyId", "code", "CODE", "TGCODE"}:
            value = strategy_id
        elif key == "tag" and tag is not None:
            value = tag
        result.append((key, value))
    return result


def request_json(template: FlowTemplate, strategy_id: str, *, timeout_sec: int, tag: str | None = None) -> dict[str, Any]:
    url_parts = urlsplit(template.url)
    query = replace_strategy_params(parse_qsl(url_parts.query, keep_blank_values=True), strategy_id, tag=tag)
    url = urlunsplit((url_parts.scheme, url_parts.netloc, url_parts.path, urlencode(query), url_parts.fragment))
    headers = {
        "User-Agent": template.user_agent,
        "Accept-Encoding": "gzip",
    }
    body_bytes: bytes | None = None
    if template.method == "POST":
        body_pairs = replace_strategy_params(parse_qsl(template.body, keep_blank_values=True), strategy_id, tag=tag)
        body_bytes = urlencode(body_pairs).encode("utf-8")
        headers["Content-Type"] = template.content_type or "application/x-www-form-urlencoded"
    request = Request(url, data=body_bytes, headers=headers, method=template.method)
    with urlopen(request, timeout=timeout_sec) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        text = raw.decode("utf-8", errors="replace")
        payload = json.loads(text)
        payload["_http_status"] = response.status
        return payload


def payload_data(payload: dict[str, Any]) -> Any:
    if "data" in payload:
        return payload.get("data")
    if "Data" in payload:
        return payload.get("Data")
    if "datas" in payload:
        return payload.get("datas")
    return None


def clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def normalize_detail_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = clone_json(payload_data(payload) or {})
    if not isinstance(data, dict):
        return {}
    if "tgCharacteristics" in data and "characteristics" not in data:
        data["characteristics"] = data.get("tgCharacteristics")
    holding = data.get("holdWareHouseInfo")
    if isinstance(holding, dict):
        for group in holding.get("holdTypeList") or []:
            if not isinstance(group, dict):
                continue
            if group.get("rate") is None:
                group["rate"] = group.get("totalRatio")
            label = fund_group_label(group)
            if label:
                group["type"] = label
            funds = group.get("fundList") or group.get("fundsList") or []
            group["fundList"] = funds
            for fund in funds:
                if not isinstance(fund, dict):
                    continue
                if fund.get("rate") is None:
                    fund["rate"] = fund.get("ratio")
                if fund.get("increaseRate") is None:
                    fund["increaseRate"] = fund.get("increase")
    return data


def normalize_adjust_group(group: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(group)
    if normalized.get("preRate") is None:
        normalized["preRate"] = group.get("preTotalRatio")
    if normalized.get("afterRate") is None:
        normalized["afterRate"] = group.get("afterTotalRatio")
    label = fund_group_label(group)
    if label:
        normalized["type"] = label
    funds = group.get("changeList") or group.get("fundList") or []
    changes: list[dict[str, Any]] = []
    for fund in funds:
        if not isinstance(fund, dict):
            continue
        item = dict(fund)
        if item.get("preRate") is None:
            item["preRate"] = fund.get("preRatio")
        if item.get("afterRate") is None:
            item["afterRate"] = fund.get("afterRatio")
        if item.get("changeState") is None:
            item["changeState"] = fund.get("newOperationType")
        changes.append(item)
    normalized["changeList"] = changes
    return normalized


def normalize_adjust_event(event: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "reason": event.get("reason"),
        "dateStr": event.get("dateStr"),
        "timeStr": event.get("timeStr"),
    }
    groups = event.get("arr") or event.get("adjustList") or []
    normalized["arr"] = [normalize_adjust_group(group) for group in groups if isinstance(group, dict)]
    return normalized


def normalize_latest_adjust(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload_data(payload) or {}
    latest = data.get("latestAdjust") if isinstance(data, dict) else None
    if not isinstance(latest, dict):
        return {}
    groups = latest.get("adjustList") or latest.get("arr") or []
    return {
        "reason": latest.get("reason"),
        "adjustList": [normalize_adjust_group(group) for group in groups if isinstance(group, dict)],
        "logoUrl": data.get("logoUrl") if isinstance(data, dict) else None,
        "dateStr": latest.get("dateStr"),
        "state": data.get("state") if isinstance(data, dict) else None,
        "isStop": None,
        "operateTime": data.get("operateTime") if isinstance(data, dict) else None,
        "currentStage": None,
    }


def normalize_history_adjust(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload_data(payload) or {}
    if not isinstance(data, dict):
        return {"adjustList": [], "logoUrl": None}
    history = data.get("adjustHistory")
    events = history if isinstance(history, list) else []
    return {
        "adjustList": [normalize_adjust_event(event) for event in events if isinstance(event, dict)],
        "logoUrl": data.get("logoUrl"),
    }


def derive_latest_adjust_from_history(history: dict[str, Any]) -> dict[str, Any]:
    events = history.get("adjustList") if isinstance(history, dict) else None
    if not isinstance(events, list) or not events:
        return {}
    latest = events[0]
    return {
        "reason": latest.get("reason"),
        "adjustList": latest.get("arr") or [],
        "logoUrl": history.get("logoUrl"),
        "dateStr": latest.get("dateStr"),
        "state": None,
        "isStop": None,
        "operateTime": None,
        "currentStage": None,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_strategy_outputs(
    *,
    strategy_id: str,
    run_dir: Path,
    detail_payload: dict[str, Any] | None,
    latest_payload: dict[str, Any] | None,
    history_payload: dict[str, Any] | None,
    news_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    strategy_run_dir = run_dir / strategy_id
    strategy_run_dir.mkdir(parents=True, exist_ok=True)
    if detail_payload is not None:
        write_json(strategy_run_dir / "raw_strategy_detail_tgfund.json", detail_payload)
    if latest_payload is not None:
        write_json(strategy_run_dir / "raw_adjust_latest_tag0.json", latest_payload)
    if history_payload is not None:
        write_json(strategy_run_dir / "raw_adjust_history_tag1.json", history_payload)
    if news_payload is not None:
        write_json(strategy_run_dir / "raw_adjust_news.json", news_payload)

    detail = normalize_detail_payload(detail_payload) if detail_payload is not None else {}
    history = normalize_history_adjust(history_payload) if history_payload is not None else {}
    latest = normalize_latest_adjust(latest_payload) if latest_payload else {}
    if history and not latest.get("adjustList"):
        latest = derive_latest_adjust_from_history(history)

    cache_dir = DEVICE_CACHE_ROOT / strategy_id
    detail_path = cache_dir / f"strategyDetailPageData{strategy_id}_{APP_ID}.0"
    latest_path = cache_dir / f"adjuseHouseList{strategy_id}_{APP_ID}.0"
    history_path = cache_dir / f"adjuseHouseListHis{strategy_id}_{APP_ID}.0"
    if detail_payload is not None and detail:
        write_json(detail_path, detail)
    if latest_payload is not None and latest:
        write_json(latest_path, latest)
    if history_payload is not None and history:
        write_json(history_path, history)

    if detail_payload is not None and detail:
        write_json(strategy_run_dir / detail_path.name, detail)
    if latest_payload is not None and latest:
        write_json(strategy_run_dir / latest_path.name, latest)
    if history_payload is not None and history:
        write_json(strategy_run_dir / history_path.name, history)

    history_events = history.get("adjustList") if isinstance(history, dict) else []
    history_data = payload_data(history_payload) if history_payload is not None else None
    history_checked_ok = (
        isinstance(history_data, dict)
        and str(history_data.get("tgCode") or "").strip().upper() == strategy_id.upper()
    )
    history_delta_count = 0
    if isinstance(history_events, list):
        for event in history_events:
            for group in event.get("arr") or []:
                history_delta_count += len(group.get("changeList") or [])

    hold = detail.get("holdWareHouseInfo") if isinstance(detail, dict) else None
    extend_info = detail.get("tgExtendInfo") if isinstance(detail, dict) else None
    benchmark_text = (
        str((extend_info or {}).get("basicCalFormulaRemark") or "").strip()
        if isinstance(extend_info, dict)
        else ""
    )
    hold_count = 0
    if isinstance(hold, dict):
        for group in hold.get("holdTypeList") or []:
            hold_count += len(group.get("fundList") or [])

    pulled_files = []
    if detail_payload is not None and detail:
        pulled_files.append(detail_path.name)
    if latest_payload is not None and latest:
        pulled_files.append(latest_path.name)
    if history_payload is not None and history:
        pulled_files.append(history_path.name)

    result = {
        "strategy_id": strategy_id,
        "detail_ok": bool(detail.get("tgExtendInfo")),
        "benchmark_text_ok": bool(benchmark_text),
        "benchmark_text": benchmark_text or None,
        "holding_info_ok": bool(hold_count > 0),
        "latest_adjustment_ok": bool(latest.get("adjustList")),
        "history_adjustment_ok": bool(history_events),
        "history_checked_ok": history_checked_ok,
        "detail_size": detail_path.stat().st_size if detail_path.exists() else None,
        "latest_size": latest_path.stat().st_size if latest_path.exists() else None,
        "history_size": history_path.stat().st_size if history_path.exists() else None,
        "holding_fund_count": hold_count,
        "history_event_count": len(history_events) if isinstance(history_events, list) else 0,
        "history_delta_count": history_delta_count,
        "pulled_files": pulled_files,
        "capture_mode": "direct_interface",
        "error": None,
    }
    write_json(strategy_run_dir / "result.json", result)
    return result


def existing_success(strategy_id: str) -> bool:
    cache_dir = DEVICE_CACHE_ROOT / strategy_id
    detail = cache_dir / f"strategyDetailPageData{strategy_id}_{APP_ID}.0"
    history = cache_dir / f"adjuseHouseListHis{strategy_id}_{APP_ID}.0"
    if not detail.exists() or not history.exists():
        return False
    try:
        detail_payload = json.loads(detail.read_text(encoding="utf-8"))
        history_payload = json.loads(history.read_text(encoding="utf-8"))
    except Exception:
        return False
    hold = detail_payload.get("holdWareHouseInfo") if isinstance(detail_payload, dict) else None
    events = history_payload.get("adjustList") if isinstance(history_payload, dict) else None
    return bool(isinstance(hold, dict) and hold.get("holdTypeList") and isinstance(events, list) and events)


def collect_one(
    strategy_id: str,
    templates: dict[str, FlowTemplate],
    run_dir: Path,
    timeout_sec: int,
    fetch_latest: bool,
    fetch_news: bool,
    skip_detail: bool,
    skip_history: bool,
) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        detail_payload = (
            None
            if skip_detail or "strategy_detail" not in templates
            else request_json(templates["strategy_detail"], strategy_id, timeout_sec=timeout_sec)
        )
        history_payload = (
            None
            if skip_history
            else request_json(templates["adjust_history"], strategy_id, timeout_sec=timeout_sec, tag="1")
        )
        latest_payload = (
            request_json(templates["adjust_latest"], strategy_id, timeout_sec=timeout_sec, tag="0")
            if fetch_latest and "adjust_latest" in templates
            else None
        )
        news_payload = (
            request_json(templates["adjust_news"], strategy_id, timeout_sec=timeout_sec)
            if fetch_news and "adjust_news" in templates
            else None
        )
        result = write_strategy_outputs(
            strategy_id=strategy_id,
            run_dir=run_dir,
            detail_payload=detail_payload,
            latest_payload=latest_payload,
            history_payload=history_payload,
            news_payload=news_payload,
        )
        result["elapsed_sec"] = round(time.perf_counter() - start, 3)
        return result
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        result = {
            "strategy_id": strategy_id,
            "detail_ok": False,
            "benchmark_text_ok": False,
            "benchmark_text": None,
            "holding_info_ok": False,
            "latest_adjustment_ok": False,
            "history_adjustment_ok": False,
            "history_checked_ok": False,
            "history_event_count": 0,
            "history_delta_count": 0,
            "holding_fund_count": 0,
            "capture_mode": "direct_interface",
            "elapsed_sec": round(time.perf_counter() - start, 3),
            "error": f"{type(error).__name__}: {error}",
        }
        strategy_run_dir = run_dir / strategy_id
        strategy_run_dir.mkdir(parents=True, exist_ok=True)
        write_json(strategy_run_dir / "result.json", result)
        return result


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    return {
        "strategy_total": total,
        "detail_ok_total": sum(1 for row in results if row.get("detail_ok")),
        "benchmark_text_ok_total": sum(1 for row in results if row.get("benchmark_text_ok")),
        "holding_info_ok_total": sum(1 for row in results if row.get("holding_info_ok")),
        "latest_adjustment_ok_total": sum(1 for row in results if row.get("latest_adjustment_ok")),
        "history_adjustment_ok_total": sum(1 for row in results if row.get("history_adjustment_ok")),
        "history_checked_ok_total": sum(1 for row in results if row.get("history_checked_ok")),
        "history_event_total": sum(int(row.get("history_event_count") or 0) for row in results),
        "history_delta_total": sum(int(row.get("history_delta_count") or 0) for row in results),
        "holding_fund_total": sum(int(row.get("holding_fund_count") or 0) for row in results),
        "error_total": sum(1 for row in results if row.get("error")),
        "avg_elapsed_sec": round(
            sum(float(row.get("elapsed_sec") or 0.0) for row in results) / total,
            3,
        )
        if total
        else 0.0,
    }


def main() -> None:
    args = parse_args()
    run_at = now_local()
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.run_dir or (
        PROJECT_ROOT / "data" / "raw" / "ttfund" / "direct_interface_runs" / run_at.strftime("%Y-%m-%d") / run_id
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    strategy_ids = list(args.strategy_ids)
    if args.strategy_file:
        strategy_ids.extend(load_strategy_ids_from_file(args.strategy_file))
    if args.use_latest_master:
        strategy_ids.extend(load_latest_master_ids())
    strategy_ids = dedupe(strategy_ids)
    if args.offset > 0:
        strategy_ids = strategy_ids[args.offset:]
    if args.limit is not None and args.limit > 0:
        strategy_ids = strategy_ids[: args.limit]
    if args.skip_existing_success:
        strategy_ids = [sid for sid in strategy_ids if not existing_success(sid)]
    if not strategy_ids:
        raise SystemExit("no strategy ids selected")

    templates = load_templates(
        args.flow_path,
        use_builtin_adjust_templates=args.use_builtin_adjust_templates,
        skip_detail=args.skip_detail,
        skip_history=args.skip_history,
    )
    results: list[dict[str, Any]] = []
    max_workers = max(1, args.workers)
    progress = ConsoleProgress("天天投顾直接接口探测", len(strategy_ids))
    progress.emit(0, success=0, failed=0, extra=f"并发数 {max_workers}")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                collect_one,
                strategy_id,
                templates,
                run_dir,
                args.timeout_sec,
                args.fetch_latest,
                args.fetch_news,
                args.skip_detail,
                args.skip_history,
            ): strategy_id
            for strategy_id in strategy_ids
        }
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            if index == 1 or index % 10 == 0 or index == len(futures):
                summary = summarize(results)
                progress.emit(
                    index,
                    success=index - summary["error_total"],
                    failed=summary["error_total"],
                    current=str(result.get("strategy_id") or ""),
                    extra=(
                        f"详情成功 {summary['detail_ok_total']} | "
                        f"调仓成功 {summary['history_adjustment_ok_total']}"
                    ),
                )

    results.sort(key=lambda row: strategy_ids.index(row["strategy_id"]) if row.get("strategy_id") in strategy_ids else 999999)
    summary = summarize(results)
    summary.update(
        {
            "run_id": run_id,
            "captured_at": run_at.isoformat(timespec="seconds"),
            "run_dir": str(run_dir),
            "flow_path": None if args.skip_detail else str(args.flow_path),
            "skip_detail": args.skip_detail,
            "skip_history": args.skip_history,
            "workers": max_workers,
            "strategy_ids": strategy_ids,
            "cache_root": str(DEVICE_CACHE_ROOT),
        }
    )
    write_json(run_dir / "results.json", results)
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
