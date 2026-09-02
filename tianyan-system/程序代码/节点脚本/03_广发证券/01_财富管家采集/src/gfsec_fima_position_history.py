from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = "gfsec_fima_position_history_preview.v1"
CHANNEL_ID = "gfsec_fima"
CHINA_TZ = timezone(timedelta(hours=8))
RUN_STAMP_RE = re.compile(r"(?P<stamp>\d{8}T\d{6}[+-]\d{4})")
FUND_NAV_TABLE = "基金日度净值"
FUND_CODE_COLUMN = "基金代码"
TRADE_DATE_COLUMN = "交易日期"
DAILY_RETURN_COLUMN = "日收益率_百分比"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _epoch_ms_iso(value: Any) -> str | None:
    number = _as_float(value)
    if number is None or number <= 0:
        return None
    try:
        return datetime.fromtimestamp(number / 1000.0, tz=CHINA_TZ).isoformat(timespec="milliseconds")
    except (OverflowError, OSError, ValueError):
        return None


def _date_from_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _dateish_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return _epoch_ms_iso(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) >= 10:
        return _epoch_ms_iso(text)
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CHINA_TZ)
        return parsed.astimezone(CHINA_TZ).isoformat(timespec="seconds")
    except ValueError:
        try:
            return f"{date.fromisoformat(text[:10]).isoformat()}T00:00:00+08:00"
        except ValueError:
            return None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _capture_metadata(path: Path, raw_root: Path) -> tuple[str, str, str]:
    relative = path.relative_to(raw_root)
    day_text = relative.parts[0] if relative.parts else ""
    run_name = relative.parts[1] if len(relative.parts) > 1 else "unknown"
    try:
        directory_day = date.fromisoformat(day_text)
    except ValueError:
        directory_day = None
    matched = RUN_STAMP_RE.search(run_name)
    if matched:
        try:
            parsed = datetime.strptime(matched.group("stamp"), "%Y%m%dT%H%M%S%z")
            if directory_day is None or abs((parsed.date() - directory_day).days) <= 1:
                return parsed.isoformat(), "run_id_second", run_name
            captured = datetime.combine(directory_day, datetime.min.time(), tzinfo=CHINA_TZ)
            return captured.isoformat(), "directory_date_run_id_mismatch", run_name
        except ValueError:
            pass
    if directory_day is not None:
        captured = datetime.combine(directory_day, datetime.min.time(), tzinfo=CHINA_TZ)
        return captured.isoformat(), "directory_date", run_name
    captured = datetime.fromtimestamp(path.stat().st_mtime, tz=CHINA_TZ)
    return captured.isoformat(timespec="seconds"), "file_mtime_fallback", run_name


def _alternative_count(value: Any) -> int:
    if value in (None, "", [], {}):
        return 0
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 1


@dataclass(frozen=True)
class Position:
    fund_code: str
    fund_name: str
    weight_ratio: float
    asset_groups: tuple[str, ...]
    product_source: str | None
    under_currency: bool

    @property
    def weight_pct(self) -> float:
        return self.weight_ratio * 100.0


@dataclass
class Observation:
    portfolio_code: str
    strategy_name: str
    allocation_id: str | None
    captured_at: str
    capture_precision: str
    run_name: str
    model_generated_at: str | None
    effective_at: str | None
    source_path: str
    positions: dict[str, Position]
    state_hash: str
    composition_hash: str
    total_weight_pct: float
    weight_close_ok: bool
    alternative_product_count: int
    duplicate_fund_codes: tuple[str, ...]
    rebalance_evidence: str

    @property
    def sort_key(self) -> tuple[str, str, str]:
        # Observation chronology is authoritative. A reused run id or a stale/missing
        # portfolio_mix must not move a newly captured state backwards in history.
        return (self.captured_at, self.model_generated_at or self.captured_at, self.source_path)


@dataclass
class StateOccurrence:
    portfolio_code: str
    sequence: int
    state_hash: str
    composition_hash: str
    observations: list[Observation] = field(default_factory=list)

    @property
    def state_id(self) -> str:
        first = self.observations[0]
        anchor = first.model_generated_at or first.captured_at or first.source_path
        digest = _sha256_text(f"{self.portfolio_code}|{self.state_hash}|{anchor}")[:24]
        return f"gfsec_fima_state_{digest}"

    @property
    def representative(self) -> Observation:
        return self.observations[-1]

    @property
    def strategy_name(self) -> str:
        for item in reversed(self.observations):
            if item.strategy_name:
                return item.strategy_name
        return self.portfolio_code

    @property
    def first_observed_at(self) -> str:
        return min(item.captured_at for item in self.observations)

    @property
    def last_observed_at(self) -> str:
        return max(item.captured_at for item in self.observations)

    @property
    def first_generated_at(self) -> str | None:
        values = [item.model_generated_at for item in self.observations if item.model_generated_at]
        return min(values) if values else None

    @property
    def last_generated_at(self) -> str | None:
        values = [item.model_generated_at for item in self.observations if item.model_generated_at]
        return max(values) if values else None

    @property
    def effective_dates(self) -> list[str]:
        return sorted({item.effective_at for item in self.observations if item.effective_at})

    @property
    def source_paths(self) -> list[str]:
        return sorted({item.source_path for item in self.observations})

    @property
    def allocation_ids(self) -> list[str]:
        return sorted({item.allocation_id for item in self.observations if item.allocation_id})

    @property
    def rebalance_evidence(self) -> list[str]:
        return sorted({item.rebalance_evidence for item in self.observations})


@dataclass
class NavStore:
    available: bool
    reason: str | None
    rows_by_code: dict[str, list[tuple[date, float]]]


