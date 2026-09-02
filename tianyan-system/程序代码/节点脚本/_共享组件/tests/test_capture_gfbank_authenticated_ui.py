from __future__ import annotations

import importlib.util
import io
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
MODULE_PATH = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "capture_gfbank_authenticated_ui.py"
SPEC = importlib.util.spec_from_file_location("capture_gfbank_authenticated_ui", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class CaptureGfbankAuthenticatedUiTest(unittest.TestCase):
    def test_h5_route_metadata_is_sanitized_and_keeps_strategy_identifiers(self) -> None:
        payload = (
            "mArguments=Bundle[{"
            "url=https://20181028.cgbchina.com.cn/cgb_smart_invest/new_smart_invest/"
            "pro_detail.html?groupCode=A043010003&spGroupCode=SP20260802&_from=secret, "
            "appId=20181028, sessionId=session_should_not_be_persisted, "
            'launchParams={"token":"also_secret"}'
            "}]"
        )
        metadata = capture.extract_h5_route_metadata(payload)
        self.assertEqual(metadata["app_id"], "20181028")
        self.assertEqual(metadata["group_code"], "A043010003")
        self.assertEqual(metadata["sp_group_code"], "SP20260802")
        self.assertEqual(
            metadata["h5_path"],
            "/cgb_smart_invest/new_smart_invest/pro_detail.html",
        )
        self.assertNotIn("session", str(metadata))
        self.assertNotIn("token", str(metadata))
        self.assertNotIn("_from", str(metadata))

    def test_all_authenticated_strategy_entries_are_recognized_as_distinct_lists(self) -> None:
        for entry_label in ("理财组合", "超级定投", "目标盈"):
            payload = (
                "<hierarchy>"
                f'<node text="{entry_label}" class="android.widget.TextView" scrollable="false" />'
                f'<node text="{entry_label}" class="android.webkit.WebView" scrollable="true" />'
                "</hierarchy>"
            )
            self.assertTrue(capture.is_strategy_list(payload, entry_label))
            self.assertEqual(capture.current_strategy_entry(payload), entry_label)

    def test_target_profit_real_title_is_recognized(self) -> None:
        payload = (
            "<hierarchy>"
            '<node text="智投·目标盈" class="android.widget.TextView" scrollable="false" />'
            '<node text="智投·目标盈" class="android.webkit.WebView" scrollable="true" />'
            "</hierarchy>"
        )
        self.assertTrue(capture.is_strategy_list(payload, "目标盈"))

    def test_target_profit_info_banner_is_distinguished_from_provider_cards(self) -> None:
        payload = (
            "<hierarchy>"
            '<node class="android.view.View" clickable="true" bounds="[0,245][1080,1253]" />'
            '<node class="android.view.View" clickable="true" bounds="[0,1286][1080,1505]" />'
            '<node class="android.view.View" clickable="true" bounds="[0,1520][1080,1739]" />'
            "</hierarchy>"
        )
        self.assertEqual(capture.target_profit_info_banner_center(payload), (540, 749))

    def test_target_profit_and_super_invest_detail_names_are_not_rejected(self) -> None:
        for strategy_name in (
            "幸福小心愿目标盈01期",
            "南方智投小目标134期",
            "博乐小目标141期",
            "超级定投家",
        ):
            payload = (
                "<hierarchy>"
                '<node text="投顾策略详情" />'
                f'<node text="{strategy_name}" />'
                '<node text="投顾服务费率：0.20%/年" />'
                "</hierarchy>"
            )
            self.assertEqual(capture.detail_strategy_name_from_payload(payload), strategy_name)

    def test_benchmark_disclosure_icon_is_located_next_to_benchmark_label(self) -> None:
        payload = (
            "<hierarchy>"
            '<node text="基准涨跌幅" clickable="false" bounds="[690,1389][837,1431]" />'
            '<node text="info" clickable="true" bounds="[843,1389][876,1431]" />'
            '<node text="成立以来" clickable="true" bounds="[768,1959][1002,2034]" />'
            "</hierarchy>"
        )
        self.assertEqual(capture.find_clickable_after_text(payload, ("基准涨跌幅",)), (859, 1410))

    def test_curve_point_requires_benchmark_value_on_the_same_date(self) -> None:
        payload = (
            "<hierarchy>"
            '<node text="业绩表现" />'
            '<node text="2026.07.28" />'
            '<node text="组合涨跌幅：+8.42%" />'
            '<node text="每个投资者的投顾组合" />'
            "</hierarchy>"
        )
        self.assertIsNone(capture.performance_point_from_payload(payload))

    def test_requested_full_name_matches_shortened_list_card_product_name(self) -> None:
        card = {
            "strategy_name": "广发活期佳30天",
            "product_card_text": "活期佳30天",
        }
        self.assertTrue(
            capture.card_matches_requested_strategy(
                card,
                {"广发优配活期佳30天"},
            )
        )

    def test_recent_scan_scope_only_uses_right_edge_and_keeps_latest_coordinate(self) -> None:
        effective_left, positions = capture.build_curve_scan_positions(
            80,
            1000,
            3,
            scan_scope="recent",
            recent_width_px=180,
        )
        self.assertEqual(effective_left, 821)
        self.assertEqual(positions[0], 821)
        self.assertEqual(positions[-1], 1000)
        self.assertLess(len(positions), 70)

    def test_auto_scope_requires_validated_multi_date_curve_cache(self) -> None:
        scope, state = capture.resolve_curve_scan_scope(
            "auto",
            "招商佳薪90天",
            {"招商佳薪90天": {"curve_point_total": 243, "last_trade_date": "2026-07-29"}},
        )
        self.assertEqual(scope, "recent")
        self.assertEqual(state["curve_point_total"], 243)
        missing_scope, _missing_state = capture.resolve_curve_scan_scope("auto", "新策略", {})
        self.assertEqual(missing_scope, "full")

    def test_unchanged_curve_skip_requires_same_exact_latest_point_and_real_history(self) -> None:
        current = {
            "trade_date": "2026-07-30",
            "cumulative_return": 29.1,
            "benchmark_return": 8.2,
        }
        cached = {
            "curve_point_total": 243,
            "last_trade_date": "2026-07-30",
            "cumulative_return": 29.1,
            "benchmark_return": 8.2,
        }
        self.assertTrue(capture.cached_curve_matches_current(cached, current))
        self.assertFalse(capture.cached_curve_matches_current({**cached, "curve_point_total": 1}, current))
        self.assertFalse(
            capture.cached_curve_matches_current(
                {**cached, "benchmark_return": 8.21},
                current,
            )
        )

    def test_monkey_curve_script_contains_only_fixed_drag_and_wait_events(self) -> None:
        payload = capture.build_monkey_curve_script(
            [87, 93],
            scan_y=1636,
            scan_right=993,
            dwell_milliseconds=350,
        )
        self.assertIn("count= 4", payload)
        self.assertIn("Drag(87,1636,89,1636,1)", payload)
        self.assertIn("Drag(93,1636,95,1636,1)", payload)
        self.assertEqual(payload.count("UserWait(350)"), 2)
        self.assertNotIn("LaunchActivity", payload)
        self.assertNotIn("DispatchPress", payload)

    def test_read_only_command_retries_transient_device_disconnect(self) -> None:
        offline = subprocess.CompletedProcess(
            ["adb"],
            1,
            stdout="",
            stderr="error: device 'phone' not found",
        )
        online = subprocess.CompletedProcess(["adb"], 0, stdout="device\n", stderr="")
        with patch.object(capture.subprocess, "run", side_effect=[offline, online]) as mocked_run, patch.object(
            capture.time, "sleep"
        ):
            result = capture.run_adb("adb", "phone", "get-state", transient_retries=1)
        self.assertEqual(result.stdout.strip(), "device")
        self.assertEqual(mocked_run.call_count, 2)

    def test_input_command_is_not_retried_because_repeating_a_tap_is_unsafe(self) -> None:
        offline = subprocess.CompletedProcess(
            ["adb"],
            1,
            stdout="",
            stderr="error: device 'phone' not found",
        )
        with patch.object(capture.subprocess, "run", return_value=offline) as mocked_run:
            with self.assertRaisesRegex(RuntimeError, "device 'phone' not found"):
                capture.run_adb("adb", "phone", "shell", "input", "tap", "10", "20")
        self.assertEqual(mocked_run.call_count, 1)

    def test_ocr_curve_evidence_round_trips_through_exact_payload_parser(self) -> None:
        point = {
            "trade_date": "2026-07-30",
            "cumulative_return": 8.52,
            "benchmark_return": 6.57,
        }
        payload = capture.build_ocr_curve_evidence_xml(
            "招商佳薪90天",
            point,
            x=321,
            minimum_confidence=0.934,
        )
        self.assertEqual(capture.performance_point_from_payload(payload), point)
        self.assertIn('source="screen_ocr_periodically_verified_against_uiautomator"', payload)

    def test_screenshot_ocr_requires_labels_and_high_confidence(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (1080, 2290), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        class Engine:
            def __init__(self) -> None:
                self.rows = iter(
                    (
                        [["2026.07.30", 0.999]],
                        [["组合涨跌幅：+8.52%", 0.95]],
                        [["基准涨跌幅：+6.57%", 0.94]],
                        [["+8.52%", 0.93]],
                        [["+6.57%", 0.92]],
                    )
                )

            def __call__(self, *_args: object, **_kwargs: object) -> tuple[object, list[float]]:
                return next(self.rows), [0.01]

        point, diagnostics = capture.performance_point_from_screenshot(
            buffer.getvalue(),
            (78, 1449, 1002, 1896),
            Engine(),
        )
        self.assertEqual(
            point,
            {"trade_date": "2026-07-30", "cumulative_return": 8.52, "benchmark_return": 6.57},
        )
        self.assertAlmostEqual(diagnostics["minimum_confidence"], 0.94)

    def test_screenshot_ocr_rejects_missing_decimal_point(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (1080, 2290), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        class Engine:
            def __init__(self) -> None:
                self.rows = iter(
                    (
                        [["2023.06.06", 0.998]],
                        [["组合涨跌幅：+119%", 0.975]],
                        [["基准涨跌幅：+0.30%", 0.961]],
                    )
                )

            def __call__(self, *_args: object, **_kwargs: object) -> tuple[object, list[float]]:
                return next(self.rows), [0.01]

        point, _diagnostics = capture.performance_point_from_screenshot(
            buffer.getvalue(),
            (78, 1449, 1002, 1896),
            Engine(),
        )
        self.assertIsNone(point)

    def test_screenshot_ocr_accepts_exact_date_below_old_confidence_cutoff(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (1080, 2290), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        class Engine:
            def __init__(self) -> None:
                self.rows = iter(
                    (
                        [["2019.11.18", 0.867]],
                        [["组合涨跌幅：+0.64%", 0.98]],
                        [["基准涨跌幅：+0.40%", 0.97]],
                        [["+0.64%", 0.96]],
                        [["+0.40%", 0.95]],
                    )
                )

            def __call__(self, *_args: object, **_kwargs: object) -> tuple[object, list[float]]:
                return next(self.rows), [0.01]

        point, _diagnostics = capture.performance_point_from_screenshot(
            buffer.getvalue(),
            (78, 1449, 1002, 1896),
            Engine(),
        )
        self.assertEqual(
            point,
            {"trade_date": "2019-11-18", "cumulative_return": 0.64, "benchmark_return": 0.40},
        )

    def test_screenshot_ocr_rejects_date_with_extra_characters(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (1080, 2290), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        class Engine:
            def __init__(self) -> None:
                self.rows = iter(
                    (
                        [["12019.11.18", 0.999]],
                        [["组合涨跌幅：+0.64%", 0.98]],
                        [["基准涨跌幅：+0.40%", 0.97]],
                    )
                )

            def __call__(self, *_args: object, **_kwargs: object) -> tuple[object, list[float]]:
                return next(self.rows), [0.01]

        point, _diagnostics = capture.performance_point_from_screenshot(
            buffer.getvalue(),
            (78, 1449, 1002, 1896),
            Engine(),
        )
        self.assertIsNone(point)

    def test_screenshot_ocr_rejects_value_crop_disagreement(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (1080, 2290), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        class Engine:
            def __init__(self) -> None:
                self.rows = iter(
                    (
                        [["2022.03.10", 0.99]],
                        [["组合涨跌幅：+15.65%", 0.97]],
                        [["基准涨跌幅：+1.47%", 0.95]],
                        [["+15.65%", 0.94]],
                        [["+11.47%", 0.93]],
                    )
                )

            def __call__(self, *_args: object, **_kwargs: object) -> tuple[object, list[float]]:
                return next(self.rows), [0.01]

        point, diagnostics = capture.performance_point_from_screenshot(
            buffer.getvalue(),
            (78, 1449, 1002, 1896),
            Engine(),
        )
        self.assertIsNone(point)
        self.assertEqual(diagnostics["value_recognized"]["benchmark_value"]["text"], "+11.47%")


if __name__ == "__main__":
    unittest.main()
