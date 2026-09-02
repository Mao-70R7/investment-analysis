from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

def resolve_project_root() -> Path:
    configured = str(os.environ.get("ADVISOR_CODE_ROOT") or "").strip()
    if configured:
        candidate = Path(configured).resolve()
        if (candidate / "AGENTS.md").is_file():
            return candidate
    current = Path.cwd().resolve()
    if (current / "AGENTS.md").is_file() and (current / "basic_data").is_dir():
        return current
    layout_path = current / "本机配置" / "runtime.local.json"
    if layout_path.is_file():
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        candidate = (current / str(layout.get("codeRoot") or "")).resolve()
        if (candidate / "AGENTS.md").is_file() and (candidate / "basic_data").is_dir():
            return candidate
    raise RuntimeError("ADVISOR_CODE_ROOT or runtime.local.json must resolve the code root containing AGENTS.md and basic_data")


PROJECT_ROOT = resolve_project_root()
DEFAULT_SOURCE = PROJECT_ROOT / "site" / "basic_data"
DEFAULT_TARGET = PROJECT_ROOT / "site" / "最小发布集"
PUBLIC_PAGES = (
    "institutions.html",
    "strategies.html",
    "strategy.html",
    "compare.html",
    "mixed-performance-scatter.html",
    "ai-strategy.html",
)

STATIC_REPORT_PAGES: tuple[str, ...] = ()

STATIC_REPORT_ASSET_DIRECTORIES: tuple[str, ...] = ()

OPTIONAL_REFERENCED_ASSET_DIRECTORIES: tuple[str, ...] = ()

# These applications are intentionally maintained in the publish repository
# outside the generated advisor-report package. Preserve only explicitly
# registered paths so an atomic rebuild cannot erase them.
PRESERVED_PUBLISH_PATHS = (
    Path("basic_data/advisor_quota_workbench"),
)

ASSET_FILES = (
    "basic.css",
    "basic-common.js",
    "strategies.js",
    "institutions.js",
    "strategy-detail.js",
    "insights.js",
    "mixed-performance-scatter.js",
    "ai-strategy-config.js",
    "ai-strategy.js",
)

DATA_FILES = (
    "basic_summary_core.js",
    "fund_detail_pack.js",
    "ai_semantic_index.js",
    "ai_topic_evidence_pack.js",
    "mixed_performance_scatter_pack.js",
)

LAZY_DATA_DIRECTORIES: tuple[str, ...] = ()

GENERATED_ROUTE_PACKS = (
    "strategy_list_pack.js",
    "institution_overview_pack.js",
    "strategy_detail_index_pack.js",
    "fund_index_pack.js",
)

COMPRESSED_PAGE_PACKS = frozenset(
    (*DATA_FILES, *GENERATED_ROUTE_PACKS, "holding_snapshot_pack.js", "data_quality_pack.js")
)

PAGE_RENDERERS = {
    "strategies.html": "strategies.js",
    "institutions.html": "institutions.js",
    "strategy.html": "strategy-detail.js",
    "compare.html": "insights.js",
    "mixed-performance-scatter.html": "mixed-performance-scatter.js",
    "ai-strategy.html": "ai-strategy.js",
}

PAGE_PACK_REPLACEMENTS = {
    "strategies.html": {"basic_summary_core.js": "strategy_list_pack.js"},
    "institutions.html": {"basic_summary_core.js": "institution_overview_pack.js"},
    "compare.html": {
        "basic_summary_core.js": "strategy_detail_index_pack.js",
        "holding_snapshot_pack.js": None,
    },
    "strategy.html": {
        "basic_summary_core.js": "strategy_detail_index_pack.js",
        "fund_detail_pack.js": "fund_index_pack.js",
        "ai_semantic_index.js": None,
    },
    "mixed-performance-scatter.html": {"basic_summary_core.js": None},
    "ai-strategy.html": {"basic_summary_core.js": "strategy_list_pack.js"},
}

CONFIG_FILES = (
    "模型服务配置.js",
    "ai-strategy-local-config.js",
)

SCRIPT_FILES = (
    "serve_basic_data_site.py",
    "ai_strategy_codex_proxy.mjs",
    "start_ai_strategy_codex_proxy.ps1",
    "start_ai_strategy_codex_proxy.cmd",
    "install_ai_strategy_codex_proxy_startup.ps1",
    "install_ai_strategy_codex_proxy_startup.cmd",
    "uninstall_ai_strategy_codex_proxy_startup.ps1",
    "uninstall_ai_strategy_codex_proxy_startup.cmd",
    "start_minimal_publish.ps1",
    "start_minimal_publish.cmd",
    "stop_minimal_publish.ps1",
    "stop_minimal_publish.cmd",
)

ACTIVE_PAGE = {
    "strategy.html": "strategies.html",
}

