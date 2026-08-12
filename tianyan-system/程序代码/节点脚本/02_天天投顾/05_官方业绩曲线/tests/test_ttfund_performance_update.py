from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "python_src"))
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

from advisor_monitor.collectors.ttfund_loggedin import TTFundLoggedInCollector
from collect_ttfund_official_performance_curve import (
    business_day_lag,
    classify_quality_scope,
    curve_gap_type,
    disclosure_date_at_coverage,
    load_strategies,
    merge_daily_rows,
)


def test_quote_uses_net_value_date_before_source_date() -> None:
    collector = TTFundLoggedInCollector.__new__(TTFundLoggedInCollector)
    collector.run_id = "test-run"
    row = collector.build_daily_row(
        "IQVTUUF",
        {
            "row": {
                "SYRQ": "2026-07-15",
                "JZRQ": "2026-07-16",
                "SYL_D": "-0.52",
                "SYL_LN": "13.07",
            },
            "source_snapshot_id": "quote-snapshot",
        },
        {},
    )

    assert row is not None
    assert row["trade_date"] == "2026-07-16"
    assert row["cumulative_return"] == 13.07
    assert row["nav"] == 1.1307


def test_official_curve_overrides_quote_and_preserves_missing_fields() -> None:
    quote_rows = [
        {
            "source_strategy_id": "IQVTUUF",
            "trade_date": "2026-07-16",
            "nav": 1.1307,
            "cumulative_return": 13.07,
            "benchmark_return": None,
            "source_type": "public_quote",
        }
    ]
    official_rows = [
        {
            "source_strategy_id": "IQVTUUF",
            "trade_date": "2026-07-16",
            "nav": 1.1307,
            "cumulative_return": 13.07,
            "benchmark_return": -18.83,
            "source_type": "official_app_curve",
        }
    ]

    merged = merge_daily_rows(quote_rows, official_rows)

    assert len(merged) == 1
    assert merged[0]["benchmark_return"] == -18.83
    assert merged[0]["source_type"] == "official_app_curve"


def test_official_curve_auto_incremental_range_and_overlap() -> None:
    from collect_ttfund_official_performance_curve import choose_range_code, filter_incremental_rows

    assert choose_range_code(
        configured_range="ln",
        auto_incremental=True,
        local_latest_date="2026-07-15",
        expected_latest_date="2026-07-16",
        full_history_gap_days=4,
    ) == "ln"
    assert choose_range_code(
        configured_range="ln",
        auto_incremental=True,
        local_latest_date="2026-07-08",
        expected_latest_date="2026-07-16",
        full_history_gap_days=4,
    ) == "ln"
    rows = [{"trade_date": "2026-07-10"}, {"trade_date": "2026-07-14"}, {"trade_date": "2026-07-16"}]
    assert filter_incremental_rows(
        rows,
        local_latest_date="2026-07-15",
        overlap_days=3,
    ) == rows[1:]


def test_rebased_latest_point_does_not_override_nonzero_quote() -> None:
    quote_rows = [
        {
            "source_strategy_id": "IQVTUUF",
            "trade_date": "2026-07-16",
            "nav": 1.1307,
            "cumulative_return": 13.07,
            "benchmark_return": None,
            "source_type": "public_quote",
        }
    ]
    rebased_rows = [
        {
            "source_strategy_id": "IQVTUUF",
            "trade_date": "2026-07-16",
            "nav": 1.0,
            "daily_return": 0.0,
            "cumulative_return": 0.0,
            "benchmark_return": 0.0,
            "source_type": "official_app_curve",
        }
    ]

    merged = merge_daily_rows(quote_rows, rebased_rows)

    assert len(merged) == 1
    assert merged[0]["nav"] == 1.1307
    assert merged[0]["cumulative_return"] == 13.07
    assert merged[0]["source_type"] == "public_quote"


def test_official_source_uses_coverage_date_instead_of_single_newer_outlier() -> None:
    eligible_ids = {f"S{index:04d}" for index in range(100)}
    latest_dates = {strategy_id: "2026-07-20" for strategy_id in eligible_ids}
    latest_dates["S0000"] = "2026-07-21"

    effective = disclosure_date_at_coverage(latest_dates, eligible_ids, 0.98)

    assert effective == "2026-07-20"
    assert business_day_lag(effective, "2026-07-21") == 1


