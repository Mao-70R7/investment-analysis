from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "_共享组件"
    / "生产程序"
    / "构建基础数据质量包.py"
)
SPEC = importlib.util.spec_from_file_location("basic_data_quality_pack", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PendingManifestQualityTests(unittest.TestCase):
    def test_explicit_pipeline_handshake_does_not_report_missing_manifest(self) -> None:
        pack = {
            "status": "warn",
            "manifest": {
                "status": "missing_manifest",
                "missing": ["deployment_manifest.json"],
            },
            "checks": [
                {"项目": "部署清单", "状态": "warn", "当前值": "missing_manifest"},
                {"项目": "重要策略元数据缺失", "状态": "warn"},
            ],
        }

        MODULE.accept_pending_manifest_generation(pack)

        self.assertEqual(pack["manifest"]["status"], "pending_generation")
        self.assertEqual(pack["manifest"]["missing"], [])
        self.assertEqual(pack["checks"][0]["状态"], "ok")
        self.assertEqual(pack["status"], "warn")

    def test_invalid_manifest_is_never_downgraded(self) -> None:
        pack = {
            "status": "warn",
            "manifest": {"status": "invalid_manifest", "missing": ["parse error"]},
            "checks": [{"项目": "部署清单", "状态": "warn"}],
        }

        MODULE.accept_pending_manifest_generation(pack)

        self.assertEqual(pack["manifest"]["status"], "invalid_manifest")
        self.assertEqual(pack["checks"][0]["状态"], "warn")


if __name__ == "__main__":
    unittest.main()