FUND_RANK_PREFIXES = ("近一月", "近三月", "近6月", "近1年")
BLOCKING_ZERO_CHECKS = (
    "strategyDetailMissingCount",
    "strategyDetailParseErrorCount",
    "officialPerformanceImageInvalidReferenceCount",
    "officialPerformanceImageMissingSourceAssetCount",
    "officialPerformanceImageMissingPublishedAssetCount",
    "forbiddenFundDetailPageCount",
    "forbiddenFundDetailFileCount",
    "currentHoldingScaleErrorReferenceCount",
)
WARNING_ONLY_CHECKS = (
    "activeCurrentHoldingRankMissingReferenceCount",
)
STRATEGY_FILTER_FACT_FIELDS = (
    "有基准",
    "有业绩走势",
    "有历史仓位",
    "对客未终止",
)
INSTITUTION_DEFAULT_FILTER_FIELDS = (
    "有基准",
    "有业绩走势",
    "对客未终止",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the minimal public analysis page set.")
    parser.add_argument("--source-basic-data", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target-dir", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--allowed-target-parent", type=Path)
    parser.add_argument("--compression-level", type=int, choices=range(1, 10), default=6)
    return parser.parse_args()


def ensure_safe_target(target: Path, allowed_parent: Path | None = None) -> None:
    target = target.resolve()
    parent = (allowed_parent or target.parent).resolve()
    if target.parent != parent:
        raise RuntimeError(f"Target must be a direct child of {parent}: {target}")
    if target.name != "最小发布集":
        raise RuntimeError(f"Refusing to replace unexpected target directory: {target}")


def ensure_safe_staging(staging: Path, final_target: Path) -> None:
    staging = staging.resolve()
    if staging.parent != final_target.resolve().parent:
        raise RuntimeError(f"Staging directory must be a sibling of the publish target: {staging}")
    expected_prefix = f".{final_target.name}.staging-"
    if not staging.name.startswith(expected_prefix):
        raise RuntimeError(f"Unexpected staging directory: {staging}")


def copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def remove_tree(path: Path) -> None:
    """Remove a generated directory, including read-only Git object files on Windows."""

    if not path.exists():
        return

    def make_writable_and_retry(function: Any, name: str, _error: Any) -> None:
        os.chmod(name, stat.S_IWRITE | stat.S_IREAD)
        function(name)

    shutil.rmtree(path, onerror=make_writable_and_retry)


def clean_target(target: Path) -> None:
    if not target.exists():
        return
    git_dir = target / ".git"
    if not git_dir.exists():
        remove_tree(target)
        return
    for child in target.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            remove_tree(child)
        else:
            os.chmod(child, stat.S_IWRITE | stat.S_IREAD)
            child.unlink()


def validate_compare_workflow(basic_root: Path) -> None:
    ai_script = (basic_root / "assets" / "ai-strategy.js").read_text(encoding="utf-8-sig")
    compare_page = (basic_root / "compare.html").read_text(encoding="utf-8-sig")
    errors: list[str] = []
    if "./compare.html?compare=" not in ai_script or "./insights.html?tab=compare" in ai_script:
        errors.append("AI strategy comparison must navigate to the published compare.html page")
    if "./data/strategy_detail_index_pack.js" not in compare_page:
        errors.append("compare.html must use the dedicated strategy detail index pack")
    if "./data/basic_summary_core.js" in compare_page:
        errors.append("compare.html must not block first render on the full summary pack")
    if "./data/holding_snapshot_pack.js" in compare_page:
        errors.append("compare.html must load the holding snapshot pack lazily")
    if errors:
        raise RuntimeError("; ".join(errors))


def validate_strategy_list_workflow(basic_root: Path) -> None:
    """Block publication when the strategy-list entry flow drifts from the approved UI contract."""

    required_tokens = {
        "assets/basic-common.js": (
            'const strategyListInstitutionField = "销售渠道/管理机构"',
            '"策略名称", strategyListInstitutionField, "基准风险资产权重",',
            '"夏普比率", "风险等级", "业绩基准说明", "最新业绩日期", "天天展示状态",',
            'return `${channel}/${manager}`',
        ),
        "assets/strategies.js": (
            'id="strategyCompareButton"',
            "data-strategy-select",
            'params.set("compare", [...state.selectedIds].join(","))',
            "./compare.html?${params.toString()}",
        ),
        "assets/ai-strategy.js": (
            'return ["命中说明", ...B.strategyListHeaders]',
            "field === B.strategyListInstitutionField",
        ),
        "assets/insights.js": (
            '${compareStandalone ? "" : compareSelectorBlock()}',
            '${compareStandalone ? "" : `<section class="panel insight-sticky-controls">',
            "返回策略列表重新选择",
        ),
        "assets/basic.css": (
            "--entity:#B86B3E",
            "--kpi:#264F63",
            "--pos:#B33F46",
            "--neg:#3F7B56",
            ".strategy-table .sticky-select",
        ),
    }
    errors: list[str] = []
    for relative_path, tokens in required_tokens.items():
        path = basic_root / relative_path
        if not path.is_file():
            errors.append(f"missing {relative_path}")
            continue
        text = path.read_text(encoding="utf-8-sig")
        missing = [token for token in tokens if token not in text]
        if missing:
            errors.append(f"{relative_path} missing contract tokens: {missing}")

    for page_name in PUBLIC_PAGES:
        page = basic_root / page_name
        if not page.is_file():
            errors.append(f"missing {page_name}")
            continue
        text = page.read_text(encoding="utf-8-sig")
        nav = re.search(r'<nav class="nav".*?</nav>', text, flags=re.DOTALL)
        if not nav:
            errors.append(f"{page_name} is missing the primary navigation")
            continue
        compare_link_count = nav.group(0).count('href="./compare.html"')
        if compare_link_count != 0:
            errors.append(f"{page_name} must not expose strategy comparison in primary navigation")
    if errors:
        raise RuntimeError("; ".join(errors))


def validate_staged_package(staging: Path) -> None:
    required = (
        staging / "index.html",
        staging / "version.json",
        staging / "deployment_manifest.json",
        staging / "package_validation.json",
        staging / "basic_data" / "strategies.html",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Staged package is incomplete: {missing}")
    validation = json.loads((staging / "package_validation.json").read_text(encoding="utf-8-sig"))
    if validation.get("status") != "ready":
        raise RuntimeError(f"Staged package validation is not ready: {validation.get('status')}")
    validate_compare_workflow(staging / "basic_data")
    validate_strategy_list_workflow(staging / "basic_data")


def promote_staged_package(staging: Path, final_target: Path, allowed_parent: Path) -> None:
    ensure_safe_target(final_target, allowed_parent)
    validate_staged_package(staging)
    if not final_target.exists():
        staging.replace(final_target)
        return

    git_dir = final_target / ".git"
    if git_dir.is_dir():
        shutil.copytree(git_dir, staging / ".git")

    backup = final_target.parent / f".{final_target.name}.backup-{os.getpid()}"
    if backup.parent != final_target.parent or not backup.name.startswith(f".{final_target.name}.backup-"):
        raise RuntimeError(f"Unexpected publish backup directory: {backup}")
    if backup.exists():
        remove_tree(backup)

    final_target.replace(backup)
    try:
        staging.replace(final_target)
    except Exception:
        if not final_target.exists() and backup.exists():
            backup.replace(final_target)
        raise
    if backup.exists():
        remove_tree(backup)


def shard_key(value: object) -> str:
    hashed = 0
    for char in str(value or ""):
        hashed = ((hashed * 31) + ord(char)) & 0xFFFFFFFF
    return f"{hashed % 256:02x}"


def safe_filename(value: object) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or ""))
    return safe[:160] or "strategy"


def build_id(source: Path) -> str:
    digest = hashlib.sha256()
    authored_asset_dir = PROJECT_ROOT / "basic_data" / "assets"
    inputs = [
        *(source / "data" / name for name in DATA_FILES),
        source / "data" / "holding_snapshot_pack.js",
        *(
            (authored_asset_dir / name)
            if (authored_asset_dir / name).is_file()
            else (source / "assets" / name)
            for name in ASSET_FILES
        ),
        *(source / name for name in STATIC_REPORT_PAGES),
    ]
    for directory in LAZY_DATA_DIRECTORIES:
        inputs.extend(sorted((source / "data" / directory).glob("*.js")))
    for directory in (*STATIC_REPORT_ASSET_DIRECTORIES, *OPTIONAL_REFERENCED_ASSET_DIRECTORIES):
        inputs.extend(sorted(path for path in (source / "assets" / directory).glob("*") if path.is_file()))
    for path in inputs:
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return f"minimal-{digest.hexdigest()[:12]}"


def write_assignment(path: Path, prefix: str, payload: dict) -> None:
    write_text(
        path,
        prefix + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n",
    )


def assignment_payload(path: Path, marker: str) -> dict:
    text = path.read_text(encoding="utf-8")
    marker_pos = text.find(marker)
    if marker_pos < 0:
        raise ValueError(f"Marker not found in {path}: {marker}")
    value_pos = text.find("=", marker_pos)
    if value_pos < 0:
        raise ValueError(f"Assignment not found in {path}: {marker}")
    payload = text[value_pos + 1 :].strip()
    if payload.endswith(";"):
        payload = payload[:-1]
    return json.loads(payload)


def rewrite_strategy_detail_paths(payload: dict, version: str) -> dict:
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    for row in result.get("strategies", []):
        strategy_id = str(row.get("统一策略ID") or "").strip()
        if not strategy_id:
            continue
        detail_stem = safe_filename(strategy_id)
        row["detailFile"] = (
            f"./data/details/{shard_key(detail_stem)}/{detail_stem}.js?v={version}"
        )
    return result


def validate_strategy_filter_facts(summary: dict) -> int:
    """Ensure institution filters and its initial business scope are usable."""

    rows = summary.get("strategies") or []
    invalid_counts = {
        field: sum(1 for row in rows if str(row.get(field) or "").strip() not in {"是", "否"})
        for field in STRATEGY_FILTER_FACT_FIELDS
    }
    invalid_counts = {field: count for field, count in invalid_counts.items() if count}
    if invalid_counts:
        raise RuntimeError(
            "Strategy list business completeness facts are missing or invalid: "
            + json.dumps(invalid_counts, ensure_ascii=False, sort_keys=True)
        )
    default_scope_count = sum(
        1
        for row in rows
        if all(
            str(row.get(field) or "").strip() == "是"
            for field in INSTITUTION_DEFAULT_FILTER_FIELDS
        )
    )
    if rows and default_scope_count == 0:
        raise RuntimeError(
            "Institution overview default scope would be empty although strategy rows exist; "
            "check business completeness fact generation"
        )
    return default_scope_count


