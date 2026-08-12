from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from advisor_monitor.models import RawSnapshot
from advisor_monitor.progress import ConsoleProgress
from advisor_monitor.storage import write_jsonl


CHANNEL_ID = "ttfund"
CHANNEL_NAME = "天天基金/投顾"
QUOTE_API_URL = "https://ibgmarket.tiantianfunds.com/combine/investAdviserInfo/getTGQuoteByFavor"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"
TTFUND_PACKAGE = "com.eastmoney.android.fund"
DEVICE_CACHE_REMOTE_DIR = f"/sdcard/Android/data/{TTFUND_PACKAGE}/files/.ttjj_cache"
DETAIL_DEEPLINK_TEMPLATE = (
    "fund://mp.1234567.com.cn/weex/funda91a99886abf7e/pages/strategyDetail/index"
    "?id={strategy_id}&showKycPopup=1"
)
HOME_CACHE_PREFIXES = (
    "layout_tougu-scroll-view",
    "saveAllAdvisersInfokey",
    "home-vuex_",
    "EFAppHomeConfigData",
)
DETAIL_CACHE_PATTERNS = (
    re.compile(r"strategyDetailPageData(?P<sid>[A-Za-z0-9]+)_"),
    re.compile(r"ttfund-layout-cache-advicer-strategy-detail-matter-(?P<sid>[A-Za-z0-9]+)-"),
)
ADJUSTMENT_HISTORY_CACHE_PATTERNS = (
    re.compile(r"adjuseHouseListHis(?P<sid>[A-Za-z0-9]+)_"),
)
ADJUSTMENT_CACHE_PATTERNS = (
    re.compile(r"adjuseHouseList(?P<sid>[A-Za-z0-9]+)_"),
)
TRANSIENT_CACHE_NAME_MARKERS = (
    ".sync-conflict-",
    ".tmp",
)
QUOTE_INTERVAL_FIELDS = [
    ("1w", "近1周", "SYL_Z"),
    ("1m", "近1月", "SYL_Y"),
    ("3m", "近3月", "SYL_3Y"),
    ("6m", "近6月", "SYL_6Y"),
    ("ytd", "今年来", "SYL_JN"),
    ("1y", "近1年", "SYL_1N"),
    ("2y", "近2年", "SYL_2N"),
    ("3y", "近3年", "SYL_3N"),
    ("since_inception", "成立来", "SYL_LN"),
]

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


def ignore_transient_cache_entries(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if any(marker in name for marker in TRANSIENT_CACHE_NAME_MARKERS)
    }


def copy_mutable_cache_tree(source_root: Path, dest_root: Path) -> int:
    try:
        shutil.copytree(
            source_root,
            dest_root,
            dirs_exist_ok=True,
            ignore=ignore_transient_cache_entries,
        )
        return 0
    except shutil.Error as exc:
        errors = exc.args[0] if exc.args else None
        if not isinstance(errors, list):
            raise
        missing_markers = ("[WinError 2]", "[WinError 3]", "[Errno 2]")
        disappeared = [
            error
            for error in errors
            if isinstance(error, tuple)
            and len(error) >= 3
            and any(marker in str(error[2]) for marker in missing_markers)
        ]
        unexpected = [error for error in errors if error not in disappeared]
        if unexpected:
            raise shutil.Error(unexpected) from exc
        return len(disappeared)


@dataclass(frozen=True)
class RawResponse:
    json_data: dict[str, Any] | None
    text: str
    snapshot: dict[str, Any]
    raw_path: Path


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text == "--":
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


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


def parse_ymd(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "--":
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", text):
        return text[:10]
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return None


def parse_epoch_millis(value: Any) -> str | None:
    if value in (None, "", "--"):
        return None
    try:
        millis = int(str(value))
    except ValueError:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


def parse_mmdd(value: Any, *, year: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    match = re.search(r"(?P<month>\d{2})-(?P<day>\d{2})", text)
    if not match:
        return None
    month = int(match.group("month"))
    day = int(match.group("day"))
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_mmdd_with_reference(value: Any, reference_date: str | None) -> str | None:
    base_year = None
    if reference_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", reference_date):
        base_year = int(reference_date[:4])
    if base_year is None:
        return None
    parsed = parse_mmdd(value, year=base_year)
    if not parsed:
        return None
    if parsed > reference_date:
        return f"{base_year - 1:04d}{parsed[4:]}"
    return parsed


def parse_date_from_prefixed_text(value: Any) -> str | None:
    if value is None:
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(value))
    return match.group(1) if match else None


def split_tags(*values: Any) -> list[str]:
    tags: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = re.split(r"[,，/、|]+", str(value))
        for item in items:
            text = str(item).strip()
            if text and text != "--" and text not in tags:
                tags.append(text)
    return tags


def build_snapshot_id(channel_id: str, collector_name: str, raw_bytes: bytes, unique_hint: str) -> str:
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    hint_hash = hashlib.sha1(unique_hint.encode("utf-8")).hexdigest()
    return f"{channel_id}-{collector_name}-{content_hash[:12]}-{hint_hash[:6]}"


def format_advisory_fee_rate(rate: Any, provision_type: Any) -> str | None:
    numeric = to_float(rate)
    unit = str(provision_type or "").strip()
    if numeric is None and not unit:
        return None
    if numeric is None:
        return unit or None
    text = f"{numeric:.2f}%"
    return f"{text}{unit}" if unit else text


def normalize_benchmark_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "null", "None"}:
        return None
    return text


def first_non_empty_text(*values: Any) -> str | None:
    for value in values:
        text = normalize_benchmark_text(value)
        if text:
            return text
    return None


def extract_detail_benchmark(detail: dict[str, Any]) -> str | None:
    """Extract the App-disclosed benchmark formula from detail payloads."""
    candidates = (
        "basicCalFormulaRemark",
        "basicCalFormula",
        "benchmark",
        "benchmarkDesc",
        "benchmarkRemark",
        "standardDesc",
        "业绩比较基准",
        "业绩基准",
    )
    extend_info = detail.get("tgExtendInfo") if isinstance(detail, dict) else None
    containers = [extend_info, detail]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in candidates:
            value = normalize_benchmark_text(container.get(key))
            if value:
                return value
    for node in recursive_nodes(detail):
        for key in candidates:
            value = normalize_benchmark_text(node.get(key))
            if value:
                return value
    return None


def recursive_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from recursive_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_nodes(child)


