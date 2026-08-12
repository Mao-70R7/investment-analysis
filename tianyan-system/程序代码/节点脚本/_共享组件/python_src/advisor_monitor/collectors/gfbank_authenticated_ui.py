from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


CHANNEL_ID = "gfbank_cgb"
CHANNEL_NAME = "广发银行发现精彩"
APP_PACKAGE = "com.cgbchina.xpt"
STRATEGY_ENTRIES = ("理财组合", "超级定投", "目标盈")
STRATEGY_ENTRY_META = {
    "理财组合": ("portfolio-list", "银行代销基金投顾理财组合"),
    "超级定投": ("super-invest-list", "银行代销基金投顾超级定投"),
    "目标盈": ("target-profit-list", "银行代销基金投顾目标盈"),
}

COMPANY_ALIASES: dict[str, tuple[str, str]] = {
    "南方基金": ("南方", "南方基金管理股份有限公司"),
    "招商基金": ("招商", "招商基金管理有限公司"),
    "博时基金": ("博时", "博时基金管理有限公司"),
    "广发基金": ("广发", "广发基金管理有限公司"),
    "景顺长城": ("景顺长城", "景顺长城基金管理有限公司"),
    "鹏华基金": ("鹏华", "鹏华基金管理有限公司"),
}

PRODUCT_PATTERN = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9·（）()\-]+?\d+天$")
INTERVAL_META: dict[str, tuple[str, tuple[str, ...]]] = {
    "1m": ("近1月", ("1m", "近1月", "近一月")),
    "6m": ("近6月", ("6m", "近6月", "近六月")),
    "1y": ("近1年", ("1y", "近1年", "近一年")),
    "since_inception": ("成立以来", ("since_inception", "since", "成立以来")),
}


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...]

    @property
    def center_x(self) -> float:
        return sum(point[0] for point in self.box) / max(1, len(self.box))

    @property
    def center_y(self) -> float:
        return sum(point[1] for point in self.box) / max(1, len(self.box))


def clean_ocr_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip("?？")


def canonical_company(value: str) -> tuple[str, str] | None:
    clean = clean_ocr_text(value)
    for alias, result in COMPANY_ALIASES.items():
        if alias in clean:
            return result
    return None


def looks_like_strategy_name(value: Any) -> bool:
    text = clean_ocr_text(value)
    if len(text) < 4 or len(text) > 50:
        return False
    if any(
        marker in text
        for marker in (
            "持有满",
            "建议持有",
            "投顾服务费",
            "组合涨跌幅",
            "基准涨跌幅",
            "成立以来收益率",
            "最新净值",
            "本策略方案由",
        )
    ):
        return False
    if PRODUCT_PATTERN.fullmatch(text):
        return True
    if "目标盈" in text or ("目标" in text and re.search(r"\d+期$", text)):
        return True
    return "定投" in text and text not in {"超级定投", "定投专区", "基金定投"}


def infer_strategy_entry(strategy_name: Any, evidence_name: Any = None) -> str:
    evidence = clean_ocr_text(evidence_name)
    for entry_label in STRATEGY_ENTRIES:
        if entry_label in evidence:
            return entry_label
    name = clean_ocr_text(strategy_name)
    if "目标盈" in name or ("目标" in name and re.search(r"\d+期$", name)):
        return "目标盈"
    if "定投" in name:
        return "超级定投"
    return "理财组合"


def source_strategy_id(strategy_name: str) -> str:
    digest = hashlib.sha256(strategy_name.strip().encode("utf-8")).hexdigest()[:16]
    return f"ui-name-{digest}"