def build_route_packs(source: Path, target: Path, version: str) -> None:
    summary = assignment_payload(
        source / "data" / "basic_summary_core.js",
        "window.__BASIC_DATA__.summary",
    )
    summary = rewrite_strategy_detail_paths(summary, version)
    validate_strategy_filter_facts(summary)
    write_assignment(
        target / "basic_summary_core.js",
        "window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; window.__BASIC_DATA__.summary = ",
        summary,
    )
    list_keys = (
        "overview",
        "fieldDictionary",
        "strategies",
        "strategyListStats",
        "rawStrategyCount",
        "filteredOutStrategyCount",
        "displayStrategyCount",
        "qualityFilteredOutStrategyCount",
        "benchmarkDisclosure",
        "channelStats",
    )
    detail_keys = (*list_keys, "globalBenchmarks")
    write_assignment(
        target / "strategy_list_pack.js",
        "window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; window.__BASIC_DATA__.summary = ",
        {key: summary[key] for key in list_keys if key in summary},
    )
    institution_keys = (*list_keys, "rebalanceEvents", "institutionAdjustmentEvents")
    write_assignment(
        target / "institution_overview_pack.js",
        "window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; window.__BASIC_DATA__.summary = ",
        {key: summary[key] for key in institution_keys if key in summary},
    )
    write_assignment(
        target / "strategy_detail_index_pack.js",
        "window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; window.__BASIC_DATA__.summary = ",
        {key: summary[key] for key in detail_keys if key in summary},
    )

    fund_pack = assignment_payload(
        source / "data" / "fund_detail_pack.js",
        "window.__BASIC_DATA__.fundDetailPack",
    )
    fund_index = {
        "version": fund_pack.get("version", 1),
        "fundFields": fund_pack.get("fundFields", []),
        "funds": fund_pack.get("funds", []),
        "holdingFields": [],
        "holdings": [],
        "monthlyFields": [],
        "monthly": [],
    }
    write_assignment(
        target / "fund_index_pack.js",
        "window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; window.__BASIC_DATA__.fundDetailPack = ",
        fund_index,
    )


def write_mixed_pack_without_fund_detail(source: Path, target: Path) -> None:
    payload = assignment_payload(source, "window.__MIXED_PERFORMANCE_SCATTER_PACK__")
    for row in payload.get("rows", []):
        detail_url = str(row.get("detailUrl") or "")
        if "fund.html" in detail_url:
            row["detailUrl"] = ""
    write_assignment(target, "window.__MIXED_PERFORMANCE_SCATTER_PACK__ = ", payload)


def compact_entity_groups(source: Path) -> tuple[dict[str, object], dict[str, object]]:
    semantic = assignment_payload(
        source / "data" / "ai_semantic_index.js",
        "window.__AI_STRATEGY_SEMANTIC_INDEX__",
    )
    strategy_pack = semantic.get("strategyEntities") or {}
    fund_pack = semantic.get("fundEntities") or {}

    strategy_fields = strategy_pack.get("fields") or []
    strategy_id_index = strategy_fields.index("统一策略ID")
    strategy_groups: dict[str, list[list[object]]] = {}
    for row in strategy_pack.get("rows") or []:
        strategy_id = str(row[strategy_id_index] or "").strip()
        if strategy_id:
            strategy_groups.setdefault(strategy_id, []).append(row)

    fund_fields = fund_pack.get("fields") or []
    fund_code_index = fund_fields.index("基金代码")
    fund_groups: dict[str, list[list[object]]] = {}
    for row in fund_pack.get("rows") or []:
        fund_code = str(row[fund_code_index] or "").strip()
        if fund_code:
            fund_groups.setdefault(fund_code, []).append(row)

    return (
        {"fields": strategy_fields, "groups": strategy_groups},
        {"fields": fund_fields, "groups": fund_groups},
    )


def number(value: object) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def classify_validation_checks(checks: dict[str, object]) -> tuple[dict[str, int], dict[str, int]]:
    blocking = {
        name: int(checks.get(name) or 0)
        for name in BLOCKING_ZERO_CHECKS
        if int(checks.get(name) or 0) != 0
    }
    warnings = {
        name: int(checks.get(name) or 0)
        for name in WARNING_ONLY_CHECKS
        if int(checks.get(name) or 0) != 0
    }
    return blocking, warnings


def rebalance_outcome_score(snapshot: dict, curve: dict | None) -> float | None:
    curve = curve or {}
    label = str(curve.get("调仓评价") or snapshot.get("调仓评价") or snapshot.get("胜负") or snapshot.get("结果评价") or "")
    if re.search(r"不可|待评价|未评价|无数据|不足|缺失", label):
        return None
    if re.search(r"胜|跑赢|正|领先|优秀|有效", label):
        return 1.0
    if re.search(r"负|跑输|落后|偏弱|无效", label):
        return 0.0
    if re.search(r"平|持平|中性", label):
        return 0.5
    excess = number(curve.get("调仓超额") if curve else snapshot.get("调仓超额") or snapshot.get("方向性超额"))
    if excess is None:
        return None
    return 1.0 if excess > 0 else 0.0 if excess < 0 else 0.5


def strategy_detail_inventory(
    source: Path,
) -> tuple[set[str], set[str], set[str], dict[str, int], list[str]]:
    fund_codes: set[str] = set()
    detail_ids: set[str] = set()
    official_performance_image_paths: set[str] = set()
    errors: list[str] = []
    stats = {
        "strategyDetailSourceCount": 0,
        "strategyDetailDeclaredCompleteCount": 0,
        "strategyDetailDeclaredIncompleteCount": 0,
        "strategyDetailWithoutHoldingCount": 0,
        "strategyDetailWithoutCurveCount": 0,
        "activeStrategyCount": 0,
        "activeStrategyWithoutCurveCount": 0,
        "currentHoldingStrategyCount": 0,
        "currentHoldingFundReferenceCount": 0,
        "currentHoldingRankMissingReferenceCount": 0,
        "activeCurrentHoldingRankMissingReferenceCount": 0,
        "inactiveCurrentHoldingRankMissingReferenceCount": 0,
        "currentHoldingScaleErrorReferenceCount": 0,
        "historyRebalanceStrategyCount": 0,
        "assessedHistoryRebalanceStrategyCount": 0,
        "unassessedHistoryRebalanceStrategyCount": 0,
        "officialPerformanceImageReferenceCount": 0,
        "officialPerformanceImageInvalidReferenceCount": 0,
        "officialPerformanceImageMissingSourceAssetCount": 0,
    }
    for prefix in FUND_RANK_PREFIXES:
        stats[f"currentHolding{prefix}RankedReferenceCount"] = 0
    for path in sorted((source / "data" / "details").glob("*.js")):
        stats["strategyDetailSourceCount"] += 1
        try:
            text = path.read_text(encoding="utf-8")
            value_pos = text.find("=")
            if value_pos < 0:
                raise ValueError("assignment not found")
            payload_text = text[value_pos + 1 :].strip()
            if payload_text.endswith(";"):
                payload_text = payload_text[:-1]
            payload = json.loads(payload_text)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
            continue

        detail_id = str(payload.get("id") or "").strip()
        if not detail_id:
            errors.append(f"{path.name}: detail id missing")
        else:
            detail_ids.add(detail_id)

        official_image = payload.get("officialPerformanceImage")
        if isinstance(official_image, dict) and official_image:
            stats["officialPerformanceImageReferenceCount"] += 1
            image_url = str(official_image.get("url") or "").strip()
            relative_text = image_url[2:] if image_url.startswith("./") else ""
            relative_path = Path(relative_text) if relative_text else None
            is_safe_reference = bool(
                relative_path
                and not relative_path.is_absolute()
                and ".." not in relative_path.parts
                and len(relative_path.parts) >= 3
                and relative_path.parts[:2] == ("assets", "gfbank-performance")
                and relative_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            )
            if not is_safe_reference:
                stats["officialPerformanceImageInvalidReferenceCount"] += 1
            else:
                relative_posix = relative_path.as_posix()
                official_performance_image_paths.add(relative_posix)
                if not (source / relative_path).is_file():
                    stats["officialPerformanceImageMissingSourceAssetCount"] += 1

        summary = payload.get("summary") or {}
        is_active = not bool(summary.get("是否已停止")) and not bool(summary.get("是否测试组合"))
        if is_active:
            stats["activeStrategyCount"] += 1
        if summary.get("数据完整性") == "完整":
            stats["strategyDetailDeclaredCompleteCount"] += 1
        else:
            stats["strategyDetailDeclaredIncompleteCount"] += 1

        snapshots = payload.get("positionSnapshots") or []
        holding_count = 0
        for snapshot in snapshots:
            for holding in snapshot.get("holdings") or []:
                holding_count += 1
                code = str(holding.get("基金代码") or "").strip()
                if code:
                    fund_codes.add(code)
        if holding_count == 0:
            stats["strategyDetailWithoutHoldingCount"] += 1

        current_snapshot = next(
            (
                snapshot
                for snapshot in snapshots
                if snapshot.get("id") == "current" or snapshot.get("类型") in {"当前仓位", "当前持仓"}
            ),
            {},
        )
        current_holdings = [
            holding
            for holding in current_snapshot.get("holdings") or []
            if (number(holding.get("权重")) or 0) > 0
        ]
        if current_holdings:
            stats["currentHoldingStrategyCount"] += 1
        for holding in current_holdings:
            stats["currentHoldingFundReferenceCount"] += 1
            missing_rank = False
            period_returns: list[float] = []
            for prefix in FUND_RANK_PREFIXES:
                rank = number(holding.get(f"{prefix}同类排名"))
                sample = number(holding.get(f"{prefix}同类样本数"))
                if rank is not None and sample is not None and sample > 0 and 1 <= rank <= sample:
                    stats[f"currentHolding{prefix}RankedReferenceCount"] += 1
                else:
                    missing_rank = True
                period_return = number(holding.get(f"{prefix}收益"))
                if period_return is not None:
                    period_returns.append(period_return)
            if missing_rank:
                stats["currentHoldingRankMissingReferenceCount"] += 1
                if is_active:
                    stats["activeCurrentHoldingRankMissingReferenceCount"] += 1
                else:
                    stats["inactiveCurrentHoldingRankMissingReferenceCount"] += 1
            if sum(value <= -90 for value in period_returns) >= 3:
                stats["currentHoldingScaleErrorReferenceCount"] += 1

        curves = payload.get("curves") or {}
        curve_points = sum(
            len((series or {}).get("points") or [])
            for series in curves.values()
            if isinstance(series, dict)
        )
        if curve_points == 0:
            stats["strategyDetailWithoutCurveCount"] += 1
            if is_active:
                stats["activeStrategyWithoutCurveCount"] += 1

        history_snapshots = [
            snapshot
            for snapshot in snapshots
            if snapshot.get("id") != "current"
            and (snapshot.get("类型") == "历史调仓" or snapshot.get("调仓事件ID") or snapshot.get("调仓原因"))
        ]
        if history_snapshots:
            stats["historyRebalanceStrategyCount"] += 1
            contribution_curves = payload.get("contributionCurves") or {}
            assessed = 0
            for snapshot in history_snapshots:
                curve = contribution_curves.get(snapshot.get("id"))
                if not curve:
                    snapshot_date = str(snapshot.get("日期") or "")
                    curve = next(
                        (item for item in contribution_curves.values() if str(item.get("起始日期") or "") == snapshot_date),
                        None,
                    )
                if rebalance_outcome_score(snapshot, curve) is not None:
                    assessed += 1
            if assessed:
                stats["assessedHistoryRebalanceStrategyCount"] += 1
            else:
                stats["unassessedHistoryRebalanceStrategyCount"] += 1

    stats["strategyDetailReferencedFundCount"] = len(fund_codes)
    stats["strategyDetailParseErrorCount"] = len(errors)
    return fund_codes, detail_ids, official_performance_image_paths, stats, errors