class TTFundLoggedInCollector:
    def __init__(
        self,
        project_root: Path,
        *,
        device_id: str | None = None,
        sync_device_cache: bool = True,
        input_cache_dir: Path | None = None,
        strategy_ids: list[str] | None = None,
        limit: int | None = None,
        quote_batch_size: int = 200,
        fetch_public_quote: bool = True,
        adb_path: str | Path = "adb",
        run_id: str | None = None,
    ) -> None:
        self.project_root = project_root
        self.device_id = device_id
        self.sync_device_cache = sync_device_cache
        self.input_cache_dir = input_cache_dir
        selected_ids = [str(value or "").strip() for value in (strategy_ids or [])]
        self.requested_strategy_ids = list(dict.fromkeys(value for value in selected_ids if value)) or None
        self.limit = limit
        self.quote_batch_size = max(1, quote_batch_size)
        self.fetch_public_quote = fetch_public_quote
        self.adb_path = str(adb_path)
        self.run_at = now_local()
        self.day = self.run_at.strftime("%Y-%m-%d")
        self.run_id = run_id or self.run_at.strftime("%Y%m%dT%H%M%S%z")
        self.captured_at = self.run_at.isoformat(timespec="seconds")
        self.raw_base_dir = (
            project_root / "data" / "raw" / CHANNEL_ID / "loggedin_cache" / self.day / self.run_id
        )
        self.normalized_base_dir = project_root / "data" / "normalized" / CHANNEL_ID
        self.raw_snapshots: list[dict[str, Any]] = []
        self._snapshot_lock = threading.Lock()
        self.file_snapshot_by_path: dict[str, str] = {}
        self.cache_copy_disappeared_total = 0

    def collect(self) -> dict[str, Any]:
        self.raw_base_dir.mkdir(parents=True, exist_ok=True)
        cache_root, cache_source = self.prepare_cache_root()
        cache_files = self.register_cache_files(cache_root, cache_source)
        home_payloads = self.load_home_payloads(cache_files)
        home_data = self.extract_home_data(home_payloads)
        detail_payloads = self.load_detail_payloads(cache_files)
        adjustment_payloads = self.load_adjustment_payloads(cache_files)

        strategy_ids = list(home_data["strategy_order"])
        for strategy_id in detail_payloads:
            if strategy_id not in strategy_ids:
                strategy_ids.append(strategy_id)
        for strategy_id in adjustment_payloads:
            if strategy_id not in strategy_ids:
                strategy_ids.append(strategy_id)
        if self.fetch_public_quote and self.requested_strategy_ids is None:
            for strategy_id in self.load_strategy_ids_from_analysis_db():
                if strategy_id not in strategy_ids:
                    strategy_ids.append(strategy_id)
        if self.requested_strategy_ids is not None:
            strategy_ids = list(self.requested_strategy_ids)
        if self.limit and self.limit > 0:
            strategy_ids = strategy_ids[: self.limit]

        home_strategies = {
            strategy_id: info
            for strategy_id, info in home_data["strategies"].items()
            if strategy_id in strategy_ids
        }
        detail_payloads = {
            strategy_id: payload
            for strategy_id, payload in detail_payloads.items()
            if strategy_id in strategy_ids
        }
        adjustment_payloads = {
            strategy_id: payload
            for strategy_id, payload in adjustment_payloads.items()
            if strategy_id in strategy_ids
        }

        quotes_by_strategy = (
            self.collect_quote_snapshots(strategy_ids)
            if self.fetch_public_quote and strategy_ids
            else {}
        )

        normalized = self.normalize(
            strategy_ids,
            home_strategies,
            detail_payloads,
            adjustment_payloads,
            quotes_by_strategy,
        )
        self.write_normalized(normalized)
        summary = self.build_summary(
            strategy_ids,
            home_strategies,
            detail_payloads,
            adjustment_payloads,
            quotes_by_strategy,
            normalized,
            cache_root,
            cache_source,
        )
        self.write_run_manifest(summary)
        return summary

    def load_strategy_ids_from_analysis_db(self) -> list[str]:
        db_path = self.project_root / "data" / "analysis_zh_current.sqlite"
        if not db_path.exists():
            return []
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                'SELECT "渠道策略ID" FROM "策略信息" WHERE "渠道ID" = ? ORDER BY "渠道策略ID"',
                (CHANNEL_ID,),
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            try:
                conn.close()
            except UnboundLocalError:
                pass
        return [str(row[0]).strip() for row in rows if row[0] and str(row[0]).strip()]

    def prepare_cache_root(self) -> tuple[Path, str]:
        if self.sync_device_cache:
            return self.sync_cache_from_device(), "adb"
        source_root = self.input_cache_dir or (self.project_root / "data" / "raw" / "device_cache")
        if not source_root.exists():
            raise FileNotFoundError(f"cache directory not found: {source_root}")
        dest_root = self.raw_base_dir / "imported_cache"
        self.cache_copy_disappeared_total = copy_mutable_cache_tree(source_root, dest_root)
        if self.cache_copy_disappeared_total:
            print(
                "[WARN] mutable cache snapshot skipped "
                f"{self.cache_copy_disappeared_total} entries that disappeared during copy",
                flush=True,
            )
        return dest_root, "local"

    def resolve_device_id(self) -> str:
        if self.device_id:
            return self.device_id
        completed = subprocess.run(
            [self.adb_path, "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        devices = []
        for line in completed.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        if not devices:
            raise RuntimeError("no adb device detected")
        if len(devices) > 1:
            raise RuntimeError("multiple adb devices detected, please specify --device-id")
        self.device_id = devices[0]
        return self.device_id

    def sync_cache_from_device(self) -> Path:
        device_id = self.resolve_device_id()
        dest_root = self.raw_base_dir / "device_cache"
        dest_root.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix="ttfund_cache_"))
        try:
            cmd = [self.adb_path, "-s", device_id, "pull", DEVICE_CACHE_REMOTE_DIR, str(temp_root)]
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            (self.raw_base_dir / "adb_pull.stdout.log").write_text(completed.stdout, encoding="utf-8")
            (self.raw_base_dir / "adb_pull.stderr.log").write_text(completed.stderr, encoding="utf-8")
            if completed.returncode != 0:
                raise RuntimeError(f"adb pull failed: {completed.stderr.strip()}")
            shutil.copytree(
                temp_root,
                dest_root,
                dirs_exist_ok=True,
                ignore=ignore_transient_cache_entries,
            )
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
        return dest_root

    def register_cache_files(self, cache_root: Path, cache_source: str) -> list[Path]:
        cache_files = sorted(path for path in cache_root.rglob("*") if path.is_file() and path.suffix == ".0")
        for path in cache_files:
            raw_bytes = path.read_bytes()
            collector_name = self.classify_cache_file(path.name)
            unique_hint = str(path.relative_to(cache_root)).replace("\\", "/")
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            if cache_source == "adb":
                source_url = f"adb://{self.resolve_device_id()}{DEVICE_CACHE_REMOTE_DIR}/{unique_hint}"
            else:
                original_root = self.input_cache_dir or (self.project_root / "data" / "raw" / "device_cache")
                source_url = str((original_root / path.relative_to(cache_root)).resolve())
            snapshot = RawSnapshot(
                snapshot_id=build_snapshot_id(CHANNEL_ID, collector_name, raw_bytes, unique_hint),
                channel_id=CHANNEL_ID,
                collector_name=collector_name,
                access_level="login",
                captured_at=self.captured_at,
                source_url=source_url,
                http_status=None,
                raw_path=str(path),
                content_type="application/json",
                content_hash=content_hash,
                parse_status="success",
            ).to_dict()
            with self._snapshot_lock:
                self.raw_snapshots.append(snapshot)
                self.file_snapshot_by_path[str(path.resolve())] = snapshot["snapshot_id"]
        return cache_files

    def classify_cache_file(self, filename: str) -> str:
        if filename.startswith("layout_tougu-scroll-view"):
            return "home_layout_cache"
        if filename.startswith("saveAllAdvisersInfokey"):
            return "save_all_advisers_cache"
        if filename.startswith("home-vuex_"):
            return "home_vuex_cache"
        if filename.startswith("EFAppHomeConfigData"):
            return "home_config_cache"
        if filename.startswith("strategyDetailPageData"):
            return "strategy_detail_cache"
        if filename.startswith("ttfund-layout-cache-advicer-strategy-detail-matter-"):
            return "strategy_detail_matter_cache"
        if filename.startswith("adjuseHouseListHis"):
            return "strategy_adjustment_history_cache"
        if filename.startswith("adjuseHouseList"):
            return "strategy_adjustment_cache"
        if filename.startswith("kyc-result-"):
            return "strategy_kyc_cache"
        return "device_cache"

    def load_cache_json(self, path: Path) -> Any | None:
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, str):
            inner = decoded.strip()
            if inner and inner[0] in "{[":
                try:
                    return json.loads(inner)
                except json.JSONDecodeError:
                    return decoded
        return decoded

    def load_home_payloads(self, cache_files: list[Path]) -> list[tuple[Path, dict[str, Any]]]:
        payloads: list[tuple[Path, dict[str, Any]]] = []
        for path in cache_files:
            if not path.name.startswith(HOME_CACHE_PREFIXES):
                continue
            payload = self.load_cache_json(path)
            if isinstance(payload, dict):
                payloads.append((path, payload))
        return payloads

    def extract_home_data(self, payloads: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
        strategies: dict[str, dict[str, Any]] = {}
        strategy_order: list[str] = []
        partner_map: dict[str, str] = {}

        for path, payload in payloads:
            snapshot_id = self.file_snapshot_by_path.get(str(path.resolve()))
            for node in recursive_nodes(payload):
                advicer_code = str(node.get("advicerCode") or "").strip()
                advicer_name = str(node.get("advicerName") or "").strip()
                if advicer_code and advicer_name:
                    partner_map[advicer_code] = advicer_name

                strategy_id = str(node.get("strategyId") or "").strip()
                if not strategy_id:
                    continue
                home_row = self.extract_home_strategy_row(node, strategy_id)
                if strategy_id not in strategies:
                    strategies[strategy_id] = home_row
                    strategies[strategy_id]["source_snapshot_ids"] = []
                    strategy_order.append(strategy_id)
                else:
                    strategies[strategy_id] = self.merge_home_strategy_row(strategies[strategy_id], home_row)
                if snapshot_id and snapshot_id not in strategies[strategy_id]["source_snapshot_ids"]:
                    strategies[strategy_id]["source_snapshot_ids"].append(snapshot_id)

        for row in strategies.values():
            partner_id = row.get("partner_id")
            if not row.get("advisor_name") and partner_id:
                row["advisor_name"] = partner_map.get(str(partner_id))

        return {
            "strategies": strategies,
            "strategy_order": strategy_order,
            "partner_map": partner_map,
        }

    def extract_home_strategy_row(self, node: dict[str, Any], strategy_id: str) -> dict[str, Any]:
        return {
            "source_strategy_id": strategy_id,
            "strategy_name": str(node.get("strategyName") or "").strip() or None,
            "advisor_name": str(node.get("jjgs") or "").strip() or None,
            "strategy_type": str(node.get("styleName") or "").strip() or None,
            "partner_id": str(node.get("partnerId") or "").strip() or None,
            "launch_date": parse_epoch_millis(node.get("establishDate")),
            "suggested_holding_period": str(node.get("shTime") or "").strip() or None,
            "description": str(node.get("resume") or "").strip() or None,
            "skip_url": str(node.get("skipUrl") or "").strip() or None,
            "risk_check": node.get("riskCheck"),
            "hold_limit": str(node.get("holdLimit") or "").strip() or None,
            "hold_limit_2": str(node.get("holdLimit2") or "").strip() or None,
            "key_title": str(node.get("keyTitle") or "").strip() or None,
            "key_title_2": str(node.get("keyTitle2") or "").strip() or None,
            "latest_year_profit": to_float(node.get("latestYearProfit")),
            "annual_rate": to_float(node.get("annualRate")),
            "draw_down": to_float(node.get("drawDown")),
            "continued_days": node.get("continuedData"),
            "classification_one": node.get("classificationOne"),
            "classification_two": node.get("classificationTwo"),
            "interval_info": node.get("intervalInfo") if isinstance(node.get("intervalInfo"), list) else [],
            "extra_home_raw": node,
        }

    def merge_home_strategy_row(self, current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(current)
        for key in (
            "strategy_name",
            "advisor_name",
            "strategy_type",
            "partner_id",
            "launch_date",
            "suggested_holding_period",
            "description",
            "skip_url",
            "hold_limit",
            "hold_limit_2",
            "key_title",
            "key_title_2",
        ):
            if not merged.get(key) and incoming.get(key):
                merged[key] = incoming[key]
        for key in (
            "latest_year_profit",
            "annual_rate",
            "draw_down",
            "continued_days",
            "classification_one",
            "classification_two",
            "risk_check",
        ):
            if merged.get(key) is None and incoming.get(key) is not None:
                merged[key] = incoming[key]
        if len(incoming.get("interval_info") or []) > len(merged.get("interval_info") or []):
            merged["interval_info"] = incoming["interval_info"]
        merged["extra_home_raw"] = merged.get("extra_home_raw") or incoming.get("extra_home_raw")
        return merged

    def load_detail_payloads(self, cache_files: list[Path]) -> dict[str, dict[str, Any]]:
        detail_payloads: dict[str, dict[str, Any]] = {}
        for path in cache_files:
            strategy_id = self.extract_strategy_id_from_detail_filename(path.name)
            if not strategy_id:
                continue
            payload = self.load_cache_json(path)
            if not isinstance(payload, dict):
                continue
            priority = 2 if path.name.startswith("strategyDetailPageData") else 1
            current = detail_payloads.get(strategy_id)
            if current and current["priority"] >= priority:
                continue
            detail_payloads[strategy_id] = {
                "payload": payload,
                "raw_path": str(path),
                "source_snapshot_id": self.file_snapshot_by_path.get(str(path.resolve())),
                "priority": priority,
            }
        return detail_payloads

    def load_adjustment_payloads(self, cache_files: list[Path]) -> dict[str, dict[str, Any]]:
        adjustment_payloads: dict[str, dict[str, Any]] = {}
        for path in cache_files:
            payload_type, strategy_id = self.extract_strategy_id_from_adjustment_filename(path.name)
            if not payload_type or not strategy_id:
                continue
            payload = self.load_cache_json(path)
            if not isinstance(payload, dict):
                continue
            meta = adjustment_payloads.setdefault(strategy_id, {})
            meta[f"{payload_type}_payload"] = payload
            meta[f"{payload_type}_raw_path"] = str(path)
            meta[f"{payload_type}_source_snapshot_id"] = self.file_snapshot_by_path.get(str(path.resolve()))
        return adjustment_payloads

    def extract_strategy_id_from_detail_filename(self, filename: str) -> str | None:
        for pattern in DETAIL_CACHE_PATTERNS:
            match = pattern.search(filename)
            if match:
                return match.group("sid")
        return None

    def extract_strategy_id_from_adjustment_filename(self, filename: str) -> tuple[str | None, str | None]:
        for pattern in ADJUSTMENT_HISTORY_CACHE_PATTERNS:
            match = pattern.search(filename)
            if match:
                return "history", match.group("sid")
        for pattern in ADJUSTMENT_CACHE_PATTERNS:
            match = pattern.search(filename)
            if match:
                return "latest", match.group("sid")
        return None, None

    def collect_quote_snapshots(self, strategy_ids: list[str]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        starts = list(range(0, len(strategy_ids), self.quote_batch_size))
        progress = ConsoleProgress("天天投顾行情快照更新", len(strategy_ids))
        progress.emit(0, success=0, failed=0, extra=f"批次数 {len(starts)}")
        for batch_index, start in enumerate(starts, start=1):
            batch_ids = strategy_ids[start:start + self.quote_batch_size]
            batch_label = f"{batch_index:04d}"
            self.collect_quote_batch_with_retry(batch_ids, batch_label, results)
            completed = min(start + len(batch_ids), len(strategy_ids))
            success = sum(1 for strategy_id in strategy_ids[:completed] if strategy_id in results)
            progress.emit(
                completed,
                success=success,
                failed=max(0, completed - success),
                current=f"批次 {batch_index}/{len(starts)}",
                extra=f"批次大小 {len(batch_ids)}",
            )
        return results

    def collect_quote_batch_with_retry(
        self,
        strategy_ids: list[str],
        batch_label: str,
        results: dict[str, dict[str, Any]],
    ) -> None:
        response = self.post_quote_batch(strategy_ids, batch_label)
        payload = response.json_data or {}
        returned_ids: set[str] = set()
        for row in payload.get("Data") or []:
            strategy_id = str(row.get("TGCODE") or "").strip()
            if not strategy_id:
                continue
            returned_ids.add(strategy_id)
            results[strategy_id] = {
                "row": row,
                "source_snapshot_id": response.snapshot["snapshot_id"],
            }

        missing_ids = [strategy_id for strategy_id in strategy_ids if strategy_id not in returned_ids]
        if not missing_ids:
            return
        if len(missing_ids) == 1:
            return

        split_index = len(missing_ids) // 2
        if split_index <= 0:
            split_index = 1
        chunks = [missing_ids[:split_index], missing_ids[split_index:]]
        for child_index, chunk in enumerate(chunks, start=1):
            chunk = [strategy_id for strategy_id in chunk if strategy_id]
            if not chunk:
                continue
            child_label = f"{batch_label}_r{child_index:02d}"
            self.collect_quote_batch_with_retry(chunk, child_label, results)

    def post_quote_batch(self, strategy_ids: list[str], batch_index: str) -> RawResponse:
        tg_code_with_date = ",".join(f"{strategy_id}_{self.day}" for strategy_id in strategy_ids)
        payload = f"tgCodeWithDateStr={tg_code_with_date}".encode("utf-8")
        status: int | None = None
        content_type: str | None = None
        raw_bytes = b""
        parse_status = "success"

        json_data: dict[str, Any] | None = None
        text = ""
        for attempt in range(1, 4):
            request = Request(
                QUOTE_API_URL,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            parse_status = "success"
            try:
                with urlopen(request, timeout=45) as response:
                    status = response.status
                    content_type = response.headers.get("Content-Type")
                    try:
                        raw_bytes = response.read()
                    except IncompleteRead as error:
                        raw_bytes = error.partial
                        parse_status = "partial"
            except HTTPError as error:
                status = error.code
                content_type = error.headers.get("Content-Type") if error.headers else None
                raw_bytes = error.read()
                parse_status = "failed"
            except URLError as error:
                raw_bytes = json.dumps(
                    {"transport_error": str(error.reason), "strategy_ids": strategy_ids},
                    ensure_ascii=False,
                ).encode("utf-8")
                parse_status = "failed"
            except OSError as error:
                raw_bytes = json.dumps(
                    {"transport_error": str(error), "strategy_ids": strategy_ids},
                    ensure_ascii=False,
                ).encode("utf-8")
                parse_status = "failed"

            text = raw_bytes.decode("utf-8", errors="replace")
            try:
                decoded = json.loads(text)
                json_data = decoded if isinstance(decoded, dict) else {"data": decoded}
                break
            except json.JSONDecodeError:
                json_data = None
                parse_status = "failed"
                if attempt == 3:
                    break

        safe_batch_index = re.sub(r"[^A-Za-z0-9_-]+", "_", str(batch_index))
        raw_path = self.raw_base_dir / "quotes" / f"batch_{safe_batch_index}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw_bytes)
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        snapshot = RawSnapshot(
            snapshot_id=build_snapshot_id(CHANNEL_ID, "quote_batch", raw_bytes, raw_path.name),
            channel_id=CHANNEL_ID,
            collector_name="quote_batch",
            access_level="public",
            captured_at=self.captured_at,
            source_url=QUOTE_API_URL,
            http_status=status,
            raw_path=str(raw_path),
            content_type=content_type,
            content_hash=content_hash,
            parse_status=parse_status,
        ).to_dict()
        with self._snapshot_lock:
            self.raw_snapshots.append(snapshot)
        return RawResponse(json_data=json_data, text=text, snapshot=snapshot, raw_path=raw_path)

    def normalize(
        self,
        strategy_ids: list[str],
        home_strategies: dict[str, dict[str, Any]],
        detail_payloads: dict[str, dict[str, Any]],
        adjustment_payloads: dict[str, dict[str, Any]],
        quotes_by_strategy: dict[str, dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        strategy_master: list[dict[str, Any]] = []
        performance_daily: list[dict[str, Any]] = []
        performance_interval: list[dict[str, Any]] = []
        fund_snapshot: list[dict[str, Any]] = []
        rebalance_events: list[dict[str, Any]] = []
        rebalance_deltas: list[dict[str, Any]] = []
        fund_public_dim: dict[str, dict[str, Any]] = {}

        for strategy_id in strategy_ids:
            home = home_strategies.get(strategy_id) or {}
            detail_meta = detail_payloads.get(strategy_id) or {}
            detail = detail_meta.get("payload") or {}
            adjustment_meta = adjustment_payloads.get(strategy_id) or {}
            adjustment = adjustment_meta.get("payload") or {}
            quote_meta = quotes_by_strategy.get(strategy_id) or {}
            quote = quote_meta.get("row") or {}
            extend_info = detail.get("tgExtendInfo") or {}
            cfh_info = detail.get("cfhInfo") or {}
            holding_info = detail.get("holdWareHouseInfo") or {}
            detail_strategy = extend_info.get("strategy") or {}
            source_url = (
                home.get("skip_url")
                or DETAIL_DEEPLINK_TEMPLATE.format(strategy_id=strategy_id)
            )
            strategy_description = self.build_strategy_description(detail_strategy) or home.get("description")
            benchmark = extract_detail_benchmark(detail)
            launch_date = (
                parse_ymd(quote.get("ESTABDATE"))
                or home.get("launch_date")
            )
            tags = split_tags(
                extend_info.get("label1"),
                extend_info.get("label2"),
                extend_info.get("label3"),
                home.get("strategy_type"),
            )
            strategy_master.append(
                {
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": strategy_id,
                    "strategy_name": extend_info.get("tgName")
                    or quote.get("TGNAME")
                    or home.get("strategy_name")
                    or strategy_id,
                    "advisor_name": first_non_empty_text(
                        extend_info.get("logoName"),
                        quote.get("LOGO_NAME"),
                        home.get("advisor_name"),
                        cfh_info.get("fortuneName"),
                    ),
                    "strategy_type": home.get("strategy_type"),
                    "risk_level": extend_info.get("risk"),
                    "launch_date": launch_date,
                    "suggested_holding_period": extend_info.get("investTerm") or home.get("suggested_holding_period"),
                    "minimum_amount": to_float(extend_info.get("minBuy")),
                    "advisory_fee_rate": format_advisory_fee_rate(
                        extend_info.get("strategyRate"),
                        extend_info.get("provisionType"),
                    ),
                    "benchmark": benchmark,
                    "tags": tags,
                    "strategy_description": strategy_description,
                    "status": self.build_strategy_status(extend_info, quote, home),
                    "source_url": source_url,
                    "first_seen_at": self.captured_at,
                    "last_seen_at": self.captured_at,
                    "run_id": self.run_id,
                    "source_snapshot_id": detail_meta.get("source_snapshot_id")
                    or quote_meta.get("source_snapshot_id")
                    or (home.get("source_snapshot_ids") or [None])[-1],
                    "extra": {
                        "partner_id": extend_info.get("partnerId") or home.get("partner_id"),
                        "wealth_no": extend_info.get("wealthNo"),
                        "continued_days": extend_info.get("continuedData") or home.get("continued_days"),
                        "established_days": extend_info.get("estabed"),
                        "logo_url": quote.get("LOGO_URL") or extend_info.get("logoUrl"),
                        "fortune_name": cfh_info.get("fortuneName"),
                        "fortune_company_id": cfh_info.get("companyId"),
                        "sale_date": parse_ymd(quote.get("SALE_DATE")),
                        "sale_end_date": parse_ymd(quote.get("SALE_END_DATE")),
                        "quote_run_days": quote.get("RUN_DATE"),
                        "quote_ann_return_since_inception": to_float(quote.get("ANNSYL_LN")),
                        "home_latest_year_profit": home.get("latest_year_profit"),
                        "home_annual_rate": home.get("annual_rate"),
                        "home_draw_down": home.get("draw_down"),
                        "home_hold_limit": home.get("hold_limit"),
                        "home_hold_limit_2": home.get("hold_limit_2"),
                        "home_key_title": home.get("key_title"),
                        "home_key_title_2": home.get("key_title_2"),
                        "server_time": extend_info.get("serverTime"),
                        "subtitle_param": extend_info.get("subtitleParam"),
                        "source_home_snapshot_ids": home.get("source_snapshot_ids") or [],
                    },
                }
            )

            daily_row = self.build_daily_row(strategy_id, quote_meta, detail_meta)
            if daily_row:
                performance_daily.append(daily_row)

            performance_interval.extend(
                self.build_interval_rows(strategy_id, quote_meta, detail_meta)
            )

            if detail:
                position_date = parse_date_from_prefixed_text(holding_info.get("date"))
                snapshot_id = f"{CHANNEL_ID}-{strategy_id}-holding-{position_date or self.day}-{self.run_id}"
                holding_year = int(position_date[:4]) if position_date else self.run_at.year
                for group in holding_info.get("holdTypeList") or []:
                    asset_type = fund_group_label(group)
                    group_rate = to_float(group.get("rate"))
                    for fund in group.get("fundList") or []:
                        fund_code = str(fund.get("fundCode") or "").strip()
                        fund_name = str(fund.get("fundName") or "").strip()
                        nav_date = parse_mmdd(fund.get("date"), year=holding_year)
                        row = {
                            "snapshot_id": snapshot_id,
                            "channel_id": CHANNEL_ID,
                            "source_strategy_id": strategy_id,
                            "position_date": position_date,
                            "disclosure_date": position_date,
                            "fund_code": fund_code,
                            "fund_name": fund_name,
                            "fund_asset_type": asset_type,
                            "fund_group_name": asset_type,
                            "fund_weight": to_float(fund.get("rate")),
                            "fund_nav": to_float(fund.get("netAssetValue")),
                            "fund_nav_date": nav_date,
                            "is_precise_weight": fund.get("rate") not in (None, "", "--"),
                            "is_login_required": True,
                            "source_url": source_url,
                            "raw_record_hash": hashlib.sha256(compact_json(fund).encode("utf-8")).hexdigest(),
                            "confidence_level": "snapshot_exact",
                            "access_level": "login",
                            "run_id": self.run_id,
                            "source_snapshot_id": detail_meta.get("source_snapshot_id"),
                            "operation_type": fund.get("operationType"),
                            "latest_fund_daily_rate": to_float(fund.get("increaseRate")),
                            "group_weight": group_rate,
                        }
                        fund_snapshot.append(row)
                        if fund_code:
                            fund_public_dim[fund_code] = {
                                "fund_code": fund_code,
                                "fund_name": fund_name,
                                "fund_company": None,
                                "fund_type": asset_type,
                                "tracking_index": None,
                                "theme_tags": None,
                                "latest_nav": to_float(fund.get("netAssetValue")),
                                "latest_nav_date": nav_date,
                                "status": None,
                                "source": "ttfund_loggedin_cache",
                                "updated_at": self.captured_at,
                                "run_id": self.run_id,
                            }

            strategy_name = (
                extend_info.get("tgName")
                or quote.get("TGNAME")
                or home.get("strategy_name")
                or strategy_id
            )
            reference_date = (
                parse_ymd(extend_info.get("serverTime"))
                or parse_ymd(quote.get("JZRQ") or quote.get("SYRQ"))
                or self.day
            )
            event_rows, delta_rows = self.build_adjustment_rows(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                source_url=source_url,
                reference_date=reference_date,
                adjustment_meta=adjustment_meta,
            )
            rebalance_events.extend(event_rows)
            rebalance_deltas.extend(delta_rows)

            if adjustment:
                strategy_name = (
                    extend_info.get("tgName")
                    or quote.get("TGNAME")
                    or home.get("strategy_name")
                    or strategy_id
                )
                reference_date = (
                    parse_ymd(extend_info.get("serverTime"))
                    or parse_ymd(quote.get("JZRQ") or quote.get("SYRQ"))
                    or self.day
                )
                rebalance_date = parse_mmdd_with_reference(adjustment.get("dateStr"), reference_date)
                event_hash = hashlib.sha256(compact_json(adjustment).encode("utf-8")).hexdigest()[:16]
                event_id = f"{CHANNEL_ID}-{strategy_id}-{rebalance_date or 'unknown'}-{event_hash}"
                rebalance_events.append(
                    {
                        "rebalance_event_id": event_id,
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": strategy_id,
                        "rebalance_date": rebalance_date,
                        "previous_position_date": None,
                        "new_position_date": rebalance_date,
                        "disclosure_date": rebalance_date,
                        "event_title": f"{strategy_name} 调仓",
                        "event_reason": adjustment.get("reason"),
                        "source_url": source_url,
                        "source_snapshot_id": adjustment_meta.get("source_snapshot_id"),
                        "confidence_level": "official_exact",
                        "run_id": self.run_id,
                    }
                )
                for group in adjustment.get("adjustList") or []:
                    group_name = fund_group_label(group)
                    group_before = to_float(group.get("preRate"))
                    group_after = to_float(group.get("afterRate"))
                    for fund in group.get("changeList") or []:
                        fund_code = str(fund.get("fundCode") or "").strip()
                        fund_name = str(fund.get("fundName") or "").strip() or None
                        before_weight = to_float(fund.get("preRate"))
                        after_weight = to_float(fund.get("afterRate"))
                        weight_delta = None
                        if before_weight is not None or after_weight is not None:
                            weight_delta = (after_weight or 0.0) - (before_weight or 0.0)
                        if before_weight in (None, 0.0) and (after_weight or 0.0) > 0:
                            action_type = "buy"
                        elif (before_weight or 0.0) > 0 and after_weight in (None, 0.0):
                            action_type = "sell"
                        elif before_weight is not None and after_weight is not None and after_weight > before_weight:
                            action_type = "increase"
                        elif before_weight is not None and after_weight is not None and after_weight < before_weight:
                            action_type = "decrease"
                        else:
                            action_type = "keep"
                        rebalance_deltas.append(
                            {
                                "rebalance_event_id": event_id,
                                "fund_code": fund_code,
                                "fund_name": fund_name,
                                "before_weight": before_weight,
                                "after_weight": after_weight,
                                "weight_delta": weight_delta,
                                "action_type": action_type,
                                "run_id": self.run_id,
                                "fund_type_code": None,
                                "fund_group_name": group_name,
                                "fund_group_weight_before": group_before,
                                "fund_group_weight_after": group_after,
                                "source_snapshot_id": adjustment_meta.get("source_snapshot_id"),
                                "operation_int": fund.get("operationInt"),
                            }
                        )

        return {
            "strategy_master": strategy_master,
            "strategy_performance_daily": performance_daily,
            "strategy_performance_interval": performance_interval,
            "strategy_fund_snapshot": fund_snapshot,
            "strategy_rebalance_event": rebalance_events,
            "strategy_rebalance_fund_delta": rebalance_deltas,
            "fund_public_dim": list(fund_public_dim.values()),
        }

    def build_strategy_description(self, detail_strategy: dict[str, Any]) -> str | None:
        parts = [
            str(detail_strategy.get("strategyConcept1") or "").strip(),
            str(detail_strategy.get("strategyConcept2") or "").strip(),
            str(detail_strategy.get("strategyConcept3") or "").strip(),
        ]
        values = [part for part in parts if part]
        return "\n".join(values) if values else None

    def build_strategy_status(
        self,
        extend_info: dict[str, Any],
        quote: dict[str, Any],
        home: dict[str, Any],
    ) -> str | None:
        if extend_info.get("isStop") is True:
            return "stopped"
        if quote.get("SALE_DATE") or quote.get("SALE_END_DATE"):
            return "on_sale_window"
        if quote.get("RUN_STATUS"):
            return str(quote.get("RUN_STATUS"))
        if home.get("skip_url"):
            return "listed"
        return None

    def build_daily_row(
        self,
        strategy_id: str,
        quote_meta: dict[str, Any],
        detail_meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        quote = quote_meta.get("row") or {}
        # JZRQ is the performance/net-value date. SYRQ can lag it by one day.
        trade_date = parse_ymd(quote.get("JZRQ") or quote.get("SYRQ"))
        daily_return = to_float(quote.get("SYL_D"))
        cumulative_return = to_float(quote.get("SYL_LN"))
        source_snapshot_id = quote_meta.get("source_snapshot_id")
        if trade_date and (daily_return is not None or cumulative_return is not None):
            return {
                "channel_id": CHANNEL_ID,
                "source_strategy_id": strategy_id,
                "trade_date": trade_date,
                "nav": round(1.0 + cumulative_return / 100.0, 8) if cumulative_return is not None else None,
                "daily_return": daily_return,
                "cumulative_return": cumulative_return,
                "benchmark_return": None,
                "index_return": None,
                "max_drawdown": None,
                "source_snapshot_id": source_snapshot_id,
                "run_id": self.run_id,
                "source_type": "public_quote",
            }

        detail = detail_meta.get("payload") or {}
        extend_info = detail.get("tgExtendInfo") or {}
        subtitle = extend_info.get("subtitleParam") or {}
        server_time = parse_ymd(extend_info.get("serverTime"))
        year = int(server_time[:4]) if server_time else self.run_at.year
        detail_trade_date = parse_mmdd(subtitle.get("title2"), year=year)
        detail_daily_return = to_float(subtitle.get("num2"))
        detail_cumulative_return = to_float(subtitle.get("num1"))
        if detail_trade_date and (detail_daily_return is not None or detail_cumulative_return is not None):
            return {
                "channel_id": CHANNEL_ID,
                "source_strategy_id": strategy_id,
                "trade_date": detail_trade_date,
                "nav": round(1.0 + detail_cumulative_return / 100.0, 8)
                if detail_cumulative_return is not None
                else None,
                "daily_return": detail_daily_return,
                "cumulative_return": detail_cumulative_return,
                "benchmark_return": None,
                "index_return": None,
                "max_drawdown": None,
                "source_snapshot_id": detail_meta.get("source_snapshot_id"),
                "run_id": self.run_id,
                "source_type": "detail_cache",
            }
        return None

    def build_interval_rows(
        self,
        strategy_id: str,
        quote_meta: dict[str, Any],
        detail_meta: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        quote = quote_meta.get("row") or {}
        detail = detail_meta.get("payload") or {}
        extend_info = detail.get("tgExtendInfo") or {}
        benchmark_map = self.build_detail_stage_map(extend_info.get("stageListAll") or [])
        as_of_date = parse_ymd(quote.get("JZRQ") or quote.get("SYRQ"))
        if as_of_date is None:
            server_time = parse_ymd(extend_info.get("serverTime"))
            as_of_date = server_time
        source_snapshot_id = quote_meta.get("source_snapshot_id") or detail_meta.get("source_snapshot_id")

        for interval_code, interval_label, quote_field in QUOTE_INTERVAL_FIELDS:
            strategy_return = to_float(quote.get(quote_field))
            benchmark_return = benchmark_map.get(interval_label, {}).get("benchmark_return")
            if strategy_return is None and benchmark_return is None:
                stage_info = benchmark_map.get(interval_label) or {}
                strategy_return = stage_info.get("strategy_return")
                benchmark_return = stage_info.get("benchmark_return")
            if strategy_return is None and benchmark_return is None:
                continue
            rows.append(
                {
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": strategy_id,
                    "interval_code": interval_code,
                    "interval_label": interval_label,
                    "return_value": strategy_return,
                    "benchmark_return": benchmark_return,
                    "as_of_date": as_of_date,
                    "source_snapshot_id": source_snapshot_id,
                    "run_id": self.run_id,
                }
            )
        return rows

    def build_detail_stage_map(self, rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
        result: dict[str, dict[str, float | None]] = {}
        for row in rows:
            label = str(row.get("period") or "").strip()
            if not label:
                continue
            result[label] = {
                "strategy_return": to_float(row.get("rate")),
                "benchmark_return": to_float(row.get("basic")),
            }
        return result

    def build_adjustment_rows(
        self,
        *,
        strategy_id: str,
        strategy_name: str,
        source_url: str,
        reference_date: str,
        adjustment_meta: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        history_payload = adjustment_meta.get("history_payload")
        if isinstance(history_payload, dict):
            return self.build_adjustment_history_rows(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                source_url=source_url,
                reference_date=reference_date,
                payload=history_payload,
                source_snapshot_id=adjustment_meta.get("history_source_snapshot_id"),
            )

        latest_payload = adjustment_meta.get("latest_payload")
        if isinstance(latest_payload, dict):
            return self.build_adjustment_single_event_rows(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                source_url=source_url,
                reference_date=reference_date,
                event_payload=latest_payload,
                groups=latest_payload.get("adjustList") or latest_payload.get("arr") or [],
                source_snapshot_id=adjustment_meta.get("latest_source_snapshot_id"),
                payload_type="latest",
                event_sequence=1,
            )

        return [], []

    def build_adjustment_history_rows(
        self,
        *,
        strategy_id: str,
        strategy_name: str,
        source_url: str,
        reference_date: str,
        payload: dict[str, Any],
        source_snapshot_id: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        events: list[dict[str, Any]] = []
        deltas: list[dict[str, Any]] = []
        for event_sequence, event_payload in enumerate(payload.get("adjustList") or [], start=1):
            if not isinstance(event_payload, dict):
                continue
            event_rows, delta_rows = self.build_adjustment_single_event_rows(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                source_url=source_url,
                reference_date=reference_date,
                event_payload=event_payload,
                groups=event_payload.get("arr") or event_payload.get("adjustList") or [],
                source_snapshot_id=source_snapshot_id,
                payload_type="history",
                event_sequence=event_sequence,
            )
            events.extend(event_rows)
            deltas.extend(delta_rows)
        return events, deltas

    def build_adjustment_single_event_rows(
        self,
        *,
        strategy_id: str,
        strategy_name: str,
        source_url: str,
        reference_date: str,
        event_payload: dict[str, Any],
        groups: list[dict[str, Any]],
        source_snapshot_id: str | None,
        payload_type: str,
        event_sequence: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rebalance_date = parse_ymd(event_payload.get("dateStr")) or parse_mmdd_with_reference(
            event_payload.get("dateStr"),
            reference_date,
        )
        event_hash = hashlib.sha256(compact_json(event_payload).encode("utf-8")).hexdigest()[:16]
        event_id = f"{CHANNEL_ID}-{strategy_id}-{rebalance_date or 'unknown'}-{event_hash}"
        event_rows = [
            {
                "rebalance_event_id": event_id,
                "channel_id": CHANNEL_ID,
                "source_strategy_id": strategy_id,
                "rebalance_date": rebalance_date,
                "previous_position_date": None,
                "new_position_date": rebalance_date,
                "disclosure_date": rebalance_date,
                "event_title": f"{strategy_name} 调仓",
                "event_reason": event_payload.get("reason"),
                "source_url": source_url,
                "source_snapshot_id": source_snapshot_id,
                "confidence_level": "official_exact",
                "run_id": self.run_id,
                "event_time": str(event_payload.get("timeStr") or "").strip() or None,
                "event_sequence": event_sequence,
                "payload_type": payload_type,
            }
        ]

        delta_rows: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_name = fund_group_label(group)
            group_before = to_float(group.get("preRate"))
            group_after = to_float(group.get("afterRate"))
            for fund in group.get("changeList") or []:
                if not isinstance(fund, dict):
                    continue
                fund_code = str(fund.get("fundCode") or "").strip()
                fund_name = str(fund.get("fundName") or "").strip() or None
                before_weight = to_float(fund.get("preRate"))
                after_weight = to_float(fund.get("afterRate"))
                weight_delta = None
                if before_weight is not None or after_weight is not None:
                    weight_delta = (after_weight or 0.0) - (before_weight or 0.0)
                if before_weight in (None, 0.0) and (after_weight or 0.0) > 0:
                    action_type = "buy"
                elif (before_weight or 0.0) > 0 and after_weight in (None, 0.0):
                    action_type = "sell"
                elif before_weight is not None and after_weight is not None and after_weight > before_weight:
                    action_type = "increase"
                elif before_weight is not None and after_weight is not None and after_weight < before_weight:
                    action_type = "decrease"
                else:
                    action_type = "keep"
                delta_rows.append(
                    {
                        "rebalance_event_id": event_id,
                        "fund_code": fund_code,
                        "fund_name": fund_name,
                        "before_weight": before_weight,
                        "after_weight": after_weight,
                        "weight_delta": weight_delta,
                        "action_type": action_type,
                        "run_id": self.run_id,
                        "fund_type_code": None,
                        "fund_group_name": group_name,
                        "fund_group_weight_before": group_before,
                        "fund_group_weight_after": group_after,
                        "source_snapshot_id": source_snapshot_id,
                        "operation_int": fund.get("operationInt"),
                        "payload_type": payload_type,
                        "event_sequence": event_sequence,
                    }
                )

        return event_rows, delta_rows

    def write_normalized(self, normalized: dict[str, list[dict[str, Any]]]) -> None:
        for entity, rows in normalized.items():
            output_path = self.normalized_base_dir / entity / self.day / f"{self.run_id}.jsonl"
            write_jsonl(output_path, rows)

    def write_run_manifest(self, summary: dict[str, Any]) -> None:
        manifest = {
            "summary": summary,
            "raw_snapshots": self.raw_snapshots,
        }
        (self.raw_base_dir / "_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary_dir = self.normalized_base_dir / "collection_summary" / self.day
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / f"{self.run_id}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def build_summary(
        self,
        strategy_ids: list[str],
        home_strategies: dict[str, dict[str, Any]],
        detail_payloads: dict[str, dict[str, Any]],
        adjustment_payloads: dict[str, dict[str, Any]],
        quotes_by_strategy: dict[str, dict[str, Any]],
        normalized: dict[str, list[dict[str, Any]]],
        cache_root: Path,
        cache_source: str,
    ) -> dict[str, Any]:
        interval_with_benchmark = sum(
            1 for row in normalized["strategy_performance_interval"] if row.get("benchmark_return") is not None
        )
        holdings_by_strategy = {
            row["source_strategy_id"] for row in normalized["strategy_fund_snapshot"] if row.get("source_strategy_id")
        }
        history_adjustment_strategies = sum(
            1 for payload in adjustment_payloads.values() if payload.get("history_payload") is not None
        )
        return {
            "channel_id": CHANNEL_ID,
            "channel_name": CHANNEL_NAME,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "raw_dir": str(self.raw_base_dir),
            "normalized_dir": str(self.normalized_base_dir),
            "cache_root": str(cache_root),
            "cache_source": cache_source,
            "cache_copy_disappeared_total": self.cache_copy_disappeared_total,
            "device_id": self.device_id,
            "strategy_total": len(strategy_ids),
            "home_strategy_total": len(home_strategies),
            "quote_strategy_total": len(quotes_by_strategy),
            "detail_cache_strategy_total": len(detail_payloads),
            "adjustment_cache_strategy_total": len(adjustment_payloads),
            "adjustment_history_cache_strategy_total": history_adjustment_strategies,
            "detail_cache_with_holdings_total": len(holdings_by_strategy),
            "strategy_master_rows": len(normalized["strategy_master"]),
            "daily_rows_total": len(normalized["strategy_performance_daily"]),
            "interval_rows_total": len(normalized["strategy_performance_interval"]),
            "interval_with_benchmark_rows": interval_with_benchmark,
            "holding_rows_total": len(normalized["strategy_fund_snapshot"]),
            "fund_public_dim_total": len(normalized["fund_public_dim"]),
            "rebalance_event_total": len(normalized["strategy_rebalance_event"]),
            "rebalance_delta_total": len(normalized["strategy_rebalance_fund_delta"]),
            "raw_snapshot_total": len(self.raw_snapshots),
        }


def collect_ttfund_loggedin(
    project_root: Path,
    *,
    device_id: str | None = None,
    sync_device_cache: bool = True,
    input_cache_dir: Path | None = None,
    strategy_ids: list[str] | None = None,
    limit: int | None = None,
    quote_batch_size: int = 200,
    fetch_public_quote: bool = True,
    adb_path: str | Path = "adb",
    run_id: str | None = None,
) -> dict[str, Any]:
    collector = TTFundLoggedInCollector(
        project_root,
        device_id=device_id,
        sync_device_cache=sync_device_cache,
        input_cache_dir=input_cache_dir,
        strategy_ids=strategy_ids,
        limit=limit,
        quote_batch_size=quote_batch_size,
        fetch_public_quote=fetch_public_quote,
        adb_path=adb_path,
        run_id=run_id,
    )
    return collector.collect()
