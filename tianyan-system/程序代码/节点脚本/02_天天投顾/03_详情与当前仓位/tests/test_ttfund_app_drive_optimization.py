from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

import drive_ttfund_app as driver
from drive_ttfund_app_sharded import aggregate_progress, shard_ids, summarize_results


def completed(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_strategy_cache_listing_uses_targeted_shell_query() -> None:
    strategy_id = "ABC123"
    output = "\n".join(
        [
            "strategyDetailPageDataABC123_app.0",
            "adjuseHouseListABC123_app.0",
            "adjuseHouseListHisABC123_app.0",
            "strategyDetailPageDataOTHER_app.0",
        ]
    )
    with (
        patch.object(driver, "adb_shell", return_value=completed(stdout=output)) as adb_shell,
        patch.object(driver, "list_remote_cache_files", side_effect=AssertionError("global listing used")),
    ):
        names = driver.list_remote_cache_files_for_strategy(Path("adb"), "device", strategy_id)

    assert names == [
        "strategyDetailPageDataABC123_app.0",
        "adjuseHouseListABC123_app.0",
        "adjuseHouseListHisABC123_app.0",
    ]
    assert strategy_id in adb_shell.call_args.args[2]


def test_strategy_cache_listing_falls_back_when_shell_query_fails() -> None:
    with (
        patch.object(driver, "adb_shell", return_value=completed(returncode=1, stderr="shell error")),
        patch.object(
            driver,
            "list_remote_cache_files",
            return_value=["strategyDetailPageDataABC123_app.0", "strategyDetailPageDataOTHER_app.0"],
        ) as global_listing,
    ):
        names = driver.list_remote_cache_files_for_strategy(Path("adb"), "device", "ABC123")

    assert names == ["strategyDetailPageDataABC123_app.0"]
    global_listing.assert_called_once()


def test_current_holding_fast_path_falls_back_when_original_holding_is_missing() -> None:
    fast = {
        "launch_ok": True,
        "detail_ok": True,
        "holding_info_ok": False,
        "error": None,
        "elapsed_sec": 2.0,
    }
    full = {
        "launch_ok": True,
        "detail_ok": True,
        "holding_info_ok": True,
        "error": None,
        "elapsed_sec": 5.0,
    }
    with patch.object(driver, "drive_one_strategy", side_effect=[fast, full]) as capture:
        result = driver.capture_current_holding_strategy(
            drive_kwargs={"strategy_id": "ABC123"},
            detail_scan_swipes=1,
        )

    assert capture.call_count == 2
    assert capture.call_args_list[0].kwargs["detail_scan_swipes"] == 0
    assert capture.call_args_list[1].kwargs["detail_scan_swipes"] == 1
    assert result["holding_info_ok"] is True
    assert result["capture_mode"] == "current_holding_full_fallback"
    assert result["elapsed_sec"] == 7.0


def test_current_holding_fast_path_keeps_valid_original_holding() -> None:
    fast = {
        "launch_ok": True,
        "detail_ok": True,
        "holding_info_ok": True,
        "error": None,
        "elapsed_sec": 2.0,
    }
    with patch.object(driver, "drive_one_strategy", return_value=fast) as capture:
        result = driver.capture_current_holding_strategy(
            drive_kwargs={"strategy_id": "ABC123"},
            detail_scan_swipes=1,
        )

    capture.assert_called_once()
    assert result["capture_mode"] == "current_holding_fast"
    assert result["fast_path_ok"] is True


def test_current_holding_completion_requires_detail_and_holding() -> None:
    detail_only = {"detail_ok": True, "holding_info_ok": False}
    complete = {"detail_ok": True, "holding_info_ok": True}

    assert not driver.is_strategy_complete(
        detail_only,
        None,
        True,
        require_current_holding=True,
    )
    assert driver.is_strategy_complete(
        complete,
        None,
        True,
        require_current_holding=True,
    )


def test_summary_counts_holding_missing_even_without_exception() -> None:
    summary = driver.summarize(
        [
            {
                "strategy_id": "A",
                "detail_ok": True,
                "holding_info_ok": True,
                "error": None,
            },
            {
                "strategy_id": "B",
                "detail_ok": True,
                "holding_info_ok": False,
                "error": None,
            },
        ]
    )

    assert summary["error_total"] == 0
    assert summary["current_holding_missing_total"] == 1
    assert summary["current_holding_missing_ids"] == ["B"]


def test_missing_detail_is_not_reported_as_required_fields_ok() -> None:
    result = driver.apply_required_field_status(
        {
            "strategy_id": "A",
            "detail_ok": False,
            "error": "detail_page_blank",
        }
    )

    assert result["required_fields_ok"] is False
    assert result["incomplete_reason"] == "detail_missing"


def test_blank_page_is_classified_as_device_degradation() -> None:
    assert driver.is_blank_page([])
    assert driver.is_device_degradation_failure({"error": "detail_page_blank"})
    assert not driver.is_device_degradation_failure(
        {"error": "detail_cache_missing", "activity_ok": True}
    )
    assert driver.is_device_degradation_failure(
        {"error": "detail_cache_missing", "activity_ok": False}
    )
    assert driver.is_device_degradation_failure(
        {"error": "detail_cache_missing", "blank_page_unresolved": True}
    )
    assert driver.is_device_degradation_failure(
        {"error": "RuntimeError: device_unavailable: adb get-state failed"}
    )
    assert not driver.is_device_degradation_failure({"error": "benchmark_text_missing"})


def test_soft_circuit_break_requires_positive_threshold() -> None:
    assert not driver.should_soft_circuit_break(
        consecutive_device_failures=50,
        threshold=0,
    )
    assert not driver.should_soft_circuit_break(
        consecutive_device_failures=4,
        threshold=5,
    )
    assert driver.should_soft_circuit_break(
        consecutive_device_failures=5,
        threshold=5,
    )
    assert driver.should_attempt_soft_circuit_recovery(recovery_total=0, max_recoveries=2)
    assert driver.should_attempt_soft_circuit_recovery(recovery_total=1, max_recoveries=2)
    assert not driver.should_attempt_soft_circuit_recovery(recovery_total=2, max_recoveries=2)
    assert not driver.should_attempt_soft_circuit_recovery(recovery_total=0, max_recoveries=0)


def test_retained_run_cache_uses_short_name_and_manifest() -> None:
    strategy_id = "ABC123"
    long_name = f"ttfund-layout-cache-advicer-strategy-detail-matter-{'x' * 120}-{strategy_id}.0"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        mirror = driver.CacheMirror(Path("adb"), "phone", root / "run", True)
        mirror.mirror_root = root / "mirror"

        def fake_adb_run(*args, **kwargs):
            local_path = Path(args[4])
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("cache", encoding="utf-8")
            return completed()

        with (
            patch.object(driver, "list_remote_cache_files_for_strategy", return_value=[long_name]),
            patch.object(driver, "adb_run", side_effect=fake_adb_run),
        ):
            pulled = mirror.pull_for_strategy(strategy_id)

        assert long_name in pulled
        retained_dir = root / "run" / "pulled_cache" / strategy_id
        retained_files = list(retained_dir.glob("*.cache"))
        assert len(retained_files) == 1
        assert len(retained_files[0].name) < 40
        assert not (retained_dir / long_name).exists()
        manifest = json.loads((retained_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest[retained_files[0].name]["source_name"] == long_name


def test_shards_are_balanced_disjoint_and_complete() -> None:
    strategy_ids = [f"S{index}" for index in range(7)]
    shards = shard_ids(strategy_ids, ["phone", "mumu"])

    flattened = [strategy_id for values in shards.values() for strategy_id in values]
    assert sorted(flattened) == sorted(strategy_ids)
    assert len(flattened) == len(set(flattened))
    assert sorted(len(values) for values in shards.values()) == [3, 4]


def test_sharded_summary_preserves_holding_quality_counts() -> None:
    summary = summarize_results(
        [
            {
                "strategy_id": "A",
                "detail_ok": True,
                "holding_info_ok": True,
                "holding_fund_count": 4,
                "capture_mode": "current_holding_fast",
                "elapsed_sec": 2.0,
                "error": None,
            },
            {
                "strategy_id": "B",
                "detail_ok": True,
                "holding_info_ok": True,
                "holding_fund_count": 3,
                "capture_mode": "current_holding_full_fallback",
                "elapsed_sec": 6.0,
                "error": None,
            },
        ]
    )

    assert summary["strategy_total"] == 2
    assert summary["holding_info_ok_total"] == 2
    assert summary["holding_fund_total"] == 7
    assert summary["fast_path_ok_total"] == 1
    assert summary["full_fallback_total"] == 1
    assert summary["error_total"] == 0


def test_sharded_progress_uses_holding_missing_instead_of_exception_count() -> None:
    with TemporaryDirectory() as directory:
        shard_dir = Path(directory) / "shard"
        shard_dir.mkdir(parents=True)
        (shard_dir / "summary.json").write_text(
            json.dumps(
                {
                    "strategy_total": 2,
                    "holding_info_ok_total": 1,
                    "current_holding_missing_total": 1,
                    "error_total": 0,
                }
            ),
            encoding="utf-8",
        )

        progress = aggregate_progress([{"run_dir": shard_dir}], requested_total=2)

    assert progress["failure_total"] == 1
    assert "error_total" not in progress


def test_sharded_cli_dry_run_builds_disjoint_plan_without_adb() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        strategy_file = root / "strategy_ids.txt"
        run_dir = root / "run"
        strategy_file.write_text("A1\nB2\nC3\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(ROOT / "节点脚本" / "_共享组件" / "生产程序" / "drive_ttfund_app_sharded.py"),
                "--strategy-file",
                str(strategy_file),
                "--run-dir",
                str(run_dir),
                "--device",
                "phone",
                "--device",
                "mumu",
                "--dry-run",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        assert result.returncode == 0, result.stderr
        plan = driver.load_json(run_dir / "plan.json")
        assert plan["state"] == "dry_run"
        assert plan["strategy_total"] == 3
        assert plan["device_total"] == 2
        assert sorted(row["strategy_total"] for row in plan["shards"]) == [1, 2]


if __name__ == "__main__":
    test_functions = [
        test_strategy_cache_listing_uses_targeted_shell_query,
        test_strategy_cache_listing_falls_back_when_shell_query_fails,
        test_current_holding_fast_path_falls_back_when_original_holding_is_missing,
        test_current_holding_fast_path_keeps_valid_original_holding,
        test_current_holding_completion_requires_detail_and_holding,
        test_summary_counts_holding_missing_even_without_exception,
        test_missing_detail_is_not_reported_as_required_fields_ok,
        test_blank_page_is_classified_as_device_degradation,
        test_soft_circuit_break_requires_positive_threshold,
        test_retained_run_cache_uses_short_name_and_manifest,
        test_shards_are_balanced_disjoint_and_complete,
        test_sharded_summary_preserves_holding_quality_counts,
        test_sharded_progress_uses_holding_missing_instead_of_exception_count,
        test_sharded_cli_dry_run_builds_disjoint_plan_without_adb,
    ]
    suite = unittest.TestSuite(unittest.FunctionTestCase(test_function) for test_function in test_functions)
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