@dataclass
class AnalysisBundle:
    summary: dict[str, Any]
    state_snapshots: list[dict[str, Any]]
    position_snapshots: list[dict[str, Any]]
    transitions: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    validation: dict[str, Any]


def _load_rebalance_evidence(path: Path) -> str:
    rebalance_path = path.with_name("rebalances.json")
    if not rebalance_path.is_file():
        return "official_endpoint_snapshot_missing"
    try:
        payload = _read_json(rebalance_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "official_endpoint_snapshot_invalid"
    if isinstance(payload, dict):
        if payload.get("transport_error"):
            return "official_endpoint_transport_error"
        data = payload.get("data")
        total = _as_float(payload.get("total"))
        if isinstance(data, list) and data:
            return "official_endpoint_nonempty"
        if total is not None and total > 0:
            return "official_endpoint_nonempty"
        if isinstance(data, list) and not data and (total is None or total == 0):
            return "official_endpoint_empty"
    if payload in (None, [], {}):
        return "official_endpoint_empty"
    return "official_endpoint_unrecognized"


def _build_positions(payload: dict[str, Any]) -> tuple[dict[str, Position], int, tuple[str, ...], list[str]]:
    aggregated: dict[str, dict[str, Any]] = {}
    alternative_count = 0
    duplicate_codes: set[str] = set()
    issues: list[str] = []
    groups = payload.get("productAllocations")
    if not isinstance(groups, list):
        return {}, 0, (), ["productAllocations is not a list"]
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            issues.append(f"productAllocations[{group_index}] is not an object")
            continue
        group_name = str(group.get("assetName") or group.get("parentAssetName") or "未分类").strip()
        alternative_count += _alternative_count(group.get("alternativeProducts"))
        products = group.get("mainProducts")
        if not isinstance(products, list):
            if products not in (None, []):
                issues.append(f"productAllocations[{group_index}].mainProducts is not a list")
            continue
        for product_index, product in enumerate(products):
            if not isinstance(product, dict):
                issues.append(f"mainProducts[{group_index},{product_index}] is not an object")
                continue
            alternative_count += _alternative_count(product.get("alternativeProducts"))
            alternative_count += _alternative_count(product.get("alternativeProductsOld"))
            code = str(product.get("productCode") or "").strip()
            if not code:
                issues.append(f"mainProducts[{group_index},{product_index}] has no productCode")
                continue
            ratio = _as_float(product.get("configRatio"))
            if ratio is None:
                ratio = _as_float(product.get("shareRatio"))
            if ratio is None:
                issues.append(f"mainProducts[{group_index},{product_index}] {code} has no valid weight")
                continue
            if ratio < -1e-12 or ratio > 1.000001:
                issues.append(f"mainProducts[{group_index},{product_index}] {code} weight out of range: {ratio}")
                continue
            if code in aggregated:
                duplicate_codes.add(code)
                aggregated[code]["weight_ratio"] += ratio
                aggregated[code]["asset_groups"].add(group_name)
                continue
            aggregated[code] = {
                "fund_code": code,
                "fund_name": str(product.get("productName") or code).strip(),
                "weight_ratio": ratio,
                "asset_groups": {group_name},
                "product_source": str(product.get("productSource") or "").strip() or None,
                "under_currency": bool(product.get("underCurrency")),
            }
    positions = {
        code: Position(
            fund_code=code,
            fund_name=item["fund_name"],
            weight_ratio=item["weight_ratio"],
            asset_groups=tuple(sorted(item["asset_groups"])),
            product_source=item["product_source"],
            under_currency=item["under_currency"],
        )
        for code, item in aggregated.items()
    }
    return positions, alternative_count, tuple(sorted(duplicate_codes)), issues


def load_observations(
    raw_root: Path,
    *,
    weight_close_min_pct: float = 99.5,
    weight_close_max_pct: float = 100.5,
) -> tuple[list[Observation], list[dict[str, Any]], dict[str, int]]:
    observations: list[Observation] = []
    issues: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    if not raw_root.is_dir():
        issues.append({"issue_type": "raw_root_missing", "source_path": raw_root.as_posix(), "detail": "directory does not exist"})
        return observations, issues, dict(counters)

    for path in sorted(raw_root.rglob("current_allocation.json")):
        counters["raw_files"] += 1
        source_path = _relative_posix(path, raw_root)
        try:
            payload = _read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            counters["invalid_json_files"] += 1
            issues.append({"issue_type": "invalid_json", "source_path": source_path, "detail": str(exc)})
            continue
        if not isinstance(payload, dict) or not payload:
            counters["empty_payload_files"] += 1
            issues.append({"issue_type": "empty_payload", "source_path": source_path, "detail": "allocation payload is empty"})
            continue

        positions, alternative_count, duplicate_codes, position_issues = _build_positions(payload)
        if not positions:
            counters["empty_payload_files"] += 1
            issues.append({"issue_type": "no_main_products", "source_path": source_path, "detail": "; ".join(position_issues) or "no mainProducts"})
            continue
        for detail in position_issues:
            counters["position_parse_warnings"] += 1
            issues.append({"issue_type": "position_parse_warning", "source_path": source_path, "detail": detail})

        portfolio_code = str(payload.get("portfolioCode") or path.parent.name).strip()
        if not portfolio_code:
            counters["invalid_payload_files"] += 1
            issues.append({"issue_type": "missing_portfolio_code", "source_path": source_path, "detail": "portfolioCode is blank"})
            continue

        mix: dict[str, Any] = {}
        mix_path = path.with_name("portfolio_mix.json")
        if mix_path.is_file():
            try:
                loaded_mix = _read_json(mix_path)
                if isinstance(loaded_mix, dict):
                    mix = loaded_mix
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                counters["invalid_mix_files"] += 1
                issues.append({"issue_type": "invalid_portfolio_mix", "source_path": _relative_posix(mix_path, raw_root), "detail": str(exc)})

        captured_at, capture_precision, run_name = _capture_metadata(path, raw_root)
        generated_at = _epoch_ms_iso(mix.get("createTime"))
        effective_at = _dateish_iso(payload.get("effectiveDate"))
        strategy_name = str(
            mix.get("strategyName")
            or mix.get("portfolioName")
            or mix.get("chiName")
            or portfolio_code
        ).strip()
        vector = [(code, format(position.weight_ratio, ".10f")) for code, position in sorted(positions.items())]
        composition = sorted(positions)
        total_weight_pct = sum(item.weight_pct for item in positions.values())
        observation = Observation(
            portfolio_code=portfolio_code,
            strategy_name=strategy_name,
            allocation_id=str(payload.get("id")) if payload.get("id") is not None else None,
            captured_at=captured_at,
            capture_precision=capture_precision,
            run_name=run_name,
            model_generated_at=generated_at,
            effective_at=effective_at,
            source_path=source_path,
            positions=positions,
            state_hash=_sha256_text(_stable_json(vector)),
            composition_hash=_sha256_text(_stable_json(composition)),
            total_weight_pct=total_weight_pct,
            weight_close_ok=weight_close_min_pct <= total_weight_pct <= weight_close_max_pct,
            alternative_product_count=alternative_count,
            duplicate_fund_codes=duplicate_codes,
            rebalance_evidence=_load_rebalance_evidence(path),
        )
        observations.append(observation)
        counters["valid_observations"] += 1
        counters[f"capture_precision_{capture_precision}"] += 1
        counters["alternative_product_references_excluded"] += alternative_count
        if effective_at:
            counters["effective_date_observations"] += 1
        if not observation.weight_close_ok:
            counters["weight_closure_failures"] += 1
        if duplicate_codes:
            counters["duplicate_fund_observations"] += 1
        counters[f"rebalance_{observation.rebalance_evidence}"] += 1

    observations.sort(key=lambda item: (item.portfolio_code, *item.sort_key))
    return observations, issues, dict(counters)


def collapse_states(observations: Sequence[Observation]) -> dict[str, list[StateOccurrence]]:
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.portfolio_code].append(observation)
    result: dict[str, list[StateOccurrence]] = {}
    for portfolio_code, items in sorted(grouped.items()):
        states: list[StateOccurrence] = []
        for observation in sorted(items, key=lambda item: item.sort_key):
            if states and states[-1].state_hash == observation.state_hash:
                states[-1].observations.append(observation)
            else:
                states.append(
                    StateOccurrence(
                        portfolio_code=portfolio_code,
                        sequence=len(states) + 1,
                        state_hash=observation.state_hash,
                        composition_hash=observation.composition_hash,
                        observations=[observation],
                    )
                )
        result[portfolio_code] = states
    return result