def test_curve_gap_only_retries_benchmark_lag_beyond_allowed_source_window() -> None:
    one_day_lag = [
        {"trade_date": "2026-07-20", "benchmark_return": 0.1},
        {"trade_date": "2026-07-21", "benchmark_return": None},
    ]
    long_lag = [
        {"trade_date": "2026-07-17", "benchmark_return": 0.1},
        {"trade_date": "2026-07-21", "benchmark_return": None},
    ]

    assert curve_gap_type(one_day_lag, 1) == ("", 1)
    assert curve_gap_type(long_lag, 1) == ("基准曲线滞后", 2)
    assert curve_gap_type([{"trade_date": "2026-07-21", "benchmark_return": None}], 1) == (
        "基准曲线缺失",
        None,
    )


def test_stopped_and_test_strategies_are_outside_active_quality_scope() -> None:
    assert classify_quality_scope({"strategy_status": "stopped", "strategy_name": "普通策略"}) == "stopped"
    assert classify_quality_scope({"strategy_status": None, "strategy_name": "内部测试策略"}) == "test"
    assert classify_quality_scope({"strategy_status": None, "strategy_name": "正常策略"}) == "active"


def test_official_row_clears_quote_fallback_provenance() -> None:
    quote_rows = [
        {
            "source_strategy_id": "S1",
            "trade_date": "2026-07-21",
            "nav": 1.02,
            "source_type": "public_quote",
            "provenance_role": "quote_calculated_fallback",
            "official_source_effective_date": "2026-07-20",
        }
    ]
    official_rows = [
        {
            "source_strategy_id": "S1",
            "trade_date": "2026-07-21",
            "nav": 1.021,
            "source_type": "official_app_curve",
        }
    ]

    merged = merge_daily_rows(quote_rows, official_rows)

    assert merged[0]["source_type"] == "official_app_curve"
    assert "provenance_role" not in merged[0]
    assert "official_source_effective_date" not in merged[0]


def test_requested_new_strategy_can_be_collected_before_database_load() -> None:
    import sqlite3
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        db_path = Path(directory) / "analysis.sqlite"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                'CREATE TABLE "策略信息" ('
                '"统一策略ID" TEXT, "渠道ID" TEXT, "渠道策略ID" TEXT, '
                '"策略名称" TEXT, "投顾机构" TEXT, "成立日期" TEXT, "策略状态" TEXT)'
            )
            conn.execute(
                'INSERT INTO "策略信息" VALUES (?, ?, ?, ?, ?, ?, ?)',
                ("ttfund__OLD", "ttfund", "OLD", "旧策略", "机构", "2020-01-01", "active"),
            )
            conn.commit()
        finally:
            conn.close()

        rows = load_strategies(db_path, ["OLD", "NEW"])

        assert [row["source_strategy_id"] for row in rows] == ["NEW", "OLD"]
        new_row = next(row for row in rows if row["source_strategy_id"] == "NEW")
        assert new_row["unified_strategy_id"] == "ttfund__NEW"


def test_catalog_new_strategy_is_unioned_with_database_inventory_before_load() -> None:
    import sqlite3
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        root = Path(directory)
        db_path = root / "analysis.sqlite"
        manifest_path = root / "catalog.json"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                'CREATE TABLE "策略信息" ('
                '"统一策略ID" TEXT, "渠道ID" TEXT, "渠道策略ID" TEXT, '
                '"策略名称" TEXT, "投顾机构" TEXT, "成立日期" TEXT, "策略状态" TEXT)'
            )
            conn.execute(
                'INSERT INTO "策略信息" VALUES (?, ?, ?, ?, ?, ?, ?)',
                ("ttfund__OLD", "ttfund", "OLD", "旧策略", "机构", "2020-01-01", "active"),
            )
            conn.commit()
        finally:
            conn.close()
        manifest_path.write_text(
            json.dumps(
                {
                    "state": "ready",
                    "catalog_strategy_ids": ["OLD", "NEW"],
                    "catalog_rows": [
                        {"source_strategy_id": "NEW", "strategy_name": "新策略", "advisor_name": "新机构"}
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        rows = load_strategies(db_path, catalog_manifest_path=manifest_path)

        assert [row["source_strategy_id"] for row in rows] == ["NEW", "OLD"]
        new_row = next(row for row in rows if row["source_strategy_id"] == "NEW")
        assert new_row["strategy_name"] == "新策略"
        assert new_row["inventory_source"] == "catalog_manifest"


def test_loggedin_collector_deduplicates_explicit_strategy_selection() -> None:
    collector = TTFundLoggedInCollector(
        Path("."),
        strategy_ids=["NEW", "NEW", "", "SECOND"],
        fetch_public_quote=True,
    )

    assert collector.requested_strategy_ids == ["NEW", "SECOND"]