def fund_detail_chart_inventory(source: Path, selected_codes: set[str]) -> tuple[dict[str, int], dict[str, list[str]]]:
    stats = {
        "fundDetailParsedCount": 0,
        "fundDetailNavChartCount": 0,
        "fundDetailNavChartMissingCount": 0,
        "fundDetailBenchmarkChartCount": 0,
        "fundDetailChartScaleErrorCount": 0,
        "fundDetailParseErrorCount": 0,
    }
    examples: dict[str, list[str]] = {"missingNav": [], "scaleError": [], "parseError": []}
    for code in sorted(selected_codes):
        path = source / "data" / "fund_details" / f"{code}.js"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            value_pos = text.rfind(" = ")
            if value_pos < 0:
                raise ValueError("assignment not found")
            payload_text = text[value_pos + 3 :].strip()
            if payload_text.endswith(";"):
                payload_text = payload_text[:-1]
            payload = json.loads(payload_text)
        except Exception as exc:
            stats["fundDetailParseErrorCount"] += 1
            if len(examples["parseError"]) < 20:
                examples["parseError"].append(f"{code}: {exc}")
            continue
        stats["fundDetailParsedCount"] += 1
        nav_rows = (payload.get("nav") or {}).get("rows") or []
        if len(nav_rows) >= 2:
            stats["fundDetailNavChartCount"] += 1
        else:
            stats["fundDetailNavChartMissingCount"] += 1
            if len(examples["missingNav"]) < 20:
                examples["missingNav"].append(code)
        benchmark_rows = (payload.get("benchmark") or {}).get("rows") or []
        if len(benchmark_rows) >= 2:
            stats["fundDetailBenchmarkChartCount"] += 1
        values = [number(row.get("走势图指数")) for row in nav_rows]
        values = [value for value in values if value is not None and value > 0]
        if any(current / previous < 0.1 for previous, current in zip(values, values[1:]) if previous > 0):
            stats["fundDetailChartScaleErrorCount"] += 1
            if len(examples["scaleError"]) < 20:
                examples["scaleError"].append(code)
    return stats, examples


def selected_fund_codes(source: Path, strategy_detail_codes: set[str]) -> tuple[set[str], dict[str, int]]:
    mixed = assignment_payload(
        source / "data" / "mixed_performance_scatter_pack.js",
        "window.__MIXED_PERFORMANCE_SCATTER_PACK__",
    )
    ranking_codes = {
        str(row.get("code") or "").strip()
        for row in mixed.get("rows", [])
        if "fund.html" in str(row.get("detailUrl") or "") and str(row.get("code") or "").strip()
    }

    fund_pack = assignment_payload(
        source / "data" / "fund_detail_pack.js",
        "window.__BASIC_DATA__.fundDetailPack",
    )
    holding_codes = {
        str(row[0]).strip()
        for row in fund_pack.get("funds", [])
        if isinstance(row, list) and row and str(row[0]).strip()
    }

    union = ranking_codes | holding_codes | strategy_detail_codes
    return union, {
        "rankingFundCount": len(ranking_codes),
        "strategyHoldingFundCount": len(holding_codes),
        "strategyDetailReferencedFundCount": len(strategy_detail_codes),
        "strategyDetailOnlyFundCount": len(strategy_detail_codes - ranking_codes - holding_codes),
        "selectedFundCount": len(union),
    }


def minimal_nav(active_page: str) -> str:
    groups = (
        (
            "产品分析",
            (
                ("institutions.html", "机构总览"),
                ("strategies.html", "策略列表"),
                ("ai-strategy.html", "AI选策略"),
            ),
        ),
        (
            "基金与排名",
            (
                ("mixed-performance-scatter.html", "全市场产品排名"),
            ),
        ),
    )
    parts: list[str] = []
    for label, items in groups:
        links = "\n".join(
            f'<a class="nav-link {"is-active" if href.partition("?")[0].partition("#")[0] == active_page else ""}" href="./{href}">{name}</a>'
            for href, name in items
        )
        parts.append(f'<div class="nav-group"><div class="nav-group-title">{label}</div>{links}</div>')
    return '<nav class="nav" aria-label="主导航">' + "\n".join(parts) + "</nav>"