def _all_cutoff_dates(states_by_portfolio: dict[str, list[StateOccurrence]]) -> tuple[date | None, date | None]:
    dates: list[date] = []
    for states in states_by_portfolio.values():
        for state in states:
            generated = _date_from_iso(state.first_generated_at or state.first_observed_at)
            if generated:
                dates.append(generated - timedelta(days=1))
    return (min(dates), max(dates)) if dates else (None, None)


def load_nav_store(
    db_path: Path | None,
    fund_codes: Iterable[str],
    start_date: date | None,
    end_date: date | None,
) -> NavStore:
    if db_path is None:
        return NavStore(False, "database path was not supplied", {})
    if not db_path.is_file():
        return NavStore(False, f"database does not exist: {db_path}", {})
    if start_date is None or end_date is None:
        return NavStore(False, "snapshot dates are unavailable", {})
    rows_by_code: dict[str, list[tuple[date, float]]] = defaultdict(list)
    codes = sorted({str(code).strip() for code in fund_codes if str(code).strip()})
    try:
        connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (FUND_NAV_TABLE,),
        ).fetchone()
        if not table_exists:
            connection.close()
            return NavStore(False, f"table is missing: {FUND_NAV_TABLE}", {})
        for offset in range(0, len(codes), 800):
            chunk = codes[offset : offset + 800]
            placeholders = ",".join("?" for _ in chunk)
            sql = (
                f'SELECT "{FUND_CODE_COLUMN}", "{TRADE_DATE_COLUMN}", "{DAILY_RETURN_COLUMN}" '
                f'FROM "{FUND_NAV_TABLE}" '
                f'WHERE "{FUND_CODE_COLUMN}" IN ({placeholders}) '
                f'AND "{TRADE_DATE_COLUMN}" > ? AND "{TRADE_DATE_COLUMN}" <= ? '
                f'ORDER BY "{FUND_CODE_COLUMN}", "{TRADE_DATE_COLUMN}"'
            )
            parameters: list[Any] = [*chunk, start_date.isoformat(), end_date.isoformat()]
            for code, day_text, daily_return_pct in connection.execute(sql, parameters):
                value = _as_float(daily_return_pct)
                if value is None:
                    continue
                try:
                    trade_day = date.fromisoformat(str(day_text)[:10])
                except ValueError:
                    continue
                rows_by_code[str(code)].append((trade_day, value))
        connection.close()
    except sqlite3.Error as exc:
        return NavStore(False, f"read-only NAV query failed: {exc}", {})
    return NavStore(True, None, dict(rows_by_code))


