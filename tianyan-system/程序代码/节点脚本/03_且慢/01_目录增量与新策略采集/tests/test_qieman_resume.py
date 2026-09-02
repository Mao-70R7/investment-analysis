from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "qieman_daily_update.py"
SPEC = importlib.util.spec_from_file_location("qieman_daily_update", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_latest_partial_stargate_run_dir_stays_within_same_node_run() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        node_root = Path(temporary) / "qieman_collect"
        previous = node_root / "attempt_02" / "collector" / "stargate" / "old-attempt"
        current = node_root / "attempt_03" / "collector"
        (previous / "raw" / "composition").mkdir(parents=True)
        (previous / "raw" / "composition" / "batch_0001.json").write_text("{}", encoding="utf-8")
        current.mkdir(parents=True)

        selected = MODULE.latest_partial_stargate_run_dir(current, "current-attempt")

    assert selected == previous.resolve()


def test_latest_partial_stargate_run_dir_ignores_attempt_without_raw_json() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        node_root = Path(temporary) / "qieman_collect"
        empty_previous = node_root / "attempt_02" / "collector" / "stargate" / "old-attempt" / "raw"
        current = node_root / "attempt_03" / "collector"
        empty_previous.mkdir(parents=True)
        current.mkdir(parents=True)

        selected = MODULE.latest_partial_stargate_run_dir(current, "current-attempt")

    assert selected is None


def test_latest_partial_history_run_dir_prefers_richest_incomplete_attempt() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        raw_root = Path(temporary)
        history_root = raw_root / "qieman" / "signed_history"
        richer = history_root / "daily-1__qieman_collect__attempt_03"
        newer_but_smaller = history_root / "daily-1__qieman_collect__attempt_04"
        for candidate in (richer, newer_but_smaller):
            (candidate / "raw" / "nav").mkdir(parents=True)
            (candidate / "checkpoint.json").write_text("{}", encoding="utf-8")
        (richer / "raw" / "nav" / "A.json").write_text("[]", encoding="utf-8")
        (richer / "raw" / "nav" / "B.json").write_text("[]", encoding="utf-8")
        (newer_but_smaller / "raw" / "nav" / "A.json").write_text("[]", encoding="utf-8")

        selected = MODULE.latest_partial_history_run_dir(raw_root, "daily-1", "current-attempt")

    assert selected == richer.resolve()


def test_latest_partial_history_run_dir_reuses_rich_failed_summary_attempt() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        raw_root = Path(temporary)
        history_root = raw_root / "qieman" / "signed_history"
        rich_failed = history_root / "daily-1__qieman_collect__attempt_03"
        small_checkpoint = history_root / "daily-1__qieman_collect__attempt_04"
        for candidate in (rich_failed, small_checkpoint):
            (candidate / "raw" / "nav").mkdir(parents=True)
            (candidate / "raw" / "signal_adjustments").mkdir(parents=True)
        for strategy_id in ("A", "B"):
            (rich_failed / "raw" / "nav" / f"{strategy_id}.json").write_text("[]", encoding="utf-8")
            (rich_failed / "raw" / "signal_adjustments" / f"{strategy_id}.json").write_text(
                '{"complete": true, "content": []}', encoding="utf-8"
            )
        (rich_failed / "summary.json").write_text('{"failedStrategyCount": 1}', encoding="utf-8")
        (small_checkpoint / "raw" / "nav" / "A.json").write_text("[]", encoding="utf-8")
        (small_checkpoint / "raw" / "signal_adjustments" / "A.json").write_text(
            '{"complete": true, "content": []}', encoding="utf-8"
        )
        (small_checkpoint / "checkpoint.json").write_text("{}", encoding="utf-8")

        selected = MODULE.latest_partial_history_run_dir(raw_root, "daily-1", "current-attempt")

    assert selected == rich_failed.resolve()


def test_daily_summary_records_each_strategy_latest_nav_date_distribution() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8-sig")

    assert '"nav_latest_date_counts"' in source
    assert 'nav.get("latestDate")' in source


def test_history_batch_targets_cover_catalog_without_overshoot() -> None:
    assert MODULE.history_batch_targets(0, 50) == []
    assert MODULE.history_batch_targets(40, 50) == [40]
    assert MODULE.history_batch_targets(120, 50) == [50, 100, 120]
    assert MODULE.history_batch_targets(613, 50) == [
        50,
        100,
        150,
        200,
        250,
        300,
        350,
        400,
        450,
        500,
        550,
        600,
        613,
    ]


if __name__ == "__main__":
    test_latest_partial_stargate_run_dir_stays_within_same_node_run()
    test_latest_partial_stargate_run_dir_ignores_attempt_without_raw_json()
    test_latest_partial_history_run_dir_prefers_richest_incomplete_attempt()
    test_latest_partial_history_run_dir_reuses_rich_failed_summary_attempt()
    test_daily_summary_records_each_strategy_latest_nav_date_distribution()
    test_history_batch_targets_cover_catalog_without_overshoot()
    print("qieman resume tests: 6 passed")
