from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from advisor_monitor.models import RawSnapshot
from advisor_monitor.storage import write_jsonl


CHANNEL_ID = "antfortune"
CHANNEL_NAME = "蚂蚁财富"
DEFAULT_SOURCE_STRATEGY_ID = "antfortune_unknown"
DEFAULT_STRATEGY_NAME = "蚂蚁财富投顾组合"
ACCESS_LEVEL = "login_capture"


@dataclass(frozen=True)
class RawRpcPayload:
    path: Path
    operation_type: str | None
    params: dict[str, Any]
    response: Any
    result: Any
    snapshot: dict[str, Any]


@dataclass(frozen=True)
class HoldingChange:
    fund_code: str | None
    fund_name: str | None
    before_weight: float | None
    after_weight: float | None
    fund_asset_type: str | None
    fund_group_name: str | None
    internal_product_id: str | None
    product_status: str | None
    raw: dict[str, Any]


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(compact_json(value).encode("utf-8")).hexdigest()


def safe_name(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", value)[:80]


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def as_dict(value: Any) -> dict[str, Any]:
    value = parse_jsonish(value)
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    value = parse_jsonish(value)
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def first_present(mapping: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", "--"):
            return value
    return None


def parse_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isnan(float(value)):
            return None
        number = int(value)
        if number > 10_000_000_000:
            number = number // 1000
        if number > 1_000_000_000:
            return datetime.fromtimestamp(number, tz=timezone.utc).date().isoformat()
        text = str(int(value))
    else:
        text = str(value).strip()

    if not text or text in {"--", "-", "null", "None"}:
        return None

    match = re.search(r"(?<!\d)(20\d{2}|19\d{2})(\d{2})(\d{2})(?!\d)", text)
    if match:
        return normalize_ymd(match.group(1), match.group(2), match.group(3))

    match = re.search(
        r"(20\d{2}|19\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})",
        text,
    )
    if match:
        return normalize_ymd(match.group(1), match.group(2), match.group(3))

    return None


def normalize_ymd(year: str, month: str, day: str) -> str | None:
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return None


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip()
    if not text or text in {"--", "-", "null", "None"}:
        return None
    text = (
        text.replace(",", "")
        .replace("％", "%")
        .replace("+", "")
        .replace("约", "")
        .replace("左右", "")
        .strip()
    )
    if text.endswith("%"):
        text = text[:-1].strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def read_weight(
    item: dict[str, Any],
    percent_keys: list[str],
    ratio_keys: list[str],
) -> float | None:
    for key in percent_keys:
        value = item.get(key)
        if value not in (None, "", "--"):
            return to_float(value)
    for key in ratio_keys:
        value = item.get(key)
        if value in (None, "", "--"):
            continue
        parsed = to_float(value)
        if parsed is None:
            continue
        if isinstance(value, (int, float)) and abs(parsed) <= 1:
            return parsed * 100
        text = str(value)
        if "%" not in text and abs(parsed) <= 1:
            return parsed * 100
        return parsed
    return None


def classify_action(before_weight: float | None, after_weight: float | None) -> str:
    before = before_weight if before_weight is not None else 0.0
    after = after_weight if after_weight is not None else 0.0
    if before == 0 and after > 0:
        return "buy"
    if before > 0 and after == 0:
        return "sell"
    if after > before:
        return "increase"
    if after < before:
        return "decrease"
    if before == after:
        return "keep"
    return "unknown"


def walk_dicts(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        node = parse_jsonish(node)
        if isinstance(node, dict):
            result.append(node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return result


def find_deep_string(value: Any, keys: list[str]) -> str | None:
    for node in walk_dicts(value):
        for key in keys:
            candidate = node.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def infer_operation_type(path: Path, payload: Any) -> str | None:
    operation = find_deep_string(
        payload,
        ["operationType", "operation_type", "api", "method", "rpcOperationType"],
    )
    if operation:
        return operation

    text = str(path).replace("\\", "/").lower()
    if "queryadjustmentdetail" in text or "advisor.transaction.queryadjustmentdetail" in text:
        return "com.alipay.wealthbffweb.advisor.transaction.queryAdjustmentDetail"
    if "convertrecord.querydetail" in text or "convert_record_detail" in text:
        return "com.alipay.wealthbffweb.fund.portfolio.convertRecord.queryDetail"
    if "adjustmentdetail" in text:
        return "com.alipay.ficcbffweb.marcopolo.adjustmentDetail.query"
    if "quarterlyreport" in text or "quarterly_report" in text:
        return "com.alipay.ficcbffweb.marcopolo.quarterlyReport.query"
    if "adjustment" in text:
        return "com.alipay.ficcbffweb.marcopolo.adjustment.query"
    return None


def extract_params(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in [
        "params",
        "requestParams",
        "requestData",
        "request",
        "args",
        "body",
        "param",
    ]:
        candidate = parse_jsonish(payload.get(key))
        if isinstance(candidate, dict):
            if "operationType" in candidate and "requestData" in candidate:
                nested = parse_jsonish(candidate.get("requestData"))
                if isinstance(nested, dict):
                    return nested
            return candidate
        if isinstance(candidate, list) and candidate and isinstance(candidate[0], dict):
            return candidate[0]
    return {}


def unwrap_response(payload: Any) -> tuple[Any, Any]:
    if not isinstance(payload, dict):
        parsed = parse_jsonish(payload)
        return parsed, parsed

    response = payload
    for key in ["response", "rpcResponse", "rawResponse", "resultData", "res"]:
        candidate = parse_jsonish(payload.get(key))
        if isinstance(candidate, (dict, list)):
            response = candidate
            break

    response = parse_jsonish(response)
    result = response
    if isinstance(response, dict):
        for key in ["result", "data"]:
            candidate = parse_jsonish(response.get(key))
            if isinstance(candidate, (dict, list)):
                result = candidate
                break
    return response, result


def strategy_id_from_payload(
    rpc: RawRpcPayload,
    default_source_strategy_id: str,
) -> str:
    for source in [rpc.params, as_dict(rpc.result), as_dict(rpc.response)]:
        value = first_present(
            source,
            [
                "portfolioProductId",
                "advisorProductId",
                "advisorMainOrderId",
                "portfolioCode",
                "portfolioId",
                "targetPortfolioId",
                "prePortfolioId",
                "assetId",
                "productId",
            ],
        )
        if value is not None:
            return str(value)
    return default_source_strategy_id


def strategy_name_from_payload(rpc: RawRpcPayload, default_strategy_name: str) -> str:
    for source in [as_dict(rpc.result), as_dict(rpc.response), rpc.params]:
        value = first_present(
            source,
            [
                "portfolioName",
                "portfolioProductName",
                "advisorProductName",
                "prePortfolioName",
                "strategyName",
            ],
        )
        if value:
            return str(value)
    return default_strategy_name


def resolve_fund_identifier(change: HoldingChange) -> tuple[str, str]:
    if change.fund_code:
        return str(change.fund_code), "exact"
    if change.internal_product_id:
        return f"product:{change.internal_product_id}", "product_id_surrogate"
    if change.fund_name:
        return f"name:{stable_hash(change.fund_name)[:16]}", "name_hash_surrogate"
    return f"unknown:{stable_hash(change.raw)[:16]}", "missing_surrogate"


def combine_date_from_parts(mapping: dict[str, Any]) -> str | None:
    year = first_present(mapping, ["year", "yyyy"])
    month = first_present(mapping, ["month", "mm"])
    day = first_present(mapping, ["day", "dd"])
    if year and month and day:
        return normalize_ymd(str(year), str(month), str(day))
    return None


def first_deep_date(value: Any, preferred_keys: list[str]) -> str | None:
    for node in walk_dicts(value):
        for key in preferred_keys:
            parsed = parse_date(node.get(key))
            if parsed:
                return parsed
        combined = combine_date_from_parts(node)
        if combined:
            return combined
    return None


class AntfortuneRawCollector:
    def __init__(
        self,
        project_root: Path,
        *,
        raw_dir: Path | None = None,
        source_strategy_id: str = DEFAULT_SOURCE_STRATEGY_ID,
        strategy_name: str = DEFAULT_STRATEGY_NAME,
        allow_empty: bool = False,
    ) -> None:
        self.project_root = project_root
        self.raw_dir = raw_dir or project_root / "data" / "raw" / CHANNEL_ID / "app_rpc"
        if not self.raw_dir.is_absolute():
            self.raw_dir = project_root / self.raw_dir
        self.default_source_strategy_id = source_strategy_id
        self.default_strategy_name = strategy_name
        self.allow_empty = allow_empty
        self.run_at = now_local()
        self.day = self.run_at.strftime("%Y-%m-%d")
        self.run_id = self.run_at.strftime("%Y%m%dT%H%M%S%z")
        self.captured_at = self.run_at.isoformat(timespec="seconds")
        self.normalized_base_dir = project_root / "data" / "normalized" / CHANNEL_ID
        self.raw_snapshots: list[dict[str, Any]] = []

    def collect(self) -> dict[str, Any]:
        files = sorted(self.raw_dir.rglob("*.json")) if self.raw_dir.exists() else []
        if not files and not self.allow_empty:
            raise FileNotFoundError(
                f"no raw JSON files found under {self.raw_dir}; "
                "put authorized RPC captures there or pass --allow-empty"
            )

        normalized: dict[str, list[dict[str, Any]]] = {
            "strategy_master": [],
            "strategy_rebalance_event": [],
            "strategy_rebalance_fund_delta": [],
            "strategy_fund_snapshot": [],
        }
        strategy_rows: dict[str, dict[str, Any]] = {}
        operation_counts: dict[str, int] = {}
        parsed_files = 0
        unsupported_files = 0

        for path in files:
            rpc = self.load_rpc_payload(path)
            if rpc is None:
                unsupported_files += 1
                continue
            operation = rpc.operation_type or "unknown"
            operation_counts[operation] = operation_counts.get(operation, 0) + 1
            strategy_id = strategy_id_from_payload(rpc, self.default_source_strategy_id)
            strategy_name = strategy_name_from_payload(rpc, self.default_strategy_name)

            before_counts = {key: len(rows) for key, rows in normalized.items()}
            self.normalize_rpc_payload(rpc, strategy_id, strategy_name, normalized)
            after_counts = {key: len(rows) for key, rows in normalized.items()}
            if after_counts != before_counts:
                parsed_files += 1
                strategy_rows[strategy_id] = self.make_strategy_row(strategy_id, strategy_name, rpc)
            else:
                unsupported_files += 1

        normalized["strategy_master"] = list(strategy_rows.values())
        self.dedupe_rows(normalized)
        self.write_normalized(normalized)
        summary = self.build_summary(files, parsed_files, unsupported_files, operation_counts, normalized)
        self.write_run_manifest(summary)
        return summary

    def load_rpc_payload(self, path: Path) -> RawRpcPayload | None:
        raw_bytes = path.read_bytes()
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        snapshot = RawSnapshot(
            snapshot_id=f"{CHANNEL_ID}-raw-rpc-{content_hash[:16]}",
            channel_id=CHANNEL_ID,
            collector_name="app_rpc_capture",
            access_level=ACCESS_LEVEL,
            captured_at=self.captured_at,
            source_url=str(path),
            http_status=None,
            raw_path=str(path),
            content_type="application/json",
            content_hash=content_hash,
            parse_status="pending",
        ).to_dict()

        try:
            payload = json.loads(raw_bytes.decode("utf-8-sig"))
        except json.JSONDecodeError:
            snapshot["parse_status"] = "failed"
            self.raw_snapshots.append(snapshot)
            return None

        operation_type = infer_operation_type(path, payload)
        params = extract_params(payload)
        response, result = unwrap_response(payload)
        snapshot["parse_status"] = "success"
        snapshot["source_url"] = operation_type or str(path)
        self.raw_snapshots.append(snapshot)
        return RawRpcPayload(
            path=path,
            operation_type=operation_type,
            params=params,
            response=response,
            result=result,
            snapshot=snapshot,
        )

    def make_strategy_row(
        self,
        source_strategy_id: str,
        strategy_name: str,
        rpc: RawRpcPayload,
    ) -> dict[str, Any]:
        return {
            "channel_id": CHANNEL_ID,
            "source_strategy_id": source_strategy_id,
            "strategy_name": strategy_name,
            "advisor_name": CHANNEL_NAME,
            "strategy_type": None,
            "risk_level": None,
            "launch_date": None,
            "suggested_holding_period": None,
            "minimum_amount": None,
            "advisory_fee_rate": None,
            "benchmark": None,
            "tags": [],
            "strategy_description": None,
            "status": None,
            "source_url": rpc.operation_type or str(rpc.path),
            "first_seen_at": self.captured_at,
            "last_seen_at": self.captured_at,
            "run_id": self.run_id,
            "source_snapshot_id": rpc.snapshot["snapshot_id"],
            "access_level": ACCESS_LEVEL,
        }

    def normalize_rpc_payload(
        self,
        rpc: RawRpcPayload,
        source_strategy_id: str,
        strategy_name: str,
        normalized: dict[str, list[dict[str, Any]]],
    ) -> None:
        operation = rpc.operation_type or ""
        result = as_dict(rpc.result)
        response = as_dict(rpc.response)
        search_root: Any = rpc.result

        if (
            operation.endswith("marcopolo.adjustment.query")
            or "adjustmentOriginalPortfolio" in result
            or "adjustmentOriginalPortfolio" in response
        ):
            self.normalize_marcopolo_adjustment(rpc, source_strategy_id, strategy_name, normalized)

        if (
            operation.endswith("marcopolo.adjustmentDetail.query")
            or ("fundName" in result and ("originalRatio" in result or "ratio" in result))
        ):
            self.normalize_marcopolo_adjustment_detail(
                rpc,
                source_strategy_id,
                strategy_name,
                normalized,
            )

        if (
            "advisor.transaction.queryAdjustmentDetail" in operation
            or "adjustmentCompare" in result
            or "adjustmentCompare" in response
        ):
            self.normalize_advisor_adjustment_detail(
                rpc,
                source_strategy_id,
                strategy_name,
                normalized,
            )

        if (
            "fund.portfolio.convertRecord.queryDetail" in operation
            or (
                "changeList" in result
                and (
                    "beforeVersion" in rpc.params
                    or "afterVersion" in rpc.params
                    or "rebalanceReason" in result
                )
            )
        ):
            self.normalize_portfolio_convert_detail(
                rpc,
                source_strategy_id,
                strategy_name,
                normalized,
            )

        if (
            "quarterlyReport.query" in operation
            or any("assetConfigList" in node for node in walk_dicts(search_root))
        ):
            self.normalize_quarterly_report_snapshot(
                rpc,
                source_strategy_id,
                normalized,
            )

    def normalize_marcopolo_adjustment(
        self,
        rpc: RawRpcPayload,
        source_strategy_id: str,
        strategy_name: str,
        normalized: dict[str, list[dict[str, Any]]],
    ) -> None:
        root = as_dict(rpc.result)
        if "adjustmentOriginalPortfolio" not in root and isinstance(rpc.response, dict):
            root = as_dict(rpc.response)

        portfolio = as_list(root.get("adjustmentOriginalPortfolio"))
        changes: list[HoldingChange] = []
        for asset in portfolio:
            if not isinstance(asset, dict):
                continue
            asset_type = str(first_present(asset, ["assetType", "assetName", "productTitle"]) or "")
            asset_name = str(first_present(asset, ["assetName", "assetType", "productTitle"]) or "")
            product_list = as_list(
                first_present(asset, ["products", "productList", "fundList", "subProductList"])
            )
            for product in product_list:
                if not isinstance(product, dict):
                    continue
                changes.append(
                    HoldingChange(
                        fund_code=string_or_none(first_present(product, ["fundCode", "fundId", "code"])),
                        fund_name=string_or_none(first_present(product, ["name", "fundName", "productName"])),
                        before_weight=read_weight(
                            product,
                            ["originalRatioPercent", "beforeRatioPercent"],
                            ["originalRatio", "beforeRatio"],
                        ),
                        after_weight=read_weight(
                            product,
                            ["ratioPercent", "afterRatioPercent", "targetRatioPercent"],
                            ["ratio", "afterRatio", "targetRatio"],
                        ),
                        fund_asset_type=asset_name or asset_type or None,
                        fund_group_name=asset_type or None,
                        internal_product_id=string_or_none(first_present(product, ["productId", "fundProductId"])),
                        product_status=string_or_none(first_present(product, ["signal", "status"])),
                        raw=product,
                    )
                )

        if not changes:
            return

        rebalance_date = (
            parse_date(
                first_present(
                    root,
                    [
                        "adjustmentInfoDate",
                        "adjustingDate",
                        "adjustingDateDesc",
                        "rebalanceDate",
                        "date",
                    ],
                )
            )
            or first_deep_date(root, ["adjustmentInfoDate", "adjustingDateDesc"])
            or self.day
        )
        previous_date = parse_date(
            first_present(root, ["previousPositionDate", "lastPositionDate", "beforeDate"])
        )
        self.emit_rebalance(
            normalized,
            source_strategy_id=source_strategy_id,
            strategy_name=strategy_name,
            event_kind="marcopolo_adjustment",
            event_key=string_or_none(first_present(rpc.params, ["clientOrderId"]))
            or string_or_none(first_present(root, ["clientOrderId", "adjustmentOrderId"])),
            rebalance_date=rebalance_date,
            previous_position_date=previous_date,
            title=string_or_none(first_present(root, ["headerTitle", "adjustmentStatusDesc"]))
            or f"{strategy_name} 调仓",
            reason=string_or_none(first_present(root, ["adjustDetailDesc", "headerDesc", "adjustmentReason"])),
            changes=changes,
            rpc=rpc,
            event_extra={
                "adjustment_status": root.get("adjustmentStatus"),
                "adjustment_status_desc": root.get("adjustmentStatusDesc"),
                "adjustment_order_id": root.get("adjustmentOrderId"),
                "expected_finish_date_desc": root.get("expectedFinishDateDesc"),
            },
        )

    def normalize_marcopolo_adjustment_detail(
        self,
        rpc: RawRpcPayload,
        source_strategy_id: str,
        strategy_name: str,
        normalized: dict[str, list[dict[str, Any]]],
    ) -> None:
        root = as_dict(rpc.result)
        if not root:
            return
        before_weight = read_weight(root, ["originalRatioPercent"], ["originalRatio"])
        after_weight = read_weight(root, ["ratioPercent"], ["ratio"])
        if before_weight is None and after_weight is None:
            return

        change = HoldingChange(
            fund_code=string_or_none(first_present(rpc.params, ["fundCode"]))
            or string_or_none(first_present(root, ["fundCode", "fundId"])),
            fund_name=string_or_none(first_present(root, ["fundName", "name", "productName"])),
            before_weight=before_weight,
            after_weight=after_weight,
            fund_asset_type=None,
            fund_group_name=string_or_none(root.get("type")),
            internal_product_id=string_or_none(first_present(root, ["productId", "fundProductId"])),
            product_status=string_or_none(first_present(rpc.params, ["signal"]))
            or string_or_none(root.get("signal")),
            raw=root,
        )
        self.emit_rebalance(
            normalized,
            source_strategy_id=source_strategy_id,
            strategy_name=strategy_name,
            event_kind="marcopolo_adjustment_detail",
            event_key=string_or_none(first_present(rpc.params, ["clientOrderId"]))
            or string_or_none(first_present(root, ["clientOrderId", "adjustmentOrderId"])),
            rebalance_date=first_deep_date(root, ["adjustmentInfoDate", "rebalanceDate"]) or self.day,
            previous_position_date=None,
            title=f"{strategy_name} 单基金调仓明细",
            reason=string_or_none(first_present(root, ["fundDesc", "reason"])),
            changes=[change],
            rpc=rpc,
            event_extra={"detail_list": root.get("list"), "pay_cost_amount_desc": root.get("payCostAmountDesc")},
        )

    def normalize_advisor_adjustment_detail(
        self,
        rpc: RawRpcPayload,
        source_strategy_id: str,
        strategy_name: str,
        normalized: dict[str, list[dict[str, Any]]],
    ) -> None:
        root = as_dict(rpc.result)
        adjustment_info = as_dict(root.get("adjustmentInfo")) if "adjustmentInfo" in root else root
        compare = as_dict(adjustment_info.get("adjustmentCompare")) or as_dict(root.get("adjustmentCompare"))
        if not compare:
            compare = root
        change_list = as_list(first_present(compare, ["changeList", "fundChangeList"]))
        changes = self.changes_from_change_list(change_list)
        if not changes:
            return

        self.emit_rebalance(
            normalized,
            source_strategy_id=source_strategy_id,
            strategy_name=strategy_name,
            event_kind="advisor_adjustment",
            event_key=string_or_none(first_present(rpc.params, ["orderId", "advisorMainOrderId"]))
            or string_or_none(first_present(root, ["orderId", "tradeOrderId"])),
            rebalance_date=parse_date(compare.get("rebalanceDate"))
            or first_deep_date(root, ["rebalanceDate"])
            or self.day,
            previous_position_date=parse_date(first_present(compare, ["previousPositionDate", "beforeDate"])),
            title=f"{strategy_name} 调仓",
            reason=string_or_none(first_present(compare, ["rebalanceReason", "reason"])),
            changes=changes,
            rpc=rpc,
            event_extra={
                "before_category_ratio": compare.get("beforeCategoryRatio"),
                "after_category_ratio": compare.get("afterCategoryRatio"),
                "expected_date_info": root.get("expectedDateInfo"),
            },
        )

    def normalize_portfolio_convert_detail(
        self,
        rpc: RawRpcPayload,
        source_strategy_id: str,
        strategy_name: str,
        normalized: dict[str, list[dict[str, Any]]],
    ) -> None:
        root = as_dict(rpc.result)
        change_list = as_list(root.get("changeList"))
        changes = self.changes_from_change_list(change_list)
        if not changes:
            return

        date_from_parts = combine_date_from_parts(root)
        self.emit_rebalance(
            normalized,
            source_strategy_id=source_strategy_id,
            strategy_name=strategy_name,
            event_kind="portfolio_convert",
            event_key="-".join(
                str(rpc.params.get(key) or "")
                for key in ["portfolioCode", "beforeVersion", "afterVersion"]
                if rpc.params.get(key)
            )
            or None,
            rebalance_date=parse_date(root.get("rebalanceDate"))
            or date_from_parts
            or parse_date(rpc.params.get("afterVersion"))
            or self.day,
            previous_position_date=parse_date(rpc.params.get("beforeVersion")),
            title=f"{strategy_name} 组合调仓",
            reason=string_or_none(first_present(root, ["rebalanceReason", "reason"])),
            changes=changes,
            rpc=rpc,
            event_extra={
                "portfolio_code": rpc.params.get("portfolioCode"),
                "before_version": rpc.params.get("beforeVersion"),
                "after_version": rpc.params.get("afterVersion"),
            },
        )

    def changes_from_change_list(self, change_list: list[Any]) -> list[HoldingChange]:
        changes: list[HoldingChange] = []
        for item in change_list:
            if not isinstance(item, dict):
                continue
            before_weight = read_weight(
                item,
                ["beforeRatioPercent", "originalRatioPercent"],
                ["beforeRatio", "originalRatio"],
            )
            after_weight = read_weight(
                item,
                ["afterRatioPercent", "ratioPercent", "targetRatioPercent"],
                ["afterRatio", "ratio", "targetRatio"],
            )
            if before_weight is None and after_weight is None:
                continue
            changes.append(
                HoldingChange(
                    fund_code=string_or_none(first_present(item, ["fundCode", "fundId", "code"])),
                    fund_name=string_or_none(first_present(item, ["productName", "fundName", "name"])),
                    before_weight=before_weight,
                    after_weight=after_weight,
                    fund_asset_type=string_or_none(first_present(item, ["assetType", "assetTypeName", "category"])),
                    fund_group_name=string_or_none(first_present(item, ["categoryName", "groupName"])),
                    internal_product_id=string_or_none(first_present(item, ["productId", "fundProductId"])),
                    product_status=string_or_none(item.get("status")),
                    raw=item,
                )
            )
        return changes

    def normalize_quarterly_report_snapshot(
        self,
        rpc: RawRpcPayload,
        source_strategy_id: str,
        normalized: dict[str, list[dict[str, Any]]],
    ) -> None:
        snapshot_rows: list[dict[str, Any]] = []
        for container, context_date in self.iter_asset_config_containers(rpc.result):
            asset_config_list = as_list(container.get("assetConfigList"))
            position_date = context_date or first_deep_date(container, ["reportDate", "endDate", "date"]) or self.day
            snapshot_id = (
                f"{CHANNEL_ID}-{source_strategy_id}-quarterly-"
                f"{position_date}-{stable_hash(container)[:12]}"
            )
            for asset in asset_config_list:
                if not isinstance(asset, dict):
                    continue
                product_groups = self.product_groups_from_asset_config(asset)
                for group_name, fund_asset_type, products in product_groups:
                    for product in products:
                        if not isinstance(product, dict):
                            continue
                        fund_name = string_or_none(
                            first_present(product, ["productName", "fundName", "name"])
                        )
                        if not fund_name:
                            continue
                        change = HoldingChange(
                            fund_code=string_or_none(
                                first_present(product, ["fundCode", "fundId", "code"])
                            ),
                            fund_name=fund_name,
                            before_weight=None,
                            after_weight=read_weight(
                                product,
                                ["productRate", "rate", "ratioPercent"],
                                ["ratio", "weight"],
                            ),
                            fund_asset_type=fund_asset_type,
                            fund_group_name=group_name,
                            internal_product_id=string_or_none(
                                first_present(product, ["productId", "fundProductId"])
                            ),
                            product_status=None,
                            raw=product,
                        )
                        fund_code, resolve_status = resolve_fund_identifier(change)
                        snapshot_rows.append(
                            self.make_snapshot_row(
                                snapshot_id=snapshot_id,
                                source_strategy_id=source_strategy_id,
                                position_date=position_date,
                                disclosure_date=position_date,
                                fund_code=fund_code,
                                fund_name=fund_name,
                                fund_asset_type=fund_asset_type,
                                fund_group_name=group_name,
                                fund_weight=change.after_weight,
                                raw_record=product,
                                source_url=rpc.operation_type or str(rpc.path),
                                confidence_level="official_exact",
                                extra={
                                    "position_side": "quarterly_report",
                                    "fund_code_resolve_status": resolve_status,
                                    "internal_product_id": change.internal_product_id,
                                    "source_snapshot_id": rpc.snapshot["snapshot_id"],
                                    "operation_type": rpc.operation_type,
                                },
                            )
                        )
        normalized["strategy_fund_snapshot"].extend(snapshot_rows)

    def iter_asset_config_containers(self, value: Any) -> list[tuple[dict[str, Any], str | None]]:
        result: list[tuple[dict[str, Any], str | None]] = []

        def walk(node: Any, context_date: str | None = None) -> None:
            node = parse_jsonish(node)
            if isinstance(node, dict):
                local_date = (
                    parse_date(
                        first_present(
                            node,
                            [
                                "profitDate",
                                "reportDate",
                                "reportDateDesc",
                                "endOfDataDesc",
                                "date",
                                "oriDate",
                                "reportDateTimestamp",
                            ],
                        )
                    )
                    or combine_date_from_parts(node)
                    or context_date
                )
                if isinstance(node.get("assetConfigList"), list):
                    result.append((node, local_date))
                for key, child in node.items():
                    key_date = parse_date(key)
                    walk(child, key_date or local_date)
            elif isinstance(node, list):
                for child in node:
                    walk(child, context_date)

        walk(value)
        return result

    def product_groups_from_asset_config(
        self,
        asset: dict[str, Any],
    ) -> list[tuple[str | None, str | None, list[Any]]]:
        groups: list[tuple[str | None, str | None, list[Any]]] = []
        asset_title = string_or_none(first_present(asset, ["assetTitle", "productTitle", "title"]))
        direct_products = as_list(asset.get("productList"))
        if direct_products:
            groups.append((asset_title, asset_title, direct_products))
        for sub_asset in as_list(asset.get("subAssetList")):
            if not isinstance(sub_asset, dict):
                continue
            sub_title = string_or_none(first_present(sub_asset, ["title", "assetTitle", "productTitle"]))
            products = as_list(sub_asset.get("productList"))
            if products:
                groups.append((sub_title or asset_title, asset_title, products))
        return groups

    def emit_rebalance(
        self,
        normalized: dict[str, list[dict[str, Any]]],
        *,
        source_strategy_id: str,
        strategy_name: str,
        event_kind: str,
        event_key: str | None,
        rebalance_date: str,
        previous_position_date: str | None,
        title: str,
        reason: str | None,
        changes: list[HoldingChange],
        rpc: RawRpcPayload,
        event_extra: dict[str, Any],
    ) -> None:
        event_hash = stable_hash(
            {
                "event_kind": event_kind,
                "event_key": event_key,
                "rebalance_date": rebalance_date,
                "changes": [change.raw for change in changes],
            }
        )[:12]
        event_id = f"{CHANNEL_ID}-{source_strategy_id}-{event_kind}-{rebalance_date}-{event_hash}"
        source_url = rpc.operation_type or str(rpc.path)
        normalized["strategy_rebalance_event"].append(
            {
                "rebalance_event_id": event_id,
                "channel_id": CHANNEL_ID,
                "source_strategy_id": source_strategy_id,
                "rebalance_date": rebalance_date,
                "previous_position_date": previous_position_date,
                "new_position_date": rebalance_date,
                "disclosure_date": rebalance_date,
                "event_title": title,
                "event_reason": reason,
                "source_url": source_url,
                "source_snapshot_id": rpc.snapshot["snapshot_id"],
                "confidence_level": "official_exact",
                "run_id": self.run_id,
                "event_kind": event_kind,
                "event_key": event_key,
                "operation_type": rpc.operation_type,
                "extra": event_extra,
            }
        )

        for change in changes:
            fund_code, resolve_status = resolve_fund_identifier(change)
            fund_name = change.fund_name or fund_code
            before_weight = change.before_weight
            after_weight = change.after_weight
            normalized["strategy_rebalance_fund_delta"].append(
                {
                    "rebalance_event_id": event_id,
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "before_weight": before_weight,
                    "after_weight": after_weight,
                    "weight_delta": None
                    if before_weight is None or after_weight is None
                    else after_weight - before_weight,
                    "action_type": classify_action(before_weight, after_weight),
                    "run_id": self.run_id,
                    "fund_asset_type": change.fund_asset_type,
                    "fund_group_name": change.fund_group_name,
                    "internal_product_id": change.internal_product_id,
                    "product_status": change.product_status,
                    "fund_code_resolve_status": resolve_status,
                    "raw_record_hash": stable_hash(change.raw),
                    "source_snapshot_id": rpc.snapshot["snapshot_id"],
                    "operation_type": rpc.operation_type,
                }
            )

            if before_weight is not None:
                before_position_date = previous_position_date or rebalance_date
                normalized["strategy_fund_snapshot"].append(
                    self.make_snapshot_row(
                        snapshot_id=f"{event_id}-before",
                        source_strategy_id=source_strategy_id,
                        position_date=before_position_date,
                        disclosure_date=rebalance_date,
                        fund_code=fund_code,
                        fund_name=fund_name,
                        fund_asset_type=change.fund_asset_type,
                        fund_group_name=change.fund_group_name,
                        fund_weight=before_weight,
                        raw_record=change.raw,
                        source_url=source_url,
                        confidence_level="official_exact",
                        extra={
                            "rebalance_event_id": event_id,
                            "position_side": "before",
                            "position_date_is_event_date_fallback": previous_position_date is None,
                            "fund_code_resolve_status": resolve_status,
                            "internal_product_id": change.internal_product_id,
                            "source_snapshot_id": rpc.snapshot["snapshot_id"],
                            "operation_type": rpc.operation_type,
                        },
                    )
                )

            if after_weight is not None:
                normalized["strategy_fund_snapshot"].append(
                    self.make_snapshot_row(
                        snapshot_id=f"{event_id}-after",
                        source_strategy_id=source_strategy_id,
                        position_date=rebalance_date,
                        disclosure_date=rebalance_date,
                        fund_code=fund_code,
                        fund_name=fund_name,
                        fund_asset_type=change.fund_asset_type,
                        fund_group_name=change.fund_group_name,
                        fund_weight=after_weight,
                        raw_record=change.raw,
                        source_url=source_url,
                        confidence_level="official_exact",
                        extra={
                            "rebalance_event_id": event_id,
                            "position_side": "after",
                            "fund_code_resolve_status": resolve_status,
                            "internal_product_id": change.internal_product_id,
                            "source_snapshot_id": rpc.snapshot["snapshot_id"],
                            "operation_type": rpc.operation_type,
                        },
                    )
                )

    def make_snapshot_row(
        self,
        *,
        snapshot_id: str,
        source_strategy_id: str,
        position_date: str,
        disclosure_date: str | None,
        fund_code: str,
        fund_name: str,
        fund_asset_type: str | None,
        fund_group_name: str | None,
        fund_weight: float | None,
        raw_record: dict[str, Any],
        source_url: str | None,
        confidence_level: str,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        row = {
            "snapshot_id": snapshot_id,
            "channel_id": CHANNEL_ID,
            "source_strategy_id": source_strategy_id,
            "position_date": position_date,
            "disclosure_date": disclosure_date,
            "fund_code": fund_code,
            "fund_name": fund_name,
            "fund_asset_type": fund_asset_type,
            "fund_group_name": fund_group_name,
            "fund_weight": fund_weight,
            "fund_nav": None,
            "fund_nav_date": None,
            "is_precise_weight": fund_weight is not None,
            "is_login_required": True,
            "source_url": source_url,
            "raw_record_hash": stable_hash(raw_record),
            "confidence_level": confidence_level,
            "access_level": ACCESS_LEVEL,
            "run_id": self.run_id,
        }
        row.update(extra)
        return row

    def dedupe_rows(self, normalized: dict[str, list[dict[str, Any]]]) -> None:
        keys_by_entity = {
            "strategy_master": ["channel_id", "source_strategy_id"],
            "strategy_rebalance_event": ["rebalance_event_id"],
            "strategy_rebalance_fund_delta": ["rebalance_event_id", "fund_code"],
            "strategy_fund_snapshot": ["snapshot_id", "fund_code"],
        }
        for entity, key_fields in keys_by_entity.items():
            seen: set[tuple[Any, ...]] = set()
            deduped: list[dict[str, Any]] = []
            for row in normalized.get(entity, []):
                key = tuple(row.get(field) for field in key_fields)
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(row)
            normalized[entity] = deduped

    def write_normalized(self, normalized: dict[str, list[dict[str, Any]]]) -> None:
        for entity, rows in normalized.items():
            if not rows:
                continue
            output_path = self.normalized_base_dir / entity / self.day / f"{self.run_id}.jsonl"
            write_jsonl(output_path, rows)

    def write_run_manifest(self, summary: dict[str, Any]) -> None:
        summary_dir = self.normalized_base_dir / "collection_summary" / self.day
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / f"{self.run_id}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def build_summary(
        self,
        files: list[Path],
        parsed_files: int,
        unsupported_files: int,
        operation_counts: dict[str, int],
        normalized: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        summary = {
            "channel_id": CHANNEL_ID,
            "channel_name": CHANNEL_NAME,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "raw_dir": str(self.raw_dir),
            "normalized_dir": str(self.normalized_base_dir),
            "input_file_total": len(files),
            "parsed_file_total": parsed_files,
            "unsupported_file_total": unsupported_files,
            "strategy_total": len(normalized["strategy_master"]),
            "rebalance_event_total": len(normalized["strategy_rebalance_event"]),
            "rebalance_fund_delta_total": len(normalized["strategy_rebalance_fund_delta"]),
            "fund_snapshot_total": len(normalized["strategy_fund_snapshot"]),
            "operation_counts": operation_counts,
            "raw_snapshot_total": len(self.raw_snapshots),
            "raw_snapshots": self.raw_snapshots,
        }
        return summary


def string_or_none(value: Any) -> str | None:
    if value in (None, "", "--"):
        return None
    return str(value)


def collect_antfortune_raw(
    project_root: Path,
    *,
    raw_dir: Path | None = None,
    source_strategy_id: str = DEFAULT_SOURCE_STRATEGY_ID,
    strategy_name: str = DEFAULT_STRATEGY_NAME,
    allow_empty: bool = False,
) -> dict[str, Any]:
    collector = AntfortuneRawCollector(
        project_root,
        raw_dir=raw_dir,
        source_strategy_id=source_strategy_id,
        strategy_name=strategy_name,
        allow_empty=allow_empty,
    )
    return collector.collect()