def _nav_drift(
    previous: StateOccurrence,
    current: StateOccurrence,
    nav_store: NavStore,
) -> dict[str, Any]:
    previous_positions = previous.representative.positions
    current_positions = current.representative.positions
    previous_date = _date_from_iso(previous.last_generated_at or previous.last_observed_at)
    current_date = _date_from_iso(current.first_generated_at or current.first_observed_at)
    if not nav_store.available:
        return {"status": "unavailable", "reason": nav_store.reason}
    if previous_date is None or current_date is None:
        return {"status": "unavailable", "reason": "snapshot generation date is missing"}
    start_cutoff = previous_date - timedelta(days=1)
    end_cutoff = current_date - timedelta(days=1)
    if end_cutoff < start_cutoff:
        return {"status": "unavailable", "reason": "snapshot dates are reversed"}

    factors: dict[str, float] = {}
    union_trade_dates: set[date] = set()
    covered_codes: set[str] = set()
    for code in previous_positions:
        factor = 1.0
        used = 0
        for trade_day, daily_return_pct in nav_store.rows_by_code.get(code, []):
            if start_cutoff < trade_day <= end_cutoff:
                factor *= 1.0 + daily_return_pct / 100.0
                union_trade_dates.add(trade_day)
                used += 1
        factors[code] = factor
        if used:
            covered_codes.add(code)
    if not union_trade_dates:
        covered_codes = set(previous_positions)
    coverage_ratio = len(covered_codes) / len(previous_positions) if previous_positions else 0.0
    raw_expected = {
        code: position.weight_ratio * factors.get(code, 1.0)
        for code, position in previous_positions.items()
    }
    normalizer = sum(raw_expected.values())
    if normalizer <= 0:
        return {"status": "unavailable", "reason": "no positive prior weights after return adjustment"}
    expected = {code: value / normalizer for code, value in raw_expected.items()}
    residual_pct = 0.5 * sum(
        abs(current_positions[code].weight_ratio - expected.get(code, 0.0))
        for code in current_positions
    ) * 100.0
    return {
        "status": "available" if coverage_ratio >= 0.9 else "insufficient_coverage",
        "reason": None if coverage_ratio >= 0.9 else "fewer than 90% of funds have NAV-return evidence",
        "start_cutoff_date": start_cutoff.isoformat(),
        "end_cutoff_date": end_cutoff.isoformat(),
        "trade_date_count": len(union_trade_dates),
        "covered_fund_count": len(covered_codes),
        "total_fund_count": len(previous_positions),
        "coverage_ratio": round(coverage_ratio, 6),
        "drift_residual_half_l1_pct": round(residual_pct, 6),
        "expected_weights": expected,
    }


def _position_delta_rows(
    previous: StateOccurrence,
    current: StateOccurrence,
    expected_weights: dict[str, float] | None,
) -> list[dict[str, Any]]:
    old_positions = previous.representative.positions
    new_positions = current.representative.positions
    rows: list[dict[str, Any]] = []
    for code in sorted(set(old_positions) | set(new_positions)):
        old = old_positions.get(code)
        new = new_positions.get(code)
        old_ratio = old.weight_ratio if old else 0.0
        new_ratio = new.weight_ratio if new else 0.0
        expected_ratio = expected_weights.get(code) if expected_weights is not None else None
        row = {
            "fund_code": code,
            "fund_name": (new or old).fund_name,
            "previous_weight_pct": round(old_ratio * 100.0, 6),
            "current_weight_pct": round(new_ratio * 100.0, 6),
            "observed_delta_pct": round((new_ratio - old_ratio) * 100.0, 6),
        }
        if expected_ratio is not None:
            row["no_trade_expected_weight_pct"] = round(expected_ratio * 100.0, 6)
            row["residual_delta_pct"] = round((new_ratio - expected_ratio) * 100.0, 6)
        rows.append(row)
    rows.sort(key=lambda item: abs(item["observed_delta_pct"]), reverse=True)
    return rows


def _persistence(
    states: Sequence[StateOccurrence],
    current_index: int,
) -> dict[str, Any]:
    composition_hash = states[current_index].composition_hash
    observation_count = 0
    state_count = 0
    last_observed_at = states[current_index].last_observed_at
    for state in states[current_index:]:
        if state.composition_hash != composition_hash:
            break
        observation_count += len(state.observations)
        state_count += 1
        last_observed_at = max(last_observed_at, state.last_observed_at)
    return {
        "same_composition_observation_count": observation_count,
        "same_composition_state_count": state_count,
        "persisted_through_observed_at": last_observed_at,
    }