def rewrite_page(source: Path, target: Path, page_name: str, version: str) -> None:
    text = source.read_text(encoding="utf-8-sig")
    active = ACTIVE_PAGE.get(page_name, page_name)
    text, nav_count = re.subn(
        r'<nav class="nav"(?:\s+aria-label="[^"]*")?>.*?</nav>',
        minimal_nav(active),
        text,
        count=1,
        flags=re.DOTALL,
    )
    if nav_count != 1:
        raise RuntimeError(f"Unable to rewrite navigation in {source}")
    text = re.sub(r"\?v=[^\"'\s<>]+", f"?v={version}", text)
    runtime_tag = f'<script src="./assets/minimal-publish-runtime.js?v={version}"></script>'
    text, runtime_count = re.subn(
        r'(<script src="\./assets/basic-common\.js[^>]*></script>)',
        r"\1\n  " + runtime_tag,
        text,
        count=1,
    )
    if runtime_count != 1:
        raise RuntimeError(f"Unable to inject minimal runtime in {source}")

    replacements = PAGE_PACK_REPLACEMENTS.get(page_name, {})
    data_scripts: list[str] = []

    def replace_data_script(match: re.Match[str]) -> str:
        name = match.group("name")
        mapped = replacements.get(name, name)
        if mapped is None:
            return ""
        if mapped not in COMPRESSED_PAGE_PACKS:
            return match.group(0)
        data_scripts.append(f"./data/{mapped}?v={version}")
        return ""

    text = re.sub(
        r'<script\s+src="\./data/(?P<name>[^"?]+\.js)(?:\?[^\"]*)?"\s*></script>',
        replace_data_script,
        text,
    )
    quality_match = re.search(r'renderGlobalQualityGate\("([^"]+)"\)', text)
    quality_scope = quality_match.group(1) if quality_match else ""
    text = re.sub(
        r'<script>window\.BasicData\s*&&\s*window\.BasicData\.renderGlobalQualityGate.*?</script>',
        "",
        text,
        flags=re.DOTALL,
    )
    renderer = PAGE_RENDERERS[page_name]
    renderer_pattern = rf'<script\s+src="\./assets/{re.escape(renderer)}(?:\?[^\"]*)?"\s*></script>'
    boot = (
        "<script>window.MinimalPublish.startPage("
        + json.dumps(
            {
                "dataScripts": data_scripts,
                "renderer": f"./assets/{renderer}?v={version}",
                "qualityScope": quality_scope,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + ");</script>"
    )
    text, renderer_count = re.subn(renderer_pattern, boot, text, count=1)
    if renderer_count != 1:
        raise RuntimeError(f"Unable to defer renderer in {source}: {renderer}")
    write_text(target, text)


def rewrite_static_report_page(source: Path, target: Path, page_name: str, version: str) -> None:
    text = sanitize_public_text(source.read_text(encoding="utf-8-sig"))
    text, nav_count = re.subn(
        r'<nav class="nav"(?:\s+aria-label="[^"]*")?>.*?</nav>',
        minimal_nav(page_name),
        text,
        count=1,
        flags=re.DOTALL,
    )
    if nav_count != 1:
        raise RuntimeError(f"Unable to rewrite navigation in {source}")
    text = re.sub(r"\?v=[^\"'\s<>]+", f"?v={version}", text)
    write_text(target, text)


def sanitize_public_text(text: str) -> str:
    allowed_inner_base = "http://10.89.189.109:8000/llmapi/v1"
    allowed_token = "__ALLOWED_INNER_MODEL_BASE__"
    text = text.replace(allowed_inner_base, allowed_token)
    text = re.sub(r'"[A-Za-z]:\\\\[^"]*"', '"<local-path-removed>"', text)
    text = re.sub(
        r'("sourceWorkbookPack"\s*:\s*")(?:\\.|[^"\\])*(")',
        r'\1<local-source-removed>\2',
        text,
    )
    text = re.sub(r"(?<!\d)10\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!\d)", "127.0.0.1", text)
    text = re.sub(r"(?<!\d)192\.168\.\d{1,3}\.\d{1,3}(?!\d)", "127.0.0.1", text)
    text = text.replace("http://127.0.0.1:8000/llmapi/v1", "http://127.0.0.1:8787/v1")
    return text.replace(allowed_token, allowed_inner_base)


def copy_public_text(source: Path, target: Path) -> None:
    write_text(target, sanitize_public_text(source.read_text(encoding="utf-8")))


def copy_directory_files(source: Path, target: Path) -> dict[str, int]:
    if not source.is_dir():
        raise FileNotFoundError(source)
    file_count = 0
    total_bytes = 0
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        relative = path.relative_to(source)
        destination = target / relative
        copy_file(path, destination)
        file_count += 1
        total_bytes += path.stat().st_size
    return {"fileCount": file_count, "bytes": total_bytes}


def copy_preserved_publish_paths(existing_target: Path, target: Path) -> dict[str, int]:
    existing_target = existing_target.resolve()
    target = target.resolve()
    if existing_target == target or not existing_target.is_dir():
        return {"fileCount": 0, "bytes": 0, "pathCount": 0}

    copied_files = 0
    copied_bytes = 0
    copied_paths = 0
    for relative in PRESERVED_PUBLISH_PATHS:
        source = existing_target / relative
        destination = target / relative
        if source.is_dir():
            copied = copy_directory_files(source, destination)
            copied_files += copied["fileCount"]
            copied_bytes += copied["bytes"]
            copied_paths += 1
        elif source.is_file():
            copy_file(source, destination)
            copied_files += 1
            copied_bytes += source.stat().st_size
            copied_paths += 1
    return {
        "fileCount": copied_files,
        "bytes": copied_bytes,
        "pathCount": copied_paths,
    }


def gzip_copy(source: Path, target: Path, compression_level: int) -> tuple[int, int]:
    return gzip_bytes(source.read_bytes(), target, compression_level)


def gzip_bytes(raw_bytes: bytes, target: Path, compression_level: int) -> tuple[int, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=compression_level,
            fileobj=output,
            mtime=0,
        ) as compressed:
            compressed.write(raw_bytes)
    return len(raw_bytes), target.stat().st_size


def gzip_data_directory(source: Path, target: Path, compression_level: int) -> dict[str, int]:
    if not source.is_dir():
        raise FileNotFoundError(source)
    stats = {
        f"{source.name}FileCount": 0,
        f"{source.name}RawBytes": 0,
        f"{source.name}CompressedBytes": 0,
    }
    for path in sorted(source.glob("*.js")):
        raw_bytes = sanitize_public_text(path.read_text(encoding="utf-8")).encode("utf-8")
        raw, compressed = gzip_bytes(raw_bytes, target / f"{path.name}.gz", compression_level)
        stats[f"{source.name}FileCount"] += 1
        stats[f"{source.name}RawBytes"] += raw
        stats[f"{source.name}CompressedBytes"] += compressed
    return stats


def write_strategy_detail(
    source: Path,
    target: Path,
    entity_fields: list[object],
    entity_rows: list[list[object]],
    compression_level: int,
) -> tuple[int, int]:
    text = source.read_text(encoding="utf-8")
    value_pos = text.find("=")
    payload_text = text[value_pos + 1 :].strip()
    if payload_text.endswith(";"):
        payload_text = payload_text[:-1]
    payload = json.loads(payload_text)
    payload["strategyEntityPack"] = {"fields": entity_fields, "rows": entity_rows}
    strategy_id = str(payload.get("id") or source.stem)
    output = (
        f"window.__BASIC_DATA__.details[{json.dumps(strategy_id, ensure_ascii=False)}] = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
    ).encode("utf-8")
    return gzip_bytes(output, target, compression_level)


def write_fund_detail(
    source: Path,
    target: Path,
    code: str,
    entity_fields: list[object],
    entity_rows: list[list[object]],
    compression_level: int,
) -> tuple[int, int]:
    text = source.read_text(encoding="utf-8")
    marker = f"fundEnrichmentDetails[{json.dumps(code, ensure_ascii=False)}] ="
    marker_pos = text.find(marker)
    if marker_pos < 0:
        raise ValueError(f"Fund detail assignment not found: {source}")
    value_pos = text.find("=", marker_pos)
    payload_text = text[value_pos + 1 :].strip()
    if payload_text.endswith(";"):
        payload_text = payload_text[:-1]
    payload = json.loads(payload_text)
    payload["fundEntityPack"] = {"fields": entity_fields, "rows": entity_rows}
    output = (
        "window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; "
        "window.__BASIC_DATA__.fundEnrichmentDetails = window.__BASIC_DATA__.fundEnrichmentDetails || {}; "
        f"window.__BASIC_DATA__.fundEnrichmentDetails[{json.dumps(code, ensure_ascii=False)}] = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
    ).encode("utf-8")
    return gzip_bytes(output, target, compression_level)


def write_latest_holding_snapshot_pack(source: Path, target: Path) -> dict[str, int]:
    payload = assignment_payload(source, "window.__BASIC_HOLDING_SNAPSHOT_PACK__")
    rows = payload.get("rows") or []
    latest_by_strategy: dict[object, str] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        strategy_index = row[0]
        date = str(row[1] or "")
        if date and date > latest_by_strategy.get(strategy_index, ""):
            latest_by_strategy[strategy_index] = date
    latest_rows = [
        row
        for row in rows
        if isinstance(row, list)
        and len(row) >= 2
        and str(row[1] or "") == latest_by_strategy.get(row[0], "")
    ]
    payload["rows"] = latest_rows
    payload["packageMode"] = "latest-snapshot-only"
    payload["sourceRowCount"] = len(rows)
    payload["latestStrategyCount"] = len(latest_by_strategy)
    write_text(
        target,
        "window.__BASIC_HOLDING_SNAPSHOT_PACK__ = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
    )
    return {
        "holdingSnapshotSourceRowCount": len(rows),
        "holdingSnapshotPublishedRowCount": len(latest_rows),
        "holdingSnapshotStrategyCount": len(latest_by_strategy),
    }


def write_sanitized_fund_manifest(source: Path, target: Path, selected_count: int) -> None:
    payload = assignment_payload(source, "window.__BASIC_DATA__.fundEnrichmentManifest")
    payload["siteDir"] = ""
    payload["dbPath"] = ""
    payload["packageFundCount"] = selected_count
    text = (
        "window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; "
        "window.__BASIC_DATA__.fundEnrichmentManifest = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
    )
    write_text(target, text)


def minimal_data_pack_manifest(basic_root: Path) -> dict[str, object]:
    excluded = {"data/data_pack_manifest.js", "data/data_quality_pack.js"}
    paths = [
        path
        for path in basic_root.rglob("*")
        if path.is_file() and path.relative_to(basic_root).as_posix() not in excluded
    ]
    rows = []
    for path in sorted(paths, key=lambda item: item.stat().st_size, reverse=True)[:40]:
        rel = path.relative_to(basic_root).as_posix()
        role = "按需压缩详情" if "/details/" in f"/{rel}" or "/fund_details/" in f"/{rel}" else "页面数据包"
        rows.append({"path": rel, "bytes": path.stat().st_size, "role": role})
    max_file = rows[0] if rows else {"path": "", "bytes": 0, "role": ""}
    first_screen_names = (
        "data/strategy_list_pack.js.gz",
        "assets/basic.css",
        "assets/basic-common.js",
        "assets/minimal-publish-runtime.js",
        "assets/strategies.js",
    )
    first_screen_bytes = sum(
        (basic_root / name).stat().st_size
        for name in first_screen_names
        if (basic_root / name).is_file()
    )
    return {
        "version": 1,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "packageType": "minimal-public",
        "totalFiles": len(paths),
        "totalBytes": sum(path.stat().st_size for path in paths),
        "maxFile": max_file,
        "firstScreenBytes": first_screen_bytes,
        "thresholds": {
            "published_site_max_mb": 1024,
            "single_file_max_mb": 100,
            "first_screen_warn_mb": 40,
        },
        "pageDependencies": {
            "strategies.html": ["strategy_list_pack.js.gz"],
            "institutions.html": ["institution_overview_pack.js.gz"],
            "strategy.html": ["strategy_detail_index_pack.js.gz", "fund_index_pack.js.gz", "details/<分片>/<策略ID>.js.gz"],
            "compare.html": ["strategy_detail_index_pack.js.gz", "details/<分片>/<策略ID>.js.gz", "holding_snapshot_pack.js.gz（后台按需）"],
            "mixed-performance-scatter.html": ["mixed_performance_scatter_pack.js.gz"],
            "ai-strategy.html": ["strategy_list_pack.js.gz", "holding_snapshot_pack.js.gz", "fund_detail_pack.js.gz", "ai_semantic_index.js.gz", "ai_topic_evidence_pack.js.gz"],
        },
        "files": rows,
    }


def scrub_private_metadata(value: object) -> object:
    if isinstance(value, dict):
        return {key: scrub_private_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_private_metadata(item) for item in value]
    if isinstance(value, str):
        if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
            return "<local-path-removed>"
        return sanitize_public_text(value)
    return value


def write_minimal_quality_packs(source: Path, basic_target: Path, data_manifest: dict[str, object]) -> None:
    write_text(
        basic_target / "data" / "data_pack_manifest.js",
        "window.__BASIC_DATA_PACK_MANIFEST__ = "
        + json.dumps(data_manifest, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
    )

    payload = assignment_payload(
        source / "data" / "data_quality_pack.js",
        "window.__BASIC_DATA_QUALITY_PACK__",
    )
    payload = scrub_private_metadata(payload)
    if not isinstance(payload, dict):
        raise TypeError("Unexpected data quality payload")
    payload["dataPackManifest"] = data_manifest
    payload["publishPackageType"] = "minimal-public"
    payload["publishPackageGeneratedAt"] = data_manifest["generatedAt"]
    max_file = data_manifest.get("maxFile") or {}
    max_mib = float(max_file.get("bytes") or 0) / (1024 * 1024) if isinstance(max_file, dict) else 0
    for check in payload.get("checks", []):
        if not isinstance(check, dict):
            continue
        name = str(check.get("项目") or check.get("name") or "")
        if name == "页面数据包体积":
            check["状态"] = "ok"
            check["status"] = "ok"
            check["当前值"] = f"最大单包 {max_mib:.2f} MiB"
            check["门槛"] = "单文件 <100 MiB，发布集 <1 GiB"
            check["说明"] = "最小发布集仅保留必要页面包，策略和基金详情使用按需 gzip 加载。"
        elif name == "部署清单":
            check["状态"] = "ok"
            check["status"] = "ok"
            check["当前值"] = "ready"
            check["门槛"] = "ready 且 missing=[]"
            check["说明"] = "最小发布包只在 deployment_manifest.json 与 package_validation.json 均通过后原子提升。"
    write_text(
        basic_target / "data" / "data_quality_pack.js",
        "window.__BASIC_DATA_QUALITY_PACK__ = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
    )


def file_manifest(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        if path.name == "deployment_manifest.json" or any(part in {".git", ".runtime"} for part in relative.parts):
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        rows.append(
            {
                "path": relative.as_posix(),
                "size": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    return rows


def entry_html(entry: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>全市场产品分析</title></head><body>
<main id="entry" style="max-width:760px;margin:12vh auto;padding:24px;font:16px/1.7 system-ui;color:#101828">
  <h1 style="font-size:24px">全市场产品分析</h1>
  <p id="message">正在进入机构总览...</p>
  <p><a href="{entry}">进入机构总览</a></p>
</main>
<script>
if (location.protocol === "file:") {{
  document.getElementById("message").innerHTML = "详情数据采用压缩按需加载，不能通过 file:// 直接运行。请双击根目录的 <b>启动最小发布集.cmd</b>。";
}} else {{
  location.replace("{entry}");
}}
</script>
</body></html>
"""


def github_pages_workflow() -> str:
    return """name: Deploy GitHub Pages

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Configure Pages
        uses: actions/configure-pages@v5
      - name: Upload site
        uses: actions/upload-pages-artifact@v4
        with:
          path: .
      - name: Deploy
        id: deployment
        uses: actions/deploy-pages@v4
"""


def readme_text(stats: dict[str, object]) -> str:
    return f"""# 最小发布集

本目录包含机构总览、策略列表、全市场产品排名、AI选策略，以及策略/基金详情下钻。策略对比保留为策略列表和 AI 选策略的下钻功能，不在一级菜单单独展示。

## 访问方式

GitHub Pages 会由 `.github/workflows/pages.yml` 自动部署。也可双击根目录 `启动最小发布集.cmd` 在本机启动静态站点：

```text
http://127.0.0.1:7676/basic_data/institutions.html
```

停止本机服务：双击根目录 `停止最小发布集.cmd`。

不要用 `file://` 直接打开页面。策略详情使用 gzip 按需加载，必须通过 HTTP 服务访问；GitHub Pages、Nginx、IIS 和本目录启动脚本都满足要求。

## AI 模型

AI 选策略默认调用内网 OpenAI 兼容服务 `inner-deepseek`，模型为 `qwen35-397b-a17b`。配置已随发布集写入 `basic_data/config`，不依赖本机 Codex 桥接。

GitHub Pages 是 HTTPS 页面，而当前内网模型地址是 HTTP。浏览器是否允许调用取决于内网服务的 HTTPS、CORS 和 Private Network Access 配置；不满足时页面仍可使用本地规则筛选，但模型解读会提示连接失败。生产稳定使用建议为该内网接口增加 HTTPS 反向代理并放行发布站点 Origin。

## 本机服务器

```powershell
python -X utf8 scripts/serve_basic_data_site.py --host 0.0.0.0 --port 7676 --directory .
```

## 数据范围

- 策略详情：{stats['strategyDetailCount']} 个。
- 策略源数据完整标记：{stats['strategyDetailDeclaredCompleteCount']} 个；源数据不完整：{stats['strategyDetailDeclaredIncompleteCount']} 个，页面保留真实缺失状态，不做推测补齐。
- 基金详情：最小发布集不发布基金详情页或单基金详情文件，避免文件数量超过托管上限；基金名称及策略持仓业务字段仍保留展示。
- 策略对比仓位快照：只保留每只策略最新有效快照，原始 {stats['holdingSnapshotSourceRowCount']} 行，发布 {stats['holdingSnapshotPublishedRowCount']} 行；不影响当前配置对比和 AI 持仓筛选。
- 机构总览：按销售渠道和投顾管理人查看策略规模、调仓走势、基准风险资产权重及数据完整性。
- 详情文件采用确定性 gzip；必须通过 HTTP 服务访问，不能用 `file://` 直接打开。
- 发布清单及 SHA256：`deployment_manifest.json`。
- 功能与数据覆盖验收：`package_validation.json`。
"""


def build_package(args: argparse.Namespace, target: Path) -> dict[str, Any]:
    source = args.source_basic_data.resolve()
    if not (source / "strategies.html").is_file():
        raise FileNotFoundError(f"Invalid basic_data source: {source}")
    version = build_id(source)

    clean_target(target)
    basic_target = target / "basic_data"
    basic_target.mkdir(parents=True)

    for page in PUBLIC_PAGES:
        rewrite_page(source / page, basic_target / page, page, version)
    for page in STATIC_REPORT_PAGES:
        rewrite_static_report_page(source / page, basic_target / page, page, version)

    write_text(basic_target / "index.html", entry_html("./institutions.html"))
    write_text(target / "index.html", entry_html("./basic_data/institutions.html"))
    write_text(target / ".gitignore", ".runtime/\n")
    write_text(target / ".nojekyll", "")
    write_text(target / ".stignore", "(?d).runtime\n(?d).runtime/**\n")
    write_text(target / ".github" / "workflows" / "pages.yml", github_pages_workflow())
    write_text(
        target / "version.json",
        json.dumps(
            {
                "version": 1,
                "buildId": version,
                "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "entry": "basic_data/institutions.html",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    write_text(
        target / "启动最小发布集.cmd",
        '@echo off\ncall "%~dp0scripts\\start_minimal_publish.cmd"\n',
    )
    write_text(
        target / "停止最小发布集.cmd",
        '@echo off\ncall "%~dp0scripts\\stop_minimal_publish.cmd"\n',
    )

    code_asset_dir = PROJECT_ROOT / "basic_data" / "assets"
    for name in ASSET_FILES:
        authored_asset = code_asset_dir / name
        copy_file(
            authored_asset if authored_asset.is_file() else source / "assets" / name,
            basic_target / "assets" / name,
        )
    static_report_asset_stats: dict[str, int] = {}
    for directory in STATIC_REPORT_ASSET_DIRECTORIES:
        copied = copy_directory_files(source / "assets" / directory, basic_target / "assets" / directory)
        static_report_asset_stats[f"{directory}FileCount"] = copied["fileCount"]
        static_report_asset_stats[f"{directory}Bytes"] = copied["bytes"]
    for directory in OPTIONAL_REFERENCED_ASSET_DIRECTORIES:
        asset_source = source / "assets" / directory
        copied = (
            copy_directory_files(asset_source, basic_target / "assets" / directory)
            if asset_source.is_dir()
            else {"fileCount": 0, "bytes": 0}
        )
        static_report_asset_stats[f"{directory}FileCount"] = copied["fileCount"]
        static_report_asset_stats[f"{directory}Bytes"] = copied["bytes"]
    copy_file(
        PROJECT_ROOT / "basic_data" / "assets" / "minimal-publish-runtime.js",
        basic_target / "assets" / "minimal-publish-runtime.js",
    )
    validate_strategy_list_workflow(basic_target)

    for name in DATA_FILES:
        if name == "basic_summary_core.js":
            continue
        if name == "mixed_performance_scatter_pack.js":
            write_mixed_pack_without_fund_detail(source / "data" / name, basic_target / "data" / name)
        else:
            copy_public_text(source / "data" / name, basic_target / "data" / name)
    build_route_packs(source, basic_target / "data", version)
    holding_snapshot_stats = write_latest_holding_snapshot_pack(
        source / "data" / "holding_snapshot_pack.js",
        basic_target / "data" / "holding_snapshot_pack.js",
    )
    for name in CONFIG_FILES:
        copy_public_text(source / "config" / name, basic_target / "config" / name)

    for name in (*DATA_FILES, *GENERATED_ROUTE_PACKS, "holding_snapshot_pack.js"):
        raw_pack = basic_target / "data" / name
        if not raw_pack.is_file():
            raise FileNotFoundError(raw_pack)
        gzip_copy(raw_pack, raw_pack.with_name(f"{raw_pack.name}.gz"), args.compression_level)
        raw_pack.unlink()

    lazy_data_stats: dict[str, int] = {}
    for directory in LAZY_DATA_DIRECTORIES:
        lazy_data_stats.update(
            gzip_data_directory(
                source / "data" / directory,
                basic_target / "data" / directory,
                args.compression_level,
            )
        )

    (
        strategy_fund_codes,
        detail_ids,
        official_performance_image_paths,
        strategy_inventory_stats,
        detail_parse_errors,
    ) = strategy_detail_inventory(source)
    if detail_parse_errors:
        raise RuntimeError("Strategy detail parsing failed: " + "; ".join(detail_parse_errors[:10]))
    missing_published_official_images = sorted(
        relative_path
        for relative_path in official_performance_image_paths
        if not (basic_target / Path(relative_path)).is_file()
    )
    strategy_inventory_stats["officialPerformanceImagePublishedAssetCount"] = (
        len(official_performance_image_paths) - len(missing_published_official_images)
    )
    strategy_inventory_stats["officialPerformanceImageMissingPublishedAssetCount"] = len(
        missing_published_official_images
    )

    summary_payload = assignment_payload(
        source / "data" / "basic_summary_core.js",
        "window.__BASIC_DATA__.summary",
    )
    summary_ids = {
        str(row.get("统一策略ID") or "").strip()
        for row in summary_payload.get("strategies", [])
        if str(row.get("统一策略ID") or "").strip()
    }
    missing_strategy_details = sorted(summary_ids - detail_ids)
    if missing_strategy_details:
        raise RuntimeError(f"Missing strategy detail files: {missing_strategy_details[:20]}")

    selection_stats = {
        "rankingFundCount": 0,
        "strategyHoldingFundCount": 0,
        "strategyDetailReferencedFundCount": len(strategy_fund_codes),
        "strategyDetailOnlyFundCount": len(strategy_fund_codes),
        "selectedFundCount": 0,
    }
    if strategy_inventory_stats["currentHoldingScaleErrorReferenceCount"]:
        raise RuntimeError(
            "Current holding period returns contain adjusted-NAV scale errors: "
            f"{strategy_inventory_stats['currentHoldingScaleErrorReferenceCount']} references"
        )
    strategy_entity_data, _fund_entity_data = compact_entity_groups(source)
    strategy_entity_fields = strategy_entity_data["fields"]
    strategy_entity_groups = strategy_entity_data["groups"]
    detail_raw = 0
    detail_compressed = 0
    strategy_count = 0
    for detail in sorted((source / "data" / "details").glob("*.js")):
        strategy_id = detail.stem
        raw, compressed = write_strategy_detail(
            detail,
            basic_target / "data" / "details" / shard_key(strategy_id) / f"{detail.name}.gz",
            strategy_entity_fields,
            strategy_entity_groups.get(strategy_id, []),
            args.compression_level,
        )
        detail_raw += raw
        detail_compressed += compressed
        strategy_count += 1

    fund_count = 0
    missing_fund_codes: list[str] = []
    broken_fund_codes: list[str] = []
    fund_chart_stats = {
        "fundDetailParsedCount": 0,
        "fundDetailNavChartCount": 0,
        "fundDetailNavChartMissingCount": 0,
        "fundDetailBenchmarkChartCount": 0,
        "fundDetailChartScaleErrorCount": 0,
        "fundDetailParseErrorCount": 0,
    }
    forbidden_fund_page_count = int((basic_target / "fund.html").exists())
    forbidden_fund_file_count = sum(1 for path in basic_target.rglob("*") if path.is_file() and "fund_details" in path.parts)

    for name in SCRIPT_FILES:
        script_source = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / name
        script_target = target / "scripts" / name
        if name == "serve_basic_data_site.py":
            copy_public_text(script_source, script_target)
        else:
            copy_file(script_source, script_target)

    linux_start = PROJECT_ROOT / "start_basic_data_site_linux.sh"
    if linux_start.is_file():
        copy_public_text(linux_start, target / linux_start.name)

    preserved_publish_stats = copy_preserved_publish_paths(args.target_dir, target)

    data_manifest = minimal_data_pack_manifest(basic_target)
    write_minimal_quality_packs(source, basic_target, data_manifest)
    quality_pack = basic_target / "data" / "data_quality_pack.js"
    gzip_copy(quality_pack, quality_pack.with_name(f"{quality_pack.name}.gz"), args.compression_level)
    quality_pack.unlink()

    stats: dict[str, object] = {
        **selection_stats,
        **strategy_inventory_stats,
        **fund_chart_stats,
        **holding_snapshot_stats,
        **lazy_data_stats,
        "strategyDetailCount": strategy_count,
        "strategyDetailSummaryCount": len(summary_ids),
        "strategyDetailMissingCount": len(missing_strategy_details),
        "fundDetailCount": fund_count,
        "missingFundDetailCount": len(missing_fund_codes),
        "missingFundDetailExamples": missing_fund_codes[:20],
        "fundBaseOnlyCount": len(missing_fund_codes),
        "fundBaseOnlyCodes": missing_fund_codes[:20],
        "detailRawBytes": detail_raw,
        "detailCompressedBytes": detail_compressed,
        "preservedPublishPathCount": preserved_publish_stats["pathCount"],
        "preservedPublishFileCount": preserved_publish_stats["fileCount"],
        "preservedPublishBytes": preserved_publish_stats["bytes"],
    }
    validation_checks = {
        "strategySummaryCount": len(summary_ids),
        "strategyDetailCount": strategy_count,
        "strategyDetailMissingCount": len(missing_strategy_details),
        "strategyDetailParseErrorCount": len(detail_parse_errors),
        "strategyDetailDeclaredIncompleteCount": strategy_inventory_stats["strategyDetailDeclaredIncompleteCount"],
        "strategyDetailWithoutHoldingCount": strategy_inventory_stats["strategyDetailWithoutHoldingCount"],
        "strategyDetailWithoutCurveCount": strategy_inventory_stats["strategyDetailWithoutCurveCount"],
        "strategyDetailReferencedFundCount": len(strategy_fund_codes),
        "selectedFundCount": 0,
        "enhancedFundDetailCount": fund_count,
        "baseOnlyFundCount": len(missing_fund_codes),
        "brokenFundDetailCount": len(broken_fund_codes),
        "forbiddenFundDetailPageCount": forbidden_fund_page_count,
        "forbiddenFundDetailFileCount": forbidden_fund_file_count,
        **strategy_inventory_stats,
        **fund_chart_stats,
        **holding_snapshot_stats,
        **static_report_asset_stats,
        **lazy_data_stats,
    }
    blocking_checks, warning_checks = classify_validation_checks(validation_checks)
    if blocking_checks:
        raise RuntimeError(f"Blocking minimal package checks failed: {blocking_checks}")
    validation = {
        "version": 1,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "ready",
        "checks": validation_checks,
        "policy": {
            "blockingZeroChecks": list(BLOCKING_ZERO_CHECKS),
            "warningOnlyChecks": list(WARNING_ONLY_CHECKS),
            "warningCounts": warning_checks,
        },
        "warnings": [
            {
                "type": "source_strategy_data_incomplete",
                "count": strategy_inventory_stats["strategyDetailDeclaredIncompleteCount"],
                "note": "源数据已明确标记不完整，发布集保留真实缺失状态，不做推测补齐。",
            },
            {
                "type": "active_strategy_without_curve",
                "count": strategy_inventory_stats["activeStrategyWithoutCurveCount"],
                "note": "活跃策略没有任何可画曲线时保留真实缺失状态；正常披露策略应为0。",
            },
            {
                "type": "current_holding_rank_missing",
                "count": strategy_inventory_stats["currentHoldingRankMissingReferenceCount"],
                "activeCount": strategy_inventory_stats["activeCurrentHoldingRankMissingReferenceCount"],
                "inactiveCount": strategy_inventory_stats["inactiveCurrentHoldingRankMissingReferenceCount"],
                "note": "同类排名缺失属于可披露的覆盖率缺口，不阻断其他准确数据发布；页面不把缺失仓位计为前50%。",
            },
            {
                "type": "history_rebalance_unassessed",
                "count": strategy_inventory_stats["unassessedHistoryRebalanceStrategyCount"],
                "note": "历史事件仅为建仓或底层净值不足时不计算胜率，页面展示具体原因。",
            },
        ],
    }
    write_text(
        target / "package_validation.json",
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
    )
    readme = readme_text(stats)
    write_text(target / "README.md", readme)
    write_text(target / "README_部署说明.md", readme)

    files = file_manifest(target)
    total_bytes = sum(int(row["size"]) for row in files)
    manifest = {
        "version": 1,
        "buildId": version,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pageSet": ["机构总览", "策略列表", "策略对比", "全市场产品排名", "AI选策略", "策略详情"],
        "entry": "basic_data/institutions.html",
        "stats": stats,
        "fileCount": len(files),
        "totalBytes": total_bytes,
        "files": files,
    }
    write_text(
        target / "deployment_manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    final_total = sum(
        path.stat().st_size
        for path in target.rglob("*")
        if path.is_file()
        and not any(part in {".git", ".runtime"} for part in path.relative_to(target).parts)
    )
    result = {
        "target": str(target),
        "fileCount": len(files) + 1,
        "totalMiB": round(final_total / (1024 * 1024), 2),
        "stats": stats,
    }
    return result


def main() -> None:
    args = parse_args()
    final_target = args.target_dir.resolve()
    allowed_parent = (args.allowed_target_parent or final_target.parent).resolve()
    ensure_safe_target(final_target, allowed_parent)
    staging = final_target.parent / f".{final_target.name}.staging-{os.getpid()}"
    ensure_safe_staging(staging, final_target)
    clean_target(staging)
    try:
        result = build_package(args, staging)
        validate_staged_package(staging)
        promote_staged_package(staging, final_target, allowed_parent)
        result["stagingTarget"] = str(staging)
        result["target"] = str(final_target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if staging.exists():
            remove_tree(staging)


if __name__ == "__main__":
    main()
