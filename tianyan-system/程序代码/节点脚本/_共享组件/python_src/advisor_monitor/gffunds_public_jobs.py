from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from advisor_monitor.collectors.gffunds_public import (
    API_BASE,
    CHANNEL_ID,
    USER_AGENT,
    build_sign,
)


DEFAULT_DISCOVERY_OUTPUT = "data/api_probe/gffunds_discovered_gfjj_ids_latest.json"
DISCOVERY_GLOB = "gffunds_discovered_gfjj_ids*.json"


@dataclass(frozen=True)
class IncrementalCutoffs:
    performance_by_strategy: dict[str, str]
    rebalance_by_strategy: dict[str, str]
    snapshot_by_strategy: dict[str, str]


def load_strategy_ids(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []

    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []

    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        if isinstance(payload, list):
            values = payload
        elif isinstance(payload, dict):
            values = (
                payload.get("valid_ids")
                or payload.get("strategy_ids")
                or payload.get("adv_ids")
                or []
            )
        else:
            values = []
    else:
        values = [line.strip() for line in text.splitlines() if line.strip()]

    result: list[str] = []
    for item in values:
        strategy_id = str(item).strip()
        if re.fullmatch(r"GFJJ\d{6}", strategy_id) and strategy_id not in result:
            result.append(strategy_id)
    return result


def write_strategy_ids(
    path: Path,
    *,
    strategy_ids: list[str],
    scanned_ranges: list[str],
    discovered_at: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "discovered_at": discovered_at or date.today().isoformat(),
        "scanned_ranges": scanned_ranges,
        "valid_total": len(strategy_ids),
        "valid_ids": strategy_ids,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def find_latest_discovered_strategy_file(project_root: Path) -> Path | None:
    api_probe_dir = project_root / "data" / "api_probe"
    if not api_probe_dir.exists():
        return None

    candidates = list(api_probe_dir.glob(DISCOVERY_GLOB))
    if not candidates:
        return None

    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def default_discovery_output_path(project_root: Path) -> Path:
    return project_root / DEFAULT_DISCOVERY_OUTPUT


def post_public_json(endpoint: str, body: dict[str, Any], *, timeout: int = 45) -> dict[str, Any] | None:
    url = f"{API_BASE}/{endpoint.lstrip('/')}"
    payload = urlencode(build_sign(body)).encode("utf-8")

    for attempt in range(1, 4):
        request = Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "GFF-Charset": "UTF-8",
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        raw_bytes = b""

        try:
            with urlopen(request, timeout=timeout) as response:
                try:
                    raw_bytes = response.read()
                except IncompleteRead as error:
                    raw_bytes = error.partial
        except HTTPError as error:
            raw_bytes = error.read()
        except URLError:
            raw_bytes = b""
        except (TimeoutError, OSError):
            raw_bytes = b""

        if not raw_bytes:
            if attempt == 3:
                return None
            continue

        try:
            decoded = json.loads(raw_bytes.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            decoded = None

        if isinstance(decoded, dict):
            return decoded
        if attempt == 3:
            return None

    return None


def discover_strategy_ids(
    *,
    start: int = 1,
    end: int = 5000,
    workers: int = 16,
) -> list[str]:
    if start < 1 or end < start:
        raise ValueError("invalid discovery range")

    candidates = [f"GFJJ{value:06d}" for value in range(start, end + 1)]
    results: list[str] = []

    def probe(strategy_id: str) -> str | None:
        payload = post_public_json(
            "get_investadvisor_operate_config_byids",
            {"session_id": "", "adv_ids": strategy_id},
        )
        config_list = (payload or {}).get("config_list") or []
        if (payload or {}).get("RETCODE") != "0000" or not config_list:
            return None
        adv_id = str(config_list[0].get("adv_id") or strategy_id).strip()
        return adv_id if re.fullmatch(r"GFJJ\d{6}", adv_id) else None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {executor.submit(probe, strategy_id): strategy_id for strategy_id in candidates}
        for future in as_completed(future_map):
            strategy_id = future.result()
            if strategy_id and strategy_id not in results:
                results.append(strategy_id)

    return sorted(results)


def iter_entity_jsonl_paths(project_root: Path, entity_name: str):
    entity_dir = project_root / "data" / "normalized" / CHANNEL_ID / entity_name
    if not entity_dir.exists():
        return
    for day_dir in sorted(path for path in entity_dir.iterdir() if path.is_dir()):
        for jsonl_path in sorted(day_dir.glob("*.jsonl")):
            yield jsonl_path


def load_latest_date_map(
    project_root: Path,
    *,
    entity_name: str,
    strategy_key: str,
    date_key: str,
) -> dict[str, str]:
    latest: dict[str, str] = {}
    for jsonl_path in iter_entity_jsonl_paths(project_root, entity_name) or []:
        with jsonl_path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                row = json.loads(line)
                strategy_id = str(row.get(strategy_key) or "").strip()
                date_value = str(row.get(date_key) or "").strip()
                if not strategy_id or not date_value:
                    continue
                previous = latest.get(strategy_id)
                if previous is None or date_value > previous:
                    latest[strategy_id] = date_value
    return latest


def load_latest_strategy_master_rows(project_root: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, tuple[tuple[str, str, str], dict[str, Any]]] = {}
    for jsonl_path in iter_entity_jsonl_paths(project_root, "strategy_master") or []:
        file_order = f"{jsonl_path.parent.name}/{jsonl_path.stem}"
        with jsonl_path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                row = json.loads(line)
                strategy_id = str(row.get("source_strategy_id") or "").strip()
                if not strategy_id:
                    continue
                rank = (
                    str(row.get("last_seen_at") or ""),
                    str(row.get("run_id") or ""),
                    file_order,
                )
                current = latest.get(strategy_id)
                if current is None or rank > current[0]:
                    latest[strategy_id] = (rank, row)
    return {strategy_id: item[1] for strategy_id, item in latest.items()}


def load_latest_protocol_cache_rows(project_root: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, tuple[tuple[str, str, str], dict[str, Any]]] = {}
    for jsonl_path in iter_entity_jsonl_paths(project_root, "strategy_master") or []:
        file_order = f"{jsonl_path.parent.name}/{jsonl_path.stem}"
        with jsonl_path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                row = json.loads(line)
                strategy_id = str(row.get("source_strategy_id") or "").strip()
                extra = row.get("extra") or {}
                text_path = extra.get("protocol_text_path")
                if not strategy_id or not text_path:
                    continue
                protocol_path = Path(str(text_path))
                if not protocol_path.exists():
                    continue
                rank = (
                    str(row.get("last_seen_at") or ""),
                    str(row.get("run_id") or ""),
                    file_order,
                )
                current = latest.get(strategy_id)
                if current is None or rank > current[0]:
                    latest[strategy_id] = (rank, row)
    return {strategy_id: item[1] for strategy_id, item in latest.items()}


def load_incremental_cutoffs(project_root: Path) -> IncrementalCutoffs:
    performance = load_latest_date_map(
        project_root,
        entity_name="strategy_performance_daily",
        strategy_key="source_strategy_id",
        date_key="trade_date",
    )
    rebalance = load_latest_date_map(
        project_root,
        entity_name="strategy_rebalance_event",
        strategy_key="source_strategy_id",
        date_key="rebalance_date",
    )
    snapshot = load_latest_date_map(
        project_root,
        entity_name="strategy_fund_snapshot",
        strategy_key="source_strategy_id",
        date_key="position_date",
    )
    return IncrementalCutoffs(
        performance_by_strategy=performance,
        rebalance_by_strategy=rebalance,
        snapshot_by_strategy=snapshot,
    )


def merge_date_maps(*maps: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for mapping in maps:
        for strategy_id, date_value in mapping.items():
            previous = merged.get(strategy_id)
            if previous is None or date_value > previous:
                merged[strategy_id] = date_value
    return merged
