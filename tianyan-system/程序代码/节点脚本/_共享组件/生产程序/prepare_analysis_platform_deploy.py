from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_SOURCE_SITE = PROJECT_ROOT / "site"
LINUX_START_SCRIPT = PROJECT_ROOT / "start_basic_data_site_linux.sh"
DEFAULT_DEPLOY_DIR = PROJECT_ROOT / "site"
DEPLOY_README = """# Linux start

This directory is the deploy root for the static analysis platform.

Start in background:

```bash
cd /path/to/analysis-platform
bash ./start_basic_data_site_linux.sh --host 0.0.0.0 --port 7676
```

The script prints the PID and log path, then exits. Console output is appended to:

```text
./logs/basic_data_site_7676.log
```

Follow logs:

```bash
tail -f ./logs/basic_data_site_7676.log
```

Run in foreground for debugging:

```bash
bash ./start_basic_data_site_linux.sh --foreground --host 0.0.0.0 --port 7676
```

Open:

```text
http://<server-ip>:7676/
```

The root page redirects to `basic_data/index.html`. This server only serves static
files. It does not run the Windows data refresh scripts.
"""
DEPLOY_STIGNORE = """// Keep the Syncthing deploy folder focused on static site assets.
// These folders are local scratch/runtime artifacts and are not needed on Linux.
(?d).cache
(?d).cache/**
(?d).python
(?d).python/**
(?d)output
(?d)output/**
(?d)tmp
(?d)tmp/**
(?d).stfolder.removed-*
(?d).stfolder.removed-*/**
(?d)logs
(?d)logs/**
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare static shell files for the deployable analysis platform.")
    parser.add_argument("--source-site", type=Path, default=DEFAULT_SOURCE_SITE)
    parser.add_argument("--deploy-dir", type=Path, default=DEFAULT_DEPLOY_DIR)
    parser.add_argument(
        "--page-set",
        choices=("minimal_publish",),
        default="minimal_publish",
    )
    return parser.parse_args()


def write_text_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except UnicodeDecodeError:
            pass
    path.write_text(text, encoding="utf-8")
    return True


def copy_if_changed(source: Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and source.stat().st_size == target.stat().st_size:
        try:
            if source.read_bytes() == target.read_bytes():
                return False
        except OSError:
            pass
    shutil.copy2(source, target)
    return True


def copy_tree_static(source: Path, target: Path, exclude_names: set[str]) -> dict[str, int]:
    copied = 0
    unchanged = 0
    for path in source.rglob("*"):
        rel = path.relative_to(source)
        if any(part in exclude_names for part in rel.parts):
            continue
        dest = target / rel
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if copy_if_changed(path, dest):
            copied += 1
        else:
            unchanged += 1
    return {"copied": copied, "unchanged": unchanged}


def remove_tree(path: Path) -> bool:
    if not path.exists():
        return False
    if not path.is_dir():
        raise RuntimeError(f"Refusing to remove non-directory path: {path}")
    shutil.rmtree(path)
    return True


def main() -> None:
    args = parse_args()
    source_site = args.source_site.resolve()
    deploy_dir = args.deploy_dir.resolve()
    deploy_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict[str, int] | str | bool] = {}
    results["removed_strategy_center"] = remove_tree(deploy_dir / "strategy_center")
    results["removed_full_data_statistics_report"] = remove_tree(deploy_dir / "full_data_statistics_report")

    root_index = (
        "<!doctype html>\n"
        '<meta charset="utf-8">\n'
        '<meta http-equiv="refresh" content="0; url=basic_data/">\n'
        "<title>\u5168\u5e02\u573a\u6295\u987e\u5206\u6790\u5e73\u53f0</title>\n"
        '<a href="basic_data/">\u8fdb\u5165\u5168\u5e02\u573a\u6295\u987e\u5206\u6790\u5e73\u53f0</a>\n'
    )
    results["root_index_changed"] = write_text_if_changed(deploy_dir / "index.html", root_index)
    if LINUX_START_SCRIPT.exists():
        results["linux_start_script_changed"] = copy_if_changed(
            LINUX_START_SCRIPT,
            deploy_dir / LINUX_START_SCRIPT.name,
        )
    else:
        results["linux_start_script_changed"] = "source_missing"
    results["linux_readme_changed"] = write_text_if_changed(deploy_dir / "README_LINUX_START.md", DEPLOY_README)
    results["syncthing_ignore_changed"] = write_text_if_changed(deploy_dir / ".stignore", DEPLOY_STIGNORE)
    results["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    results["deploy_dir"] = str(deploy_dir)
    results["page_set"] = args.page_set

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