def snapshot_id(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"gfbank_cgb-authenticated_ui-{digest}"


def normalize_ocr_rows(rows: Iterable[Any]) -> list[OcrLine]:
    normalized: list[OcrLine] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        raw_box, raw_text, raw_confidence = row[0], row[1], row[2]
        try:
            box = tuple((float(point[0]), float(point[1])) for point in raw_box)
            confidence = float(raw_confidence)
        except (TypeError, ValueError, IndexError):
            continue
        text = clean_ocr_text(raw_text)
        if text:
            normalized.append(OcrLine(text=text, confidence=confidence, box=box))
    return normalized


def extract_strategy_cards(
    ocr_lines: Iterable[OcrLine],
    *,
    screenshot_name: str,
    screenshot_snapshot_id: str,
) -> list[dict[str, Any]]:
    lines = list(ocr_lines)
    companies = [line for line in lines if canonical_company(line.text)]
    cards: list[dict[str, Any]] = []
    for product in lines:
        product_text = clean_ocr_text(product.text)
        if product.confidence < 0.85 or product.center_x < 430:
            continue
        if not looks_like_strategy_name(product_text):
            continue
        candidates = [
            company
            for company in companies
            if company.center_x < 460 and abs(company.center_y - product.center_y) <= 110
        ]
        if not candidates:
            continue
        company = min(candidates, key=lambda item: abs(item.center_y - product.center_y))
        company_info = canonical_company(company.text)
        if not company_info:
            continue
        prefix, advisor_name = company_info
        strategy_name = product_text if product_text.startswith(prefix) else f"{prefix}{product_text}"
        holding_days_match = re.search(r"(\d+)天$", product_text)
        cards.append(
            {
                "strategy_name": strategy_name,
                "advisor_name": advisor_name,
                "suggested_holding_period": f"{holding_days_match.group(1)}天以上" if holding_days_match else None,
                "product_card_text": product_text,
                "company_card_text": clean_ocr_text(company.text),
                "product_ocr_confidence": round(product.confidence, 6),
                "company_ocr_confidence": round(company.confidence, 6),
                "tap_x": round(product.center_x),
                "tap_y": round(product.center_y),
                "source_evidence_file": screenshot_name,
                "source_snapshot_id": screenshot_snapshot_id,
                "strategy_entry": infer_strategy_entry(strategy_name, screenshot_name),
            }
        )
    return cards


def ocr_strategy_cards(image_paths: Iterable[Path]) -> list[dict[str, Any]]:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised only on missing optional runtime
        raise RuntimeError("rapidocr_onnxruntime is required for gfbank authenticated screenshot parsing") from exc

    engine = RapidOCR()
    by_name: dict[str, dict[str, Any]] = {}
    for image_path in sorted(image_paths):
        result, _elapsed = engine(str(image_path))
        cards = extract_strategy_cards(
            normalize_ocr_rows(result or []),
            screenshot_name=image_path.name,
            screenshot_snapshot_id=snapshot_id(image_path),
        )
        for card in cards:
            name = card["strategy_name"]
            previous = by_name.get(name)
            if previous is None or card["product_ocr_confidence"] > previous["product_ocr_confidence"]:
                by_name[name] = card
    return sorted(by_name.values(), key=lambda row: row["strategy_name"])


def ocr_entry_provider_cards(image_paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Locate institution cards on the image-only 超级定投/目标盈 landing pages."""
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised only on missing optional runtime
        raise RuntimeError("rapidocr_onnxruntime is required for gfbank authenticated screenshot parsing") from exc

    engine = RapidOCR()
    by_advisor: dict[str, dict[str, Any]] = {}
    for image_path in sorted(image_paths):
        result, _elapsed = engine(str(image_path))
        for line in normalize_ocr_rows(result or []):
            company = canonical_company(line.text)
            if not company or line.confidence < 0.82 or line.center_y < 430:
                continue
            prefix, advisor_name = company
            candidate = {
                "provider_prefix": prefix,
                "advisor_name": advisor_name,
                "tap_x": 540,
                "tap_y": round(line.center_y),
                "ocr_confidence": round(line.confidence, 6),
                "source_evidence_file": image_path.name,
                "source_snapshot_id": snapshot_id(image_path),
            }
            previous = by_advisor.get(advisor_name)
            if previous is None or candidate["ocr_confidence"] > previous["ocr_confidence"]:
                by_advisor[advisor_name] = candidate
    return sorted(by_advisor.values(), key=lambda row: int(row["tap_y"]))


def xml_texts(path: Path) -> list[str]:
    root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    texts: list[str] = []
    for node in root.iter("node"):
        text = str(node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        if text:
            texts.append(text)
    return texts


def _first_matching(texts: Iterable[str], pattern: str) -> str | None:
    regex = re.compile(pattern)
    for text in texts:
        match = regex.search(text)
        if match:
            return match.group(1).strip()
    return None


def _numeric_before(texts: list[str], label: str, lookback: int = 3) -> float | None:
    for index, text in enumerate(texts):
        if label not in text:
            continue
        for candidate in reversed(texts[max(0, index - lookback) : index]):
            clean = candidate.strip().replace("%", "")
            if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", clean):
                return float(clean)
    return None


def _percent_after(texts: list[str], label: str, lookahead: int = 4) -> float | None:
    for index, text in enumerate(texts):
        if label not in text:
            continue
        for candidate in texts[index + 1 : index + 1 + lookahead]:
            match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", candidate)
            if match:
                # The normalized strategy performance model stores returns in
                # percentage points (0.07 means 0.07%), not decimal returns.
                return float(match.group(1))
    return None


def parse_month_day(value: str | None, year: int) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{2})[./-](\d{2})", value)
    if not match:
        return None
    try:
        return datetime(year, int(match.group(1)), int(match.group(2))).date().isoformat()
    except ValueError:
        return None


def parse_full_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{4})[./-](\d{2})[./-](\d{2})", value)
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date().isoformat()
    except ValueError:
        return None


def interval_code_from_path(path: Path) -> str | None:
    stem = clean_ocr_text(path.stem).lower()
    for code, (_label, aliases) in INTERVAL_META.items():
        if any(alias.lower() in stem for alias in aliases):
            return code
    return None


def detail_evidence_key(path: Path | str) -> str | None:
    stem = Path(path).stem
    match = re.match(r"^detail_(.+)_(?:1m|6m|1y|since_inception|benchmark)$", stem)
    return match.group(1) if match else None


def _full_dates(texts: Iterable[str]) -> list[str]:
    dates: list[str] = []
    for text in texts:
        parsed = parse_full_date(text)
        if parsed:
            dates.append(parsed)
    return dates


def _performance_section_texts(texts: list[str]) -> list[str]:
    start = next((index for index, text in enumerate(texts) if text == "业绩表现"), None)
    if start is None:
        return []
    end_markers = ("每个投资者的投顾组合", "组合配置", "交易规则")
    end = next(
        (
            index
            for index, text in enumerate(texts[start + 1 :], start=start + 1)
            if any(text.startswith(marker) for marker in end_markers)
        ),
        len(texts),
    )
    return texts[start:end]


def benchmark_description_from_texts(texts: list[str]) -> str | None:
    skip_values = {"业绩基准", "关闭", "我知道了", "知道了", "确定"}
    for index, text in enumerate(texts):
        if "业绩基准" not in text:
            continue
        inline = re.sub(r"^.*?业绩基准\s*[：:]?\s*", "", text).strip()
        if inline and inline not in skip_values:
            return inline
        for candidate in texts[index + 1 : index + 10]:
            clean = candidate.strip()
            if (
                clean
                and clean not in skip_values
                and not looks_like_strategy_name(clean)
                and "投顾策略详情" not in clean
                and "业绩表现" not in clean
                and "组合涨跌幅" not in clean
                and "基准涨跌幅" not in clean
            ):
                return clean
    return None


def parse_detail_xml(
    path: Path,
    *,
    captured_at: datetime,
    interval_code: str | None = None,
) -> dict[str, Any] | None:
    texts = xml_texts(path)
    if not any("投顾策略详情" in text for text in texts):
        return None
    strategy_name = next(
        (
            text
            for text in texts
            if looks_like_strategy_name(text)
        ),
        None,
    )
    if not strategy_name:
        return None
    advisor_name = _first_matching(texts, r"本策略方案由\s*(.+?)\s*提供")
    risk_level = next((text for text in texts if re.match(r"P?R\d", text)), None)
    cumulative_pct = _numeric_before(texts, "成立以来收益率")
    nav = _numeric_before(texts, "最新净值")
    nav_label = next((text for text in texts if "最新净值" in text), None)
    trade_date = parse_month_day(nav_label, captured_at.year)
    selected_interval_code = interval_code or interval_code_from_path(path)
    selected_interval_label = (
        INTERVAL_META[selected_interval_code][0] if selected_interval_code in INTERVAL_META else None
    )
    performance_texts = _performance_section_texts(texts)
    range_dates = _full_dates(performance_texts)
    suggested = _numeric_before(texts, "建议持有")
    suggested_text = None
    for index, text in enumerate(texts):
        if "建议持有" in text and index > 0:
            suggested_text = texts[index - 1]
            break
    fee = _first_matching(texts, r"投顾服务费率[：:]\s*([^，。；;]+)")
    highlights = [
        text
        for text in texts
        if text and not text.startswith("亮点") and any(marker in text for marker in ("严控风险", "专属定制", "致力于"))
    ]
    return {
        "strategy_name": strategy_name,
        "advisor_name": advisor_name,
        "risk_level": risk_level,
        "cumulative_return": cumulative_pct,
        "nav": nav,
        "trade_date": trade_date,
        # 广发智投详情页的“组合涨跌幅/基准涨跌幅”跟随近1月、
        # 近6月、近1年、成立以来标签变化，是区间收益，不是单日收益。
        "daily_return": None,
        "benchmark_return": None,
        "interval_code": selected_interval_code,
        "interval_label": selected_interval_label,
        "interval_return": _percent_after(performance_texts, "组合涨跌幅"),
        "interval_benchmark_return": _percent_after(performance_texts, "基准涨跌幅"),
        "interval_start_date": range_dates[-2] if len(range_dates) >= 2 else None,
        "interval_end_date": range_dates[-1] if range_dates else trade_date,
        "suggested_holding_period": suggested_text or (f"{int(suggested)}个月以上" if suggested is not None else None),
        "advisory_fee_rate": fee,
        "benchmark": benchmark_description_from_texts(texts),
        "strategy_description": "；".join(dict.fromkeys(highlights)) or None,
        "source_evidence_file": path.name,
        "source_snapshot_id": snapshot_id(path),
    }


def parse_curve_point_xml(path: Path) -> dict[str, Any] | None:
    """Parse one exact point exposed by the draggable 成立以来 ECharts tooltip.

    The page header above the chart is regular WebView text.  After a horizontal
    touch it contains the snapped trading date plus the exact strategy and
    benchmark returns.  Those values are structured UI evidence; chart pixels
    themselves are deliberately not interpreted.
    """
    if "since_inception" not in clean_ocr_text(path.stem).lower():
        return None
    texts = xml_texts(path)
    if not any("投顾策略详情" in text for text in texts):
        return None
    strategy_name = next(
        (
            text
            for text in texts
            if looks_like_strategy_name(text)
        ),
        None,
    )
    if not strategy_name:
        return None
    performance_texts = _performance_section_texts(texts)
    dates = _full_dates(performance_texts)
    cumulative_return = _percent_after(performance_texts, "组合涨跌幅")
    if not dates or cumulative_return is None:
        return None
    return {
        "strategy_name": strategy_name,
        "trade_date": dates[0],
        "cumulative_return": cumulative_return,
        "benchmark_return": _percent_after(performance_texts, "基准涨跌幅"),
        "nav": 1.0 + cumulative_return / 100.0,
        "source_evidence_file": path.name,
        "source_snapshot_id": snapshot_id(path),
    }


def _advisor_from_texts(texts: Iterable[str]) -> str | None:
    for text in texts:
        company = canonical_company(text)
        if company:
            return company[1]
    return None


def parse_special_entry_xmls(
    paths: Iterable[Path],
    *,
    run_id: str,
    captured_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse non-portfolio disclosures without inventing performance.

    超级定投 exposes a service/current-allocation page, while 目标盈 exposes
    current and historical service periods.  Neither page is a substitute for
    a dated strategy/benchmark curve, so this parser emits strategy master rows
    only.
    """
    captured_text = captured_at.isoformat(timespec="seconds")
    by_name: dict[str, dict[str, Any]] = {}
    parsed_files = 0
    for path in sorted(paths):
        try:
            texts = xml_texts(path)
        except (OSError, ET.ParseError):
            continue
        parsed_files += 1
        evidence = clean_ocr_text(path.name)
        entry = "目标盈" if "目标盈" in evidence else "超级定投" if "超级定投" in evidence else None
        if entry is None:
            continue
        file_advisor = _advisor_from_texts(texts)
        names: list[str] = []
        if entry == "超级定投":
            names = [
                text.strip()
                for text in texts
                if re.fullmatch(r"(?:南方|广发|招商|博时|景顺长城|鹏华)基金超级定投", text.strip())
            ]
        else:
            for text in texts:
                clean = re.sub(r"^(?:南方|广发|招商|博时|景顺长城|鹏华)(?:基金)?\s*[-—]\s*", "", text.strip())
                if "目标" in clean and re.search(r"\d+期$", clean):
                    names.append(clean)
        for name in dict.fromkeys(names):
            strategy_id = source_strategy_id(name)
            advisor_name = file_advisor
            if advisor_name is None:
                advisor_name = _advisor_from_texts([name])
            target_rate = _numeric_before(texts, "目标止盈年化收益率") if entry == "目标盈" else None
            operating_days = None
            service_state = None
            try:
                name_index = next(index for index, text in enumerate(texts) if name in text)
            except StopIteration:
                name_index = -1
            if name_index >= 0:
                next_name_index = next(
                    (
                        index
                        for index in range(name_index + 1, len(texts))
                        if "目标" in texts[index] and re.search(r"\d+期$", texts[index])
                    ),
                    min(len(texts), name_index + 14),
                )
                local_texts = texts[name_index:next_name_index]
                for index, text in enumerate(local_texts):
                    if text != "实际运作天数":
                        continue
                    for candidate in reversed(local_texts[max(0, index - 4):index]):
                        if re.fullmatch(r"\d+", candidate):
                            operating_days = int(candidate)
                            break
                    if operating_days is not None:
                        break
                service_state = next(
                    (
                        text
                        for text in local_texts
                        if any(marker in text for marker in ("运作中", "已止盈", "运作结束"))
                    ),
                    None,
                )
            description_parts: list[str] = []
            if entry == "目标盈" and target_rate is not None:
                description_parts.append(f"目标止盈年化收益率{target_rate:.2f}%")
            if operating_days is not None:
                description_parts.append(f"实际运作{operating_days}天")
            if entry == "超级定投":
                for text in texts:
                    if any(marker in text for marker in ("您希望这笔钱怎么投", "发车日期", "本期投入建议", "本期扣款率")):
                        description_parts.append(text.strip())
            source_id = snapshot_id(path)
            current_target_name = names[0] if names else None
            is_current = entry == "超级定投" or (
                entry == "目标盈" and "current" in evidence and name == current_target_name
            ) or bool(service_state and "运作中" in service_state)
            row = {
                "channel_id": CHANNEL_ID,
                "source_strategy_id": strategy_id,
                "strategy_name": name,
                "advisor_name": advisor_name,
                "strategy_type": STRATEGY_ENTRY_META[entry][1],
                "risk_level": next((text for text in texts if re.match(r"^(?:中低|中高|低|高)风险$|^P?R\d", text)), None),
                "launch_date": None,
                "suggested_holding_period": None,
                "minimum_amount": None,
                "advisory_fee_rate": None,
                "benchmark": None,
                "tags": ["广发银行", "广发智投", entry, "登录态页面"],
                "strategy_description": "；".join(dict.fromkeys(description_parts)) or None,
                "status": "active_authenticated_ui" if is_current else "historical_authenticated_ui",
                "source_url": f"cgbapp://{APP_PACKAGE}/gfzt/{STRATEGY_ENTRY_META[entry][0]}/{strategy_id}",
                "first_seen_at": captured_text,
                "last_seen_at": captured_text,
                "run_id": run_id,
                "source_snapshot_id": source_id,
                "extra": {
                    "access_level": "login",
                    "strategy_entry": entry,
                    "source_strategy_id_semantics": "stable_slug_derived_from_authenticated_ui_name",
                    "official_strategy_id_available": False,
                    "source_evidence_file": path.name,
                    "performance_disclosure_status": "not_disclosed_on_authenticated_entry_page",
                    "target_annualized_return_pct": target_rate,
                    "actual_operating_days": operating_days,
                    "service_state": service_state,
                },
            }
            previous = by_name.get(name)
            if previous is None or sum(value not in (None, "", []) for value in row.values()) > sum(
                value not in (None, "", []) for value in previous.values()
            ):
                by_name[name] = row
    rows = sorted(by_name.values(), key=lambda row: (row["strategy_type"], row["strategy_name"]))
    return rows, {
        "special_entry_file_total": len(list(paths)) if not isinstance(paths, list) else len(paths),
        "special_entry_parsed_file_total": parsed_files,
        "special_entry_strategy_total": len(rows),
    }


def captured_at_from_files(paths: Iterable[Path]) -> datetime:
    candidates = [path.stat().st_mtime for path in paths if path.exists()]
    if not candidates:
        return datetime.now().astimezone()
    return datetime.fromtimestamp(max(candidates)).astimezone()


def match_details_for_card(
    card: dict[str, Any],
    details_by_name: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], str]:
    card_name = clean_ocr_text(card.get("strategy_name"))
    exact = details_by_name.get(card_name, [])
    if exact:
        return exact, "exact_strategy_name"
    product_text = clean_ocr_text(card.get("product_card_text"))
    advisor_name = clean_ocr_text(card.get("advisor_name"))
    candidates: list[list[dict[str, Any]]] = []
    for detail_name, detail_rows in details_by_name.items():
        if not product_text or product_text not in clean_ocr_text(detail_name):
            continue
        detail_advisors = {
            clean_ocr_text(row.get("advisor_name"))
            for row in detail_rows
            if clean_ocr_text(row.get("advisor_name"))
        }
        if advisor_name and detail_advisors and advisor_name not in detail_advisors:
            continue
        candidates.append(detail_rows)
    if len(candidates) == 1:
        return candidates[0], "product_card_text_with_advisor"
    return [], "unmatched"


def capture_metadata_by_strategy(capture_summary: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Return sanitized route and performance-lineage metadata keyed by strategy name.

    The official target-profit H5 uses ``groupCode + spGroupCode`` for the
    child-period detail, but requests performance with ``groupCode`` only.
    Keeping those identifiers next to the normalized row lets later collectors
    reproduce the parent request without persisting the session/signing payload.
    """
    if not isinstance(capture_summary, dict):
        return {}
    by_name: dict[str, dict[str, Any]] = {}
    for item in capture_summary.get("h5_route_metadata", []):
        if not isinstance(item, dict):
            continue
        name = clean_ocr_text(item.get("strategy_name"))
        if not name:
            continue
        target = by_name.setdefault(name, {})
        # Prefer the child detail over a landing route because it contains both
        # identifiers.  Values were already sanitized by the capture process.
        if item.get("route_role") == "target_profit_child_detail" or "route_role" not in target:
            for key in ("app_id", "h5_host", "h5_path", "group_code", "sp_group_code", "route_role"):
                value = item.get(key)
                if value not in (None, ""):
                    target[key] = value
    for item in capture_summary.get("performance_lineages", []):
        if not isinstance(item, dict):
            continue
        name = clean_ocr_text(item.get("strategy_name"))
        if not name:
            continue
        target = by_name.setdefault(name, {})
        for key in (
            "performance_disclosure_status",
            "performance_entity_scope",
            "performance_lineage_evidence",
            "entry_strategy_name",
        ):
            value = item.get(key)
            if value not in (None, ""):
                target[key] = value
        route = item.get("h5_route_metadata")
        if isinstance(route, dict):
            for key in ("app_id", "h5_host", "h5_path", "group_code", "sp_group_code", "route_role"):
                value = route.get(key)
                if value not in (None, ""):
                    target[key] = value
    return by_name


def build_normalized(
    *,
    image_paths: Iterable[Path],
    detail_paths: Iterable[Path],
    curve_point_paths: Iterable[Path] = (),
    special_entry_paths: Iterable[Path] = (),
    capture_summary: dict[str, Any] | None = None,
    run_id: str,
    captured_at: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    special_entry_paths = list(special_entry_paths)
    detail_paths = list(detail_paths)
    benchmark_by_evidence_key: dict[str, str] = {}
    for path in detail_paths:
        if not path.stem.endswith("_benchmark"):
            continue
        key = detail_evidence_key(path)
        if not key:
            continue
        try:
            description = benchmark_description_from_texts(xml_texts(path))
        except (OSError, ET.ParseError):
            description = None
        if description:
            benchmark_by_evidence_key[key] = description
    details = [
        detail
        for detail in (parse_detail_xml(path, captured_at=captured_at) for path in detail_paths)
        if detail is not None
    ]
    for detail in details:
        key = detail_evidence_key(str(detail.get("source_evidence_file") or ""))
        if key and benchmark_by_evidence_key.get(key):
            detail["benchmark"] = benchmark_by_evidence_key[key]
    details_by_name: dict[str, list[dict[str, Any]]] = {}
    for detail in details:
        details_by_name.setdefault(detail["strategy_name"], []).append(detail)
    card_source = "screenshot_ocr"
    ocr_error = None
    try:
        # 目标盈 landing cards contain phrases such as “观察期180天”; these
        # are product terms, not strategy names.  Exact period names come from
        # authenticated current/history XML instead.
        card_images = [path for path in image_paths if "目标盈" not in clean_ocr_text(path.stem)]
        cards = ocr_strategy_cards(card_images)
    except RuntimeError as exc:
        # The authenticated detail page exposes the official strategy name and
        # advisor as structured text.  OCR is only needed to discover cards; it
        # must not make already captured exact detail evidence unusable.
        ocr_error = f"{type(exc).__name__}: {exc}"
        card_source = "authenticated_detail_xml_fallback"
        cards = []
    if not cards:
        if ocr_error is None:
            ocr_error = "screenshot_ocr_returned_no_valid_strategy_cards"
        card_source = "authenticated_detail_xml_fallback"
        for strategy_name, rows in sorted(details_by_name.items()):
            detail = max(
                rows,
                key=lambda row: sum(
                    row.get(field) not in (None, "")
                    for field in ("nav", "cumulative_return", "risk_level", "advisory_fee_rate", "benchmark")
                ),
            )
            cards.append(
                {
                    "strategy_name": strategy_name,
                    "advisor_name": detail.get("advisor_name"),
                    "suggested_holding_period": detail.get("suggested_holding_period"),
                    "product_card_text": strategy_name,
                    "company_card_text": None,
                    "product_ocr_confidence": None,
                    "company_ocr_confidence": None,
                    "source_evidence_file": detail.get("source_evidence_file"),
                    "source_snapshot_id": detail.get("source_snapshot_id"),
                    "strategy_entry": infer_strategy_entry(strategy_name, detail.get("source_evidence_file")),
                }
            )
    curve_points = [
        point
        for point in (parse_curve_point_xml(path) for path in curve_point_paths)
        if point is not None
    ]
    curve_points_by_name: dict[str, dict[str, dict[str, Any]]] = {}
    curve_point_conflicts: list[dict[str, Any]] = []
    for point in curve_points:
        by_date = curve_points_by_name.setdefault(point["strategy_name"], {})
        previous = by_date.get(point["trade_date"])
        if previous and any(
            previous.get(field) != point.get(field)
            for field in ("nav", "cumulative_return", "benchmark_return")
        ):
            curve_point_conflicts.append(
                {
                    "strategy_name": point["strategy_name"],
                    "trade_date": point["trade_date"],
                    "first_source": previous.get("source_evidence_file"),
                    "second_source": point.get("source_evidence_file"),
                }
            )
        by_date[point["trade_date"]] = point
    curve_latest_detail_mismatches: list[dict[str, Any]] = []
    for strategy_name, points_by_date in curve_points_by_name.items():
        latest_curve = max(points_by_date.values(), key=lambda row: row["trade_date"])
        matching_details = [
            row
            for row in details_by_name.get(strategy_name, [])
            if row.get("trade_date") and row.get("nav") is not None and row.get("cumulative_return") is not None
        ]
        if not matching_details:
            continue
        latest_detail = max(matching_details, key=lambda row: row["trade_date"])
        reasons: list[str] = []
        if latest_curve["trade_date"] != latest_detail["trade_date"]:
            reasons.append("latest_trade_date_not_aligned")
        if latest_curve["trade_date"] == latest_detail["trade_date"]:
            if abs(float(latest_curve["nav"]) - float(latest_detail["nav"])) > 0.0001:
                reasons.append("latest_nav_not_aligned")
            if abs(float(latest_curve["cumulative_return"]) - float(latest_detail["cumulative_return"])) > 0.01:
                reasons.append("latest_cumulative_return_not_aligned")
        if reasons:
            curve_latest_detail_mismatches.append(
                {
                    "strategy_name": strategy_name,
                    "curve_trade_date": latest_curve["trade_date"],
                    "detail_trade_date": latest_detail["trade_date"],
                    "curve_nav": latest_curve["nav"],
                    "detail_nav": latest_detail["nav"],
                    "curve_cumulative_return": latest_curve["cumulative_return"],
                    "detail_cumulative_return": latest_detail["cumulative_return"],
                    "reasons": reasons,
                }
            )
    strategy_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    interval_rows: list[dict[str, Any]] = []
    captured_text = captured_at.isoformat(timespec="seconds")
    matched_detail_names: set[str] = set()
    captured_metadata = capture_metadata_by_strategy(capture_summary)

    for card in cards:
        strategy_details, name_match_method = match_details_for_card(card, details_by_name)
        detail = max(
            strategy_details,
            key=lambda row: sum(
                row.get(field) not in (None, "")
                for field in (
                    "nav",
                    "cumulative_return",
                    "risk_level",
                    "advisory_fee_rate",
                    "benchmark",
                    "strategy_description",
                )
            ),
            default={},
        )
        strategy_name = detail.get("strategy_name") or card["strategy_name"]
        strategy_entry = str(
            card.get("strategy_entry")
            or infer_strategy_entry(strategy_name, card.get("source_evidence_file"))
        )
        entry_slug, strategy_type = STRATEGY_ENTRY_META.get(
            strategy_entry,
            STRATEGY_ENTRY_META["理财组合"],
        )
        if detail.get("strategy_name"):
            matched_detail_names.add(str(detail["strategy_name"]))
        strategy_id = source_strategy_id(strategy_name)
        source_id = detail.get("source_snapshot_id") or card["source_snapshot_id"]
        strategy_curve_points = sorted(
            curve_points_by_name.get(strategy_name, {}).values(),
            key=lambda row: row["trade_date"],
        )
        route_metadata = captured_metadata.get(clean_ocr_text(strategy_name), {})
        performance_disclosure_status = route_metadata.get("performance_disclosure_status")
        if not performance_disclosure_status:
            if strategy_curve_points:
                performance_disclosure_status = (
                    "official_parent_group_curve_captured"
                    if strategy_entry == "目标盈"
                    else "official_authenticated_detail"
                )
            elif strategy_entry == "目标盈" and detail:
                performance_disclosure_status = "target_child_performance_not_verified"
            elif detail:
                performance_disclosure_status = "official_authenticated_detail"
            else:
                performance_disclosure_status = "not_disclosed"
        target_performance_verified = (
            strategy_entry != "目标盈"
            or bool(strategy_curve_points)
            or performance_disclosure_status == "official_child_detail_performance_visible"
        )
        performance_entity_scope = route_metadata.get("performance_entity_scope") or (
            "underlying_parent_group_code"
            if strategy_entry == "目标盈" and target_performance_verified
            else "target_child_period"
            if strategy_entry == "目标盈"
            else "strategy"
        )
        strategy_rows.append(
            {
                "channel_id": CHANNEL_ID,
                "source_strategy_id": strategy_id,
                "strategy_name": strategy_name,
                "advisor_name": detail.get("advisor_name") or card["advisor_name"],
                "strategy_type": strategy_type,
                "risk_level": detail.get("risk_level"),
                "launch_date": None,
                "suggested_holding_period": detail.get("suggested_holding_period") or card.get("suggested_holding_period"),
                "minimum_amount": None,
                "advisory_fee_rate": detail.get("advisory_fee_rate"),
                "benchmark": detail.get("benchmark"),
                "tags": ["广发银行", "广发智投", strategy_entry, "登录态页面"],
                "strategy_description": detail.get("strategy_description"),
                "status": "active_authenticated_ui",
                "source_url": f"cgbapp://{APP_PACKAGE}/gfzt/{entry_slug}/{strategy_id}",
                "first_seen_at": captured_text,
                "last_seen_at": captured_text,
                "run_id": run_id,
                "source_snapshot_id": source_id,
                "extra": {
                    "access_level": "login",
                    "strategy_entry": strategy_entry,
                    "source_strategy_id_semantics": "stable_slug_derived_from_authenticated_ui_name",
                    "official_strategy_id_available": bool(route_metadata.get("group_code")),
                    "official_group_code": route_metadata.get("group_code"),
                    "official_sp_group_code": route_metadata.get("sp_group_code"),
                    "official_h5_app_id": route_metadata.get("app_id"),
                    "official_h5_path": route_metadata.get("h5_path"),
                    "detail_observed": bool(detail),
                    "product_card_text": card.get("product_card_text"),
                    "company_card_text": card.get("company_card_text"),
                    "product_ocr_confidence": card.get("product_ocr_confidence"),
                    "company_ocr_confidence": card.get("company_ocr_confidence"),
                    "source_evidence_file": detail.get("source_evidence_file") or card.get("source_evidence_file"),
                    "list_strategy_name": card.get("strategy_name"),
                    "detail_strategy_name": detail.get("strategy_name"),
                    "strategy_name_match_method": name_match_method,
                    "strategy_card_source": card_source,
                    "official_curve_point_count": len(strategy_curve_points),
                    "official_curve_source": (
                        "authenticated_since_inception_tooltip"
                        if strategy_curve_points
                        else "authenticated_latest_nav_only"
                        if target_performance_verified and detail
                        else "not_disclosed"
                    ),
                    "performance_disclosure_status": performance_disclosure_status,
                    "performance_entity_scope": performance_entity_scope,
                    "performance_lineage_evidence": route_metadata.get("performance_lineage_evidence"),
                },
            }
        )
        if strategy_curve_points:
            previous_nav: float | None = None
            for point in strategy_curve_points:
                nav = float(point["nav"])
                daily_return = (
                    round((nav / previous_nav - 1.0) * 100.0, 8)
                    if previous_nav not in (None, 0.0)
                    else None
                )
                daily_rows.append(
                    {
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": strategy_id,
                        "trade_date": point["trade_date"],
                        "nav": nav,
                        "daily_return": daily_return,
                        "cumulative_return": point["cumulative_return"],
                        "benchmark_return": point.get("benchmark_return"),
                        "index_return": None,
                        "max_drawdown": None,
                        "section_name": "登录态成立以来走势图逐点披露",
                        "section_type": "gfbank_authenticated_ui_curve_tooltip",
                        "run_id": run_id,
                        "source_snapshot_id": point.get("source_snapshot_id"),
                    }
                )
                previous_nav = nav
        elif target_performance_verified and detail.get("trade_date") and detail.get("nav") is not None:
            exact_since_inception = next(
                (
                    row
                    for row in strategy_details
                    if row.get("interval_code") == "since_inception"
                    and row.get("interval_end_date") == detail.get("trade_date")
                    and row.get("interval_return") is not None
                    and abs(float(row["interval_return"]) - float(detail.get("cumulative_return") or 0.0)) <= 0.01
                ),
                None,
            )
            daily_rows.append(
                {
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": strategy_id,
                    "trade_date": detail["trade_date"],
                    "nav": detail.get("nav"),
                    "daily_return": detail.get("daily_return"),
                    "cumulative_return": detail.get("cumulative_return"),
                    "benchmark_return": (
                        exact_since_inception.get("interval_benchmark_return")
                        if exact_since_inception
                        else detail.get("benchmark_return")
                    ),
                    "index_return": None,
                    "max_drawdown": None,
                    "section_name": "登录态投顾详情页",
                    "section_type": "gfbank_authenticated_ui_latest",
                    "run_id": run_id,
                    "source_snapshot_id": detail.get("source_snapshot_id"),
                }
            )
        interval_seen: set[tuple[str, str | None]] = set()
        if not target_performance_verified:
            strategy_details = []
        for interval_detail in strategy_details:
            interval_code = interval_detail.get("interval_code")
            interval_return = interval_detail.get("interval_return")
            if interval_code not in INTERVAL_META or interval_return is None:
                continue
            as_of_date = interval_detail.get("interval_end_date") or interval_detail.get("trade_date")
            dedupe_key = (str(interval_code), as_of_date)
            if dedupe_key in interval_seen:
                continue
            interval_seen.add(dedupe_key)
            interval_rows.append(
                {
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": strategy_id,
                    "interval_code": interval_code,
                    "interval_label": INTERVAL_META[str(interval_code)][0],
                    "return_value": interval_return,
                    "benchmark_return": interval_detail.get("interval_benchmark_return"),
                    "as_of_date": as_of_date,
                    "interval_start_date": interval_detail.get("interval_start_date"),
                    "run_id": run_id,
                    "source_snapshot_id": interval_detail.get("source_snapshot_id"),
                }
            )

    special_rows, special_diagnostics = parse_special_entry_xmls(
        special_entry_paths,
        run_id=run_id,
        captured_at=captured_at,
    )
    for special_row in special_rows:
        metadata = captured_metadata.get(clean_ocr_text(special_row.get("strategy_name")), {})
        if not metadata:
            continue
        extra = special_row.get("extra") if isinstance(special_row.get("extra"), dict) else {}
        special_row["extra"] = {
            **extra,
            "official_strategy_id_available": bool(metadata.get("group_code")),
            "official_group_code": metadata.get("group_code"),
            "official_sp_group_code": metadata.get("sp_group_code"),
            "official_h5_app_id": metadata.get("app_id"),
            "official_h5_path": metadata.get("h5_path"),
        }
    strategy_rows_by_id = {row["source_strategy_id"]: row for row in strategy_rows}
    for special_row in special_rows:
        strategy_id = special_row["source_strategy_id"]
        previous = strategy_rows_by_id.get(strategy_id)
        if previous is None:
            strategy_rows.append(special_row)
            strategy_rows_by_id[strategy_id] = special_row
            continue
        merged_row = {**previous, **{key: value for key, value in special_row.items() if value not in (None, "", [])}}
        previous_extra = previous.get("extra") if isinstance(previous.get("extra"), dict) else {}
        special_extra = special_row.get("extra") if isinstance(special_row.get("extra"), dict) else {}
        if previous_extra.get("detail_observed"):
            # The landing/current-history page remains valuable for target rate,
            # operating days and state, but it must not overwrite verified detail
            # performance lineage with its generic "not disclosed here" status.
            special_extra = {
                key: value
                for key, value in special_extra.items()
                if key != "performance_disclosure_status"
            }
        merged_row["extra"] = {**previous_extra, **special_extra}
        previous.clear()
        previous.update(merged_row)

    normalized = {
        "strategy_master": strategy_rows,
        "strategy_performance_daily": daily_rows,
        "strategy_performance_interval": interval_rows,
        "strategy_fund_snapshot": [],
        "strategy_rebalance_event": [],
        "strategy_rebalance_fund_delta": [],
        "fund_public_dim": [],
        "app_public_entry": [
            {
                "channel_id": CHANNEL_ID,
                "channel_name": CHANNEL_NAME,
                "source_url": f"cgbapp://{APP_PACKAGE}/gfzt/{STRATEGY_ENTRY_META[entry_label][0]}",
                "title": f"广发智投{entry_label}",
                "run_id": run_id,
                "captured_at": captured_text,
                "access_level": "login",
                "available_entities": [
                    "strategy_master",
                    "strategy_performance_daily_partial",
                    *(["strategy_performance_interval_partial"] if interval_rows else []),
                ],
                "missing_entities": ["strategy_fund_snapshot", "strategy_rebalance_event", "strategy_rebalance_fund_delta"],
            }
            for entry_label in sorted(
                {
                    str(row.get("extra", {}).get("strategy_entry") or "理财组合")
                    for row in strategy_rows
                },
                key=STRATEGY_ENTRIES.index,
            )
        ],
        "strategy_disclosure_event": [],
    }
    diagnostics = {
        "strategy_total": len(strategy_rows),
        "detail_total": len(details),
        "detail_strategy_total": len(details_by_name),
        "curve_point_total": len(curve_points),
        "curve_strategy_total": len(curve_points_by_name),
        "curve_point_counts_by_strategy": {
            name: len(rows)
            for name, rows in sorted(curve_points_by_name.items())
        },
        "curve_point_conflicts": curve_point_conflicts,
        "curve_latest_detail_mismatches": curve_latest_detail_mismatches,
        "curve_strategy_coverage_ratio": round(
            len({row["source_strategy_id"] for row in daily_rows if row.get("section_type") == "gfbank_authenticated_ui_curve_tooltip"})
            / len(strategy_rows),
            6,
        ) if strategy_rows else 0.0,
        "daily_performance_rows": len(daily_rows),
        "interval_performance_rows": len(interval_rows),
        "benchmark_description_strategy_total": sum(
            1 for row in strategy_rows if str(row.get("benchmark") or "").strip()
        ),
        "benchmark_description_eligible_strategy_total": sum(
            1
            for row in strategy_rows
            if str(row.get("extra", {}).get("strategy_entry") or "理财组合") == "理财组合"
        ),
        "benchmark_description_coverage_ratio": round(
            sum(
                1
                for row in strategy_rows
                if str(row.get("benchmark") or "").strip()
                and str(row.get("extra", {}).get("strategy_entry") or "理财组合") == "理财组合"
            )
            / sum(
                1
                for row in strategy_rows
                if str(row.get("extra", {}).get("strategy_entry") or "理财组合") == "理财组合"
            ),
            6,
        ) if any(
            str(row.get("extra", {}).get("strategy_entry") or "理财组合") == "理财组合"
            for row in strategy_rows
        ) else 1.0,
        "strategy_entry_counts": {
            entry_label: sum(
                1
                for row in strategy_rows
                if str(row.get("extra", {}).get("strategy_entry") or "") == entry_label
            )
            for entry_label in STRATEGY_ENTRIES
        },
        "duplicate_strategy_names": sorted(
            name for name in {row["strategy_name"] for row in strategy_rows} if sum(item["strategy_name"] == name for item in strategy_rows) > 1
        ),
        "unmatched_detail_strategy_names": sorted(set(details_by_name) - matched_detail_names),
        "unmatched_curve_strategy_names": sorted(set(curve_points_by_name) - {row["strategy_name"] for row in strategy_rows}),
        "minimum_product_ocr_confidence": min(
            (
                float(row["extra"]["product_ocr_confidence"])
                for row in strategy_rows
                if row["extra"].get("product_ocr_confidence") is not None
            ),
            default=None,
        ),
        "strategy_card_source": card_source,
        "ocr_error": ocr_error,
        **special_diagnostics,
        "nav_cumulative_return_mismatches": [
            {
                "source_strategy_id": row["source_strategy_id"],
                "trade_date": row["trade_date"],
                "nav": row["nav"],
                "cumulative_return": row["cumulative_return"],
            }
            for row in daily_rows
            if row.get("nav") is not None
            and row.get("cumulative_return") is not None
            and abs(float(row["nav"]) - (1.0 + float(row["cumulative_return"]) / 100.0)) > 0.0001
        ],
    }
    return normalized, diagnostics