def build_transitions(
    states_by_portfolio: dict[str, list[StateOccurrence]],
    nav_store: NavStore,
    *,
    drift_residual_threshold_pct: float = 0.2,
    persistence_min_observations: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    transitions: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for portfolio_code, states in sorted(states_by_portfolio.items()):
        for current_index in range(1, len(states)):
            previous = states[current_index - 1]
            current = states[current_index]
            previous_positions = previous.representative.positions
            current_positions = current.representative.positions
            previous_codes = set(previous_positions)
            current_codes = set(current_positions)
            membership_changed = previous_codes != current_codes
            persistence = _persistence(states, current_index)
            official_evidence = sorted(set(previous.rebalance_evidence + current.rebalance_evidence))
            official_nonempty = "official_endpoint_nonempty" in official_evidence
            half_l1_pct = 0.5 * sum(
                abs(
                    (current_positions[code].weight_ratio if code in current_positions else 0.0)
                    - (previous_positions[code].weight_ratio if code in previous_positions else 0.0)
                )
                for code in previous_codes | current_codes
            ) * 100.0
            nav_result: dict[str, Any] = {"status": "not_applicable", "reason": "composition changed"}
            expected_weights: dict[str, float] | None = None

            if official_nonempty:
                classification = "official_rebalance_evidence_available"
                confidence = "official_endpoint_requires_dedicated_processing"
                candidate = False
                reason = "The official rebalance endpoint is non-empty; use the official-event pipeline instead of inference."
            elif membership_changed:
                persistent = persistence["same_composition_observation_count"] >= persistence_min_observations
                classification = "composition_change_candidate" if persistent else "transient_composition_change_candidate"
                confidence = "high_inferred_window" if persistent else "low_inferred_window"
                candidate = True
                reason = (
                    "The mainProducts membership changed and persisted in later official current-model snapshots."
                    if persistent
                    else "The mainProducts membership changed but persistence evidence is limited."
                )
            else:
                nav_result = _nav_drift(previous, current, nav_store)
                expected_weights = nav_result.pop("expected_weights", None)
                residual = nav_result.get("drift_residual_half_l1_pct")
                if nav_result.get("status") != "available" or residual is None:
                    classification = "insufficient_nav_evidence"
                    confidence = "not_promoted"
                    candidate = False
                    reason = nav_result.get("reason") or "NAV-drift evidence is unavailable."
                elif float(residual) <= drift_residual_threshold_pct:
                    classification = "market_drift"
                    confidence = "explained_by_no_trade_model"
                    candidate = False
                    reason = "The observed weight movement is within the no-trade NAV-drift residual threshold."
                else:
                    classification = "weight_reallocation_candidate"
                    confidence = "medium_inferred_window"
                    candidate = True
                    reason = "The same-fund weight vector is not sufficiently explained by the no-trade NAV-drift model."

            deltas = _position_delta_rows(previous, current, expected_weights)
            added = [item for item in deltas if item["previous_weight_pct"] == 0 and item["current_weight_pct"] != 0]
            removed = [item for item in deltas if item["previous_weight_pct"] != 0 and item["current_weight_pct"] == 0]
            candidate_id = "gfsec_fima_change_" + _sha256_text(
                f"{portfolio_code}|{previous.state_id}|{current.state_id}"
            )[:24]
            transition = {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "channel_id": CHANNEL_ID,
                "underlying_portfolio_code": portfolio_code,
                "strategy_name": current.strategy_name or previous.strategy_name,
                "previous_state_id": previous.state_id,
                "current_state_id": current.state_id,
                "previous_state_hash": previous.state_hash,
                "current_state_hash": current.state_hash,
                "previous_composition_hash": previous.composition_hash,
                "current_composition_hash": current.composition_hash,
                "last_observed_previous_at": previous.last_observed_at,
                "first_observed_changed_at": current.first_observed_at,
                "previous_model_generated_at": previous.last_generated_at,
                "changed_model_generated_at": current.first_generated_at,
                "position_effective_date": None,
                "timing_semantics": "bounded_by_observed_current-model snapshots; not an official effective date or customer trade date",
                "classification": classification,
                "classification_reason": reason,
                "is_change_candidate": candidate,
                "candidate_confidence": confidence,
                "eligible_for_official_rebalance_table": False,
                "official_rebalance_evidence": official_evidence,
                "same_composition": not membership_changed,
                "previous_fund_count": len(previous_codes),
                "current_fund_count": len(current_codes),
                "added_fund_count": len(added),
                "removed_fund_count": len(removed),
                "observed_turnover_half_l1_pct": round(half_l1_pct, 6),
                "drift_model": nav_result,
                "persistence": persistence,
                "added_positions": added,
                "removed_positions": removed,
                "largest_observed_weight_changes": deltas[:10],
                "previous_source_path": previous.source_paths[-1],
                "current_source_path": current.source_paths[0],
                "source_semantics": "official current-model observations plus explicitly labelled inference",
            }
            transitions.append(transition)
            if candidate:
                candidates.append(transition.copy())
    return transitions, candidates


def _state_rows(
    states_by_portfolio: dict[str, list[StateOccurrence]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    states_out: list[dict[str, Any]] = []
    positions_out: list[dict[str, Any]] = []
    for portfolio_code, states in sorted(states_by_portfolio.items()):
        for state in states:
            representative = state.representative
            state_row = {
                "schema_version": SCHEMA_VERSION,
                "channel_id": CHANNEL_ID,
                "state_id": state.state_id,
                "underlying_portfolio_code": portfolio_code,
                "strategy_name": state.strategy_name,
                "state_sequence": state.sequence,
                "state_hash": state.state_hash,
                "composition_hash": state.composition_hash,
                "observed_from_at": state.first_observed_at,
                "observed_through_at": state.last_observed_at,
                "model_generated_from_at": state.first_generated_at,
                "model_generated_through_at": state.last_generated_at,
                "position_effective_date": state.effective_dates[0] if len(state.effective_dates) == 1 else None,
                "effective_date_values": state.effective_dates,
                "position_date_semantics": "observation window; effective date remains null when the source does not disclose one",
                "source_observation_count": len(state.observations),
                "source_allocation_ids": state.allocation_ids,
                "source_paths": state.source_paths,
                "official_rebalance_evidence": state.rebalance_evidence,
                "fund_count": len(representative.positions),
                "total_weight_pct": round(representative.total_weight_pct, 6),
                "weight_close_ok": representative.weight_close_ok,
                "snapshot_fact_type": "official_current_model_observation",
                "customer_actual_holding": False,
                "alternative_products_included": False,
            }
            states_out.append(state_row)
            for position in sorted(representative.positions.values(), key=lambda item: item.fund_code):
                positions_out.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "channel_id": CHANNEL_ID,
                        "state_id": state.state_id,
                        "underlying_portfolio_code": portfolio_code,
                        "strategy_name": state.strategy_name,
                        "observed_from_at": state.first_observed_at,
                        "observed_through_at": state.last_observed_at,
                        "model_generated_from_at": state.first_generated_at,
                        "model_generated_through_at": state.last_generated_at,
                        "position_effective_date": state_row["position_effective_date"],
                        "fund_code": position.fund_code,
                        "fund_name": position.fund_name,
                        "asset_groups": list(position.asset_groups),
                        "product_source": position.product_source,
                        "under_currency": position.under_currency,
                        "fund_weight_pct": round(position.weight_pct, 6),
                        "snapshot_fact_type": "official_current_model_observation",
                        "source_semantics": "mainProducts exact weight observed from the official current-allocation endpoint",
                        "customer_actual_holding": False,
                        "alternative_product": False,
                    }
                )
    return states_out, positions_out


def _validation(
    observations: Sequence[Observation],
    issues: Sequence[dict[str, Any]],
    counters: dict[str, int],
    state_rows: Sequence[dict[str, Any]],
    position_rows: Sequence[dict[str, Any]],
    transitions: Sequence[dict[str, Any]],
    nav_store: NavStore,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(check_id: str, status: str, detail: str, count: int | None = None) -> None:
        row: dict[str, Any] = {"check_id": check_id, "status": status, "detail": detail}
        if count is not None:
            row["count"] = count
        checks.append(row)

    add(
        "GFSEC_FIMA_HISTORY_SOURCE_PRESENT",
        "passed" if observations else "failed",
        "At least one valid current-allocation observation is required.",
        len(observations),
    )
    invalid_count = counters.get("invalid_json_files", 0) + counters.get("empty_payload_files", 0)
    add(
        "GFSEC_FIMA_HISTORY_SOURCE_PARSE",
        "warn" if invalid_count else "passed",
        "Invalid or empty historical source files are excluded and retained in parse_issues.",
        invalid_count,
    )
    closure_failures = sum(1 for item in observations if not item.weight_close_ok)
    add(
        "GFSEC_FIMA_HISTORY_WEIGHT_CLOSURE",
        "failed" if closure_failures else "passed",
        "Every retained official current-model state must close within the configured weight range.",
        closure_failures,
    )
    duplicate_position_keys = len(position_rows) - len(
        {(item["state_id"], item["fund_code"]) for item in position_rows}
    )
    add(
        "GFSEC_FIMA_HISTORY_POSITION_UNIQUENESS",
        "failed" if duplicate_position_keys else "passed",
        "The flattened history key is (state_id, fund_code).",
        duplicate_position_keys,
    )
    duplicate_transition_keys = len(transitions) - len({item["candidate_id"] for item in transitions})
    add(
        "GFSEC_FIMA_HISTORY_TRANSITION_UNIQUENESS",
        "failed" if duplicate_transition_keys else "passed",
        "Each consecutive state transition must have one stable candidate_id.",
        duplicate_transition_keys,
    )
    add(
        "GFSEC_FIMA_HISTORY_ALTERNATIVE_EXCLUSION",
        "passed",
        "Only mainProducts are written; historical alternativeProducts references are counted but excluded.",
        counters.get("alternative_product_references_excluded", 0),
    )
    capture_mismatches = counters.get("capture_precision_directory_date_run_id_mismatch", 0)
    add(
        "GFSEC_FIMA_HISTORY_CAPTURE_TIME_LINEAGE",
        "warn" if capture_mismatches else "passed",
        "When a reused run-id date differs from its storage directory by more than one day, chronology falls back to the directory date.",
        capture_mismatches,
    )
    effective_count = sum(1 for item in observations if item.effective_at)
    add(
        "GFSEC_FIMA_HISTORY_EFFECTIVE_DATE_DISCLOSURE",
        "passed" if effective_count else "warn",
        "The source does not currently disclose an effectiveDate; observation dates are not substituted as official effective dates.",
        effective_count,
    )
    official_eligible = sum(1 for item in transitions if item.get("eligible_for_official_rebalance_table"))
    add(
        "GFSEC_FIMA_HISTORY_INFERENCE_BOUNDARY",
        "failed" if official_eligible else "passed",
        "Snapshot differences remain inferred candidates and are never eligible for the official rebalance table.",
        official_eligible,
    )
    endpoint_problem_count = sum(
        value
        for key, value in counters.items()
        if key.startswith("rebalance_official_endpoint_")
        and key not in {"rebalance_official_endpoint_empty", "rebalance_official_endpoint_nonempty"}
    )
    add(
        "GFSEC_FIMA_HISTORY_REBALANCE_ENDPOINT_COVERAGE",
        "warn" if endpoint_problem_count else "passed",
        "A missing, invalid, transport-error, or unrecognized rebalance snapshot is not treated as verified-empty official history.",
        endpoint_problem_count,
    )
    add(
        "GFSEC_FIMA_HISTORY_NAV_EVIDENCE",
        "passed" if nav_store.available else "warn",
        nav_store.reason or "Fund NAV returns were loaded read-only for no-trade drift checks.",
    )
    if issues:
        add(
            "GFSEC_FIMA_HISTORY_PARSE_ISSUE_LINEAGE",
            "passed",
            "Every excluded or partially parsed source file is listed with its relative source path.",
            len(issues),
        )

    statuses = {item["status"] for item in checks}
    overall = "failed" if "failed" in statuses else "warn" if "warn" in statuses else "passed"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": overall,
        "checks": checks,
        "parse_issues": list(issues),
    }


def analyze(
    raw_root: Path,
    *,
    db_path: Path | None = None,
    weight_close_min_pct: float = 99.5,
    weight_close_max_pct: float = 100.5,
    drift_residual_threshold_pct: float = 0.2,
    persistence_min_observations: int = 2,
) -> AnalysisBundle:
    observations, issues, counters = load_observations(
        raw_root,
        weight_close_min_pct=weight_close_min_pct,
        weight_close_max_pct=weight_close_max_pct,
    )
    states_by_portfolio = collapse_states(observations)
    all_fund_codes = {
        code
        for observation in observations
        for code in observation.positions
    }
    start_date, end_date = _all_cutoff_dates(states_by_portfolio)
    nav_store = load_nav_store(db_path, all_fund_codes, start_date, end_date)
    transitions, candidates = build_transitions(
        states_by_portfolio,
        nav_store,
        drift_residual_threshold_pct=drift_residual_threshold_pct,
        persistence_min_observations=persistence_min_observations,
    )
    state_rows, position_rows = _state_rows(states_by_portfolio)
    validation = _validation(
        observations,
        issues,
        counters,
        state_rows,
        position_rows,
        transitions,
        nav_store,
    )
    classifications = Counter(item["classification"] for item in transitions)
    confidence_counts = Counter(item["candidate_confidence"] for item in candidates)
    composition_candidates = [
        item for item in candidates if item["classification"] in {"composition_change_candidate", "transient_composition_change_candidate"}
    ]
    generated_at = datetime.now(tz=CHINA_TZ).isoformat(timespec="seconds")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "status": validation["status"],
        "source": {
            "channel_id": CHANNEL_ID,
            "raw_root": raw_root.resolve().as_posix(),
            "database_path": db_path.resolve().as_posix() if db_path else None,
            "database_access": "read_only" if db_path else "not_used",
        },
        "semantics": {
            "snapshot": "official current-model allocation observed at collection time",
            "history": "a sequence of observed current-model states, not official effective-dated holdings",
            "candidate": "an inferred configuration-change window, not an official rebalance or customer trade",
            "main_database_written": False,
            "official_rebalance_table_written": False,
        },
        "parameters": {
            "weight_close_min_pct": weight_close_min_pct,
            "weight_close_max_pct": weight_close_max_pct,
            "drift_residual_threshold_pct": drift_residual_threshold_pct,
            "persistence_min_observations": persistence_min_observations,
            "nav_cutoff_rule": "model_generated_date minus one calendar day",
            "nav_min_fund_coverage_ratio": 0.9,
        },
        "counts": {
            **counters,
            "portfolio_count": len(states_by_portfolio),
            "state_occurrence_count": len(state_rows),
            "position_snapshot_row_count": len(position_rows),
            "transition_count": len(transitions),
            "change_candidate_count": len(candidates),
            "composition_change_candidate_count": len(composition_candidates),
            "classification_counts": dict(sorted(classifications.items())),
            "candidate_confidence_counts": dict(sorted(confidence_counts.items())),
        },
        "high_value_candidates": [
            {
                "candidate_id": item["candidate_id"],
                "underlying_portfolio_code": item["underlying_portfolio_code"],
                "strategy_name": item["strategy_name"],
                "classification": item["classification"],
                "candidate_confidence": item["candidate_confidence"],
                "last_observed_previous_at": item["last_observed_previous_at"],
                "first_observed_changed_at": item["first_observed_changed_at"],
                "previous_model_generated_at": item["previous_model_generated_at"],
                "changed_model_generated_at": item["changed_model_generated_at"],
                "added_fund_count": item["added_fund_count"],
                "removed_fund_count": item["removed_fund_count"],
                "observed_turnover_half_l1_pct": item["observed_turnover_half_l1_pct"],
                "persistence": item["persistence"],
                "added_positions": item["added_positions"],
                "removed_positions": item["removed_positions"],
            }
            for item in composition_candidates
        ],
        "limitations": [
            "effectiveDate is not disclosed in the current-allocation payload, so no official effective date is manufactured",
            "same-composition weight candidates depend on NAV timing and remain medium-confidence review items",
            "customer-plan holdings require authenticated plan-level APIs and are outside this model-strategy dataset",
            "official rebalance endpoint snapshots are authoritative; inferred candidates never replace them",
        ],
    }
    return AnalysisBundle(
        summary=summary,
        state_snapshots=state_rows,
        position_snapshots=position_rows,
        transitions=transitions,
        candidates=candidates,
        validation=validation,
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def _markdown_summary(bundle: AnalysisBundle) -> str:
    summary = bundle.summary
    counts = summary["counts"]
    lines = [
        "# 广发证券财富管家持仓历史预览",
        "",
        f"生成时间：`{summary['generated_at']}`  ",
        f"内部校验：`{summary['status']}`  ",
        "数据口径：官方当前模型仓位的历史观测序列；配置变化仅为候选，不是官方调仓或客户交易。",
        "",
        "## 覆盖与结果",
        "",
        f"- 原始仓位文件：{counts.get('raw_files', 0)}；有效观测：{counts.get('valid_observations', 0)}。",
        f"- 底层组合：{counts.get('portfolio_count', 0)}；去重状态：{counts.get('state_occurrence_count', 0)}；持仓明细行：{counts.get('position_snapshot_row_count', 0)}。",
        f"- 相邻状态变化：{counts.get('transition_count', 0)}；变化候选：{counts.get('change_candidate_count', 0)}；成分变化候选：{counts.get('composition_change_candidate_count', 0)}。",
        f"- 无效 JSON：{counts.get('invalid_json_files', 0)}；空载荷：{counts.get('empty_payload_files', 0)}；权重闭合失败：{counts.get('weight_closure_failures', 0)}。",
        "",
        "分类分布：",
        "",
    ]
    for key, value in counts.get("classification_counts", {}).items():
        lines.append(f"- `{key}`：{value}")
    lines.extend(["", "## 高价值成分变化候选", ""])
    high_value = summary.get("high_value_candidates", [])
    if not high_value:
        lines.append("未发现持续的成分变化候选。")
    else:
        lines.extend(
            [
                "|组合代码|策略|前状态观测截止|新状态首次观测|新增|移除|半 L1 换手代理|置信度|",
                "|---|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for item in high_value:
            lines.append(
                "|{code}|{name}|{old}|{new}|{added}|{removed}|{turnover:.4f}%|{confidence}|".format(
                    code=item["underlying_portfolio_code"],
                    name=item["strategy_name"],
                    old=item["last_observed_previous_at"],
                    new=item["first_observed_changed_at"],
                    added=item["added_fund_count"],
                    removed=item["removed_fund_count"],
                    turnover=item["observed_turnover_half_l1_pct"],
                    confidence=item["candidate_confidence"],
                )
            )
        lines.append("")
        for item in high_value:
            removed = "、".join(
                f"{position['fund_code']} {position['fund_name']}（{position['previous_weight_pct']:.2f}%→0）"
                for position in item.get("removed_positions", [])
            ) or "无"
            added = "、".join(
                f"{position['fund_code']} {position['fund_name']}（0→{position['current_weight_pct']:.2f}%）"
                for position in item.get("added_positions", [])
            ) or "无"
            lines.append(
                f"- `{item['underlying_portfolio_code']}`：移除 {removed}；新增 {added}。"
            )
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- `position_snapshots.jsonl` 可用于回看某次采集时官方模型展示的精确权重，但不能替代未披露的生效日。",
            "- `change_candidates.jsonl` 只进入人工复核层；`eligible_for_official_rebalance_table` 固定为 `false`。",
            "- `transition_audit.jsonl` 同时保留被净值漂移解释的变化，便于复核阈值，不应全部视为调仓。",
            "- 本程序只读主库净值表，不修改主库、正式调仓表或页面数据包。",
            "",
            "## 文件",
            "",
            "- `state_snapshots.jsonl`：状态级历史快照及来源窗口。",
            "- `position_snapshots.jsonl`：基金级精确权重观测。",
            "- `transition_audit.jsonl`：所有相邻状态变化及漂移解释。",
            "- `change_candidates.jsonl`：需要进一步复核的配置变化候选。",
            "- `validation.json`：内部校验与被排除源文件清单。",
            "",
        ]
    )
    return "\n".join(lines)


def write_bundle(bundle: AnalysisBundle, output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = output_dir.parent / f".{output_dir.name}.staging-{os.getpid()}"
    if stage.exists():
        raise FileExistsError(f"staging directory already exists: {stage}")
    stage.mkdir(parents=False)
    _write_json(stage / "summary.json", bundle.summary)
    _write_json(stage / "validation.json", bundle.validation)
    _write_jsonl(stage / "state_snapshots.jsonl", bundle.state_snapshots)
    _write_jsonl(stage / "position_snapshots.jsonl", bundle.position_snapshots)
    _write_jsonl(stage / "transition_audit.jsonl", bundle.transitions)
    _write_jsonl(stage / "change_candidates.jsonl", bundle.candidates)
    (stage / "summary.md").write_text(_markdown_summary(bundle), encoding="utf-8")
    os.replace(stage, output_dir)


def _safe_workspace_path(workspace_root: Path, value: Any, key: str) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    pure = PurePosixPath(text)
    if not text or pure.is_absolute() or (len(text) >= 2 and text[1] == ":") or ".." in pure.parts:
        raise ValueError(f"{key} must be a safe workspace-relative path: {text!r}")
    resolved = workspace_root.joinpath(*pure.parts).resolve(strict=False)
    if resolved != workspace_root and workspace_root not in resolved.parents:
        raise ValueError(f"{key} escapes workspace root: {text!r}")
    return resolved


def resolve_runtime_paths(workspace_root: Path) -> tuple[Path, Path, Path]:
    root = workspace_root.resolve()
    config_path = root / "本机配置" / "runtime.local.json"
    config: dict[str, Any] = {}
    if config_path.is_file():
        loaded = _read_json(config_path)
        if not isinstance(loaded, dict):
            raise ValueError(f"runtime config must be an object: {config_path}")
        config = loaded
    raw_root = _safe_workspace_path(root, config.get("rawRoot", "data/raw"), "rawRoot")
    database_root = _safe_workspace_path(root, config.get("databaseRoot", "data"), "databaseRoot")
    output_root = _safe_workspace_path(root, config.get("outputRoot", "outputs"), "outputRoot")
    return raw_root / "gfsec_fima" / "public_api", database_root / "analysis_zh_current.sqlite", output_root


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an inference-labelled GF Securities FIMA current-position history preview.",
    )
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--weight-close-min-pct", type=float, default=99.5)
    parser.add_argument("--weight-close-max-pct", type=float, default=100.5)
    parser.add_argument("--drift-residual-threshold-pct", type=float, default=0.2)
    parser.add_argument("--persistence-min-observations", type=int, default=2)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    default_raw_root, default_db_path, output_root = resolve_runtime_paths(args.workspace_root)
    raw_root = (args.raw_root or default_raw_root).resolve()
    db_path = (args.db_path or default_db_path).resolve()
    if args.output_dir:
        output_dir = args.output_dir.resolve()
    else:
        stamp = datetime.now(tz=CHINA_TZ).strftime("%Y%m%dT%H%M%S%z")
        output_dir = output_root / "gfsec_fima_position_history" / stamp
    bundle = analyze(
        raw_root,
        db_path=db_path,
        weight_close_min_pct=args.weight_close_min_pct,
        weight_close_max_pct=args.weight_close_max_pct,
        drift_residual_threshold_pct=args.drift_residual_threshold_pct,
        persistence_min_observations=args.persistence_min_observations,
    )
    write_bundle(bundle, output_dir)
    print(
        json.dumps(
            {
                "status": bundle.summary["status"],
                "output_dir": output_dir.as_posix(),
                "counts": bundle.summary["counts"],
            },
            ensure_ascii=False,
        )
    )
    return 1 if bundle.validation["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
