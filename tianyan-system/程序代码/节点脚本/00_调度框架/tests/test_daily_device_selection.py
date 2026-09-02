from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "节点脚本" / "_共享组件" / "生产程序"))

import select_daily_update_device as selector  # noqa: E402


class DailyDeviceSelectionTests(unittest.TestCase):
    def test_auto_selects_only_the_configured_physical_phone(self) -> None:
        with patch.object(
            selector,
            "check_physical",
            return_value={"deviceType": "physical", "deviceId": "physical-1", "ready": True},
        ) as physical:
            selected, attempts, priority = selector.select_device(
                Path("adb.exe"),
                physical_device_id="physical-1",
                preflight_attempts=3,
                preflight_retry_seconds=0,
            )

        self.assertEqual(priority, ["physical"])
        self.assertEqual(selected["deviceType"], "physical")
        self.assertEqual(len(attempts), 1)
        physical.assert_called_once()
        self.assertFalse(hasattr(selector, "check_mumu"))

    def test_physical_preflight_retries_transient_failures(self) -> None:
        not_ready = {
            "deviceId": "physical-1",
            "ready": False,
            "stateReady": False,
            "isEmulator": False,
        }
        ready = {
            "deviceId": "physical-1",
            "ready": True,
            "stateReady": True,
            "isEmulator": False,
        }
        with patch.object(selector, "adb_device_health", side_effect=[not_ready, not_ready, ready]) as health, patch.object(
            selector.time,
            "sleep",
        ) as sleep:
            result = selector.check_physical(
                Path("adb.exe"),
                "physical-1",
                attempts=3,
                retry_wait_seconds=0.1,
            )

        self.assertTrue(result["ready"])
        self.assertEqual(result["preflightAttemptCount"], 3)
        self.assertEqual(health.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_stale_config_falls_back_to_unique_online_physical_phone(self) -> None:
        stale = {
            "deviceId": "192.168.0.78:40197",
            "ready": False,
            "stateReady": False,
            "isEmulator": False,
        }
        usb = {
            "deviceId": "b27b7c93",
            "ready": True,
            "stateReady": True,
            "isEmulator": False,
        }
        with patch.object(selector, "discover_online_device_ids", return_value=["b27b7c93"]), patch.object(
            selector,
            "check_physical",
            side_effect=[stale, usb],
        ):
            selected, attempts, priority = selector.select_device(
                Path("adb.exe"),
                physical_device_id="192.168.0.78:40197",
                preflight_attempts=1,
                preflight_retry_seconds=0,
            )

        self.assertEqual(priority, ["physical"])
        self.assertEqual(selected["deviceId"], "b27b7c93")
        self.assertEqual(selected["selectionMethod"], "auto_discovery")
        self.assertEqual(len(attempts), 2)

    def test_multiple_auto_discovered_physical_phones_are_not_selected_implicitly(self) -> None:
        def ready(serial: str) -> dict[str, object]:
            return {"deviceId": serial, "ready": True, "stateReady": True, "isEmulator": False}

        with patch.object(selector, "discover_online_device_ids", return_value=["usb-1", "usb-2"]), patch.object(
            selector,
            "check_physical",
            side_effect=[ready("usb-1"), ready("usb-2")],
        ):
            selected, attempts, _ = selector.select_device(
                Path("adb.exe"),
                physical_device_id="",
                preflight_attempts=1,
                preflight_retry_seconds=0,
            )

        self.assertIsNone(selected)
        self.assertEqual(len(attempts), 3)
        self.assertIn("multiple ready physical devices", attempts[-1]["error"])

    def test_emulator_result_is_rejected_without_retries(self) -> None:
        emulator = {
            "deviceId": "127.0.0.1:16384",
            "ready": False,
            "stateReady": True,
            "isEmulator": True,
            "blockedReasons": ["emulator_device_not_allowed"],
        }
        with patch.object(selector, "adb_device_health", return_value=emulator) as health:
            result = selector.check_physical(
                Path("adb.exe"),
                "127.0.0.1:16384",
                attempts=3,
                retry_wait_seconds=0,
            )

        self.assertFalse(result["ready"])
        self.assertTrue(result["isEmulator"])
        health.assert_called_once()

    def test_main_writes_physical_only_selection_contract(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            adb_path = root / "adb.exe"
            adb_path.write_bytes(b"")
            output_path = root / "selection.json"
            argv = [
                "select_daily_update_device.py",
                "--project-root",
                str(root),
                "--adb-path",
                str(adb_path),
                "--physical-device-id",
                "physical-1",
                "--preflight-retry-seconds",
                "0",
                "--output-path",
                str(output_path),
            ]
            with patch.object(sys, "argv", argv), patch.object(
                selector,
                "check_physical",
                return_value={"deviceType": "physical", "deviceId": "physical-1", "ready": True},
            ):
                exit_code = selector.main()
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["deviceMode"], "physical_only")
        self.assertEqual(payload["priority"], ["physical"])
        self.assertEqual(payload["selected"]["deviceId"], "physical-1")
        self.assertIsNone(payload["fallback"])
        self.assertFalse(payload["fallbackUsed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
