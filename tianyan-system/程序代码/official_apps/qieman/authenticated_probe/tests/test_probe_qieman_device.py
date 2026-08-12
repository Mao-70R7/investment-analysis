from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "probe_qieman_device.py"
SPEC = importlib.util.spec_from_file_location("probe_qieman_device", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ProbeQiemanDeviceTests(unittest.TestCase):
    def test_parse_adb_devices(self) -> None:
        rows = MODULE.parse_adb_devices(
            "List of devices attached\nabc123 device product:p model:Phone transport_id:1\nxyz unauthorized\n"
        )
        self.assertEqual(rows[0]["serial"], "abc123")
        self.assertEqual(rows[0]["state"], "device")
        self.assertEqual(rows[0]["model"], "Phone")
        self.assertEqual(rows[1]["state"], "unauthorized")

    def test_parse_front_component(self) -> None:
        package_name, activity = MODULE.parse_front_component(
            "mResumedActivity: ActivityRecord{123 u0 com.example.qieman/.MainActivity t12}"
        )
        self.assertEqual(package_name, "com.example.qieman")
        self.assertEqual(activity, ".MainActivity")

    def test_choose_foreground_package(self) -> None:
        package_name, reason = MODULE.choose_package(None, "com.example.qieman", ["com.other"])
        self.assertEqual(package_name, "com.example.qieman")
        self.assertEqual(reason, "foreground")

    def test_redaction(self) -> None:
        value = "Authorization: Bearer-abc mobile=13800138000 mail=a@example.com token=secret"
        redacted = MODULE.redact_sensitive_text(value)
        self.assertNotIn("Bearer-abc", redacted)
        self.assertNotIn("13800138000", redacted)
        self.assertNotIn("a@example.com", redacted)
        self.assertNotIn("secret", redacted)

    def test_sensitive_home_screen_text_is_fully_redacted(self) -> None:
        xml = '<node text="hi 毛先生" content-desc="最新收益 3.95" resource-id="navigation_adviser" />'
        redacted, sensitive = MODULE.redact_ui_xml(xml)
        self.assertTrue(sensitive)
        self.assertNotIn("毛先生", redacted)
        self.assertNotIn("3.95", redacted)
        self.assertIn('resource-id="navigation_adviser"', redacted)

    def test_inventory_apk_extracts_sanitized_clues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            apk = Path(temp_dir) / "base.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr(
                    "classes.dex",
                    "https://api.qieman.com/v1/strategy/detail?token=secret /portfolio/holding 基准 持仓".encode("utf-8"),
                )
            inventory = MODULE.inventory_apk(apk)
        self.assertTrue(any("api.qieman.com" in item for item in inventory["urls"]))
        self.assertFalse(any("secret" in item for item in inventory["urls"]))
        self.assertIn("基准", inventory["business_term_hits"])
        self.assertIn("持仓", inventory["business_term_hits"])

    def test_device_lock_is_owned_and_released_by_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_root = Path(temp_dir)
            path, token = MODULE.acquire_device_lock("run-1", lock_root)
            self.assertTrue(path.is_file())
            MODULE.release_device_lock(path, "wrong-token")
            self.assertTrue(path.is_file())
            MODULE.release_device_lock(path, token)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
