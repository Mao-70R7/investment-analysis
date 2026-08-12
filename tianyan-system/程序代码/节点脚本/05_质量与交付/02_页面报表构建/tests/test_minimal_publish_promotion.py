from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


CODE_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_minimal_publish_set.py"
SPEC = importlib.util.spec_from_file_location("build_minimal_publish_set", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MinimalPublishPromotionTests(unittest.TestCase):
    def make_staging(self, parent: Path, suffix: str) -> Path:
        staging = parent / f".最小发布集.staging-{suffix}"
        basic = staging / "basic_data"
        assets = basic / "assets"
        assets.mkdir(parents=True)
        (staging / "index.html").write_text("new", encoding="utf-8")
        (staging / "version.json").write_text("{}", encoding="utf-8")
        (staging / "deployment_manifest.json").write_text("{}", encoding="utf-8")
        (staging / "package_validation.json").write_text(
            json.dumps({"status": "ready"}), encoding="utf-8"
        )
        for page in MODULE.PUBLIC_PAGES:
            active = MODULE.ACTIVE_PAGE.get(page, page)
            extra = (
                '<script src="./data/strategy_detail_index_pack.js"></script>'
                if page == "compare.html"
                else ""
            )
            (basic / page).write_text(
                f"<html><body>{MODULE.minimal_nav(active)}{extra}</body></html>",
                encoding="utf-8",
            )
        source_assets = CODE_ROOT / "basic_data" / "assets"
        for name in ("basic-common.js", "strategies.js", "ai-strategy.js", "insights.js", "basic.css"):
            shutil.copy2(source_assets / name, assets / name)
        return staging

    def make_existing_target(self, parent: Path) -> Path:
        target = parent / "最小发布集"
        (target / ".git").mkdir(parents=True)
        (target / ".git" / "marker").write_text("git", encoding="utf-8")
        (target / "old.txt").write_text("old", encoding="utf-8")
        return target

    def test_git_worktree_is_swapped_as_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            target = self.make_existing_target(parent)
            staging = self.make_staging(parent, "success")

            MODULE.promote_staged_package(staging, target, parent)

            self.assertFalse(staging.exists())
            self.assertFalse((target / "old.txt").exists())
            self.assertEqual((target / "index.html").read_text(encoding="utf-8"), "new")
            self.assertEqual((target / ".git" / "marker").read_text(encoding="utf-8"), "git")
            self.assertFalse(any(parent.glob(".最小发布集.backup-*")))

    def test_failed_second_rename_restores_original_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            target = self.make_existing_target(parent)
            staging = self.make_staging(parent, "rollback")
            real_replace = Path.replace

            def guarded_replace(path: Path, destination: Path) -> Path:
                if path == staging:
                    raise PermissionError("simulated staging rename failure")
                return real_replace(path, destination)

            with patch.object(Path, "replace", new=guarded_replace):
                with self.assertRaises(PermissionError):
                    MODULE.promote_staged_package(staging, target, parent)

            self.assertTrue((target / "old.txt").is_file())
            self.assertEqual((target / ".git" / "marker").read_text(encoding="utf-8"), "git")
            self.assertFalse(any(parent.glob(".最小发布集.backup-*")))

    def test_invalid_strategy_list_contract_is_rejected_before_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            target = self.make_existing_target(parent)
            staging = self.make_staging(parent, "invalid-contract")
            script = staging / "basic_data" / "assets" / "strategies.js"
            script.write_text(
                script.read_text(encoding="utf-8").replace('id="strategyCompareButton"', 'id="removedCompareButton"'),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "missing contract tokens"):
                MODULE.promote_staged_package(staging, target, parent)

            self.assertTrue((target / "old.txt").is_file())

    def test_remove_tree_cleans_read_only_git_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary) / ".最小发布集.staging-readonly"
            pack = staging / ".git" / "objects" / "pack" / "sample.idx"
            pack.parent.mkdir(parents=True)
            pack.write_bytes(b"readonly")
            os.chmod(pack, stat.S_IREAD)

            MODULE.remove_tree(staging)

            self.assertFalse(staging.exists())


if __name__ == "__main__":
    unittest.main()
