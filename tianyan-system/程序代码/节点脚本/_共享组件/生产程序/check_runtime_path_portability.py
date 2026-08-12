from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
FORBIDDEN_PATTERNS = (
    re.compile(r"E:[\\/]+synctingData(?:ToWork)?", re.IGNORECASE),
    re.compile(r"C:[\\/]+advisor_monitor_backup", re.IGNORECASE),
    re.compile(r"Path\(__file__\)\.resolve\(\)\.parents\[\d+\]"),
    re.compile(r"PROJECT_ROOT\s*/\s*[\"']scripts[\"']", re.IGNORECASE),
    re.compile(r"[\"']\.\\\\scripts\\\\"),
)
SCAN_SUFFIXES = {".py", ".ps1", ".bat", ".cmd", ".json", ".sh"}
ROOT_FILES = {
    "00_每日数据更新并发布_唯一入口.bat",
    "AGENTS.md",
    "README_AI.md",
}
SCAN_DIRS = ("节点脚本", "config")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reject old-PC absolute paths from the runtime code package.")
    parser.add_argument("--code-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def candidate_files(code_root: Path) -> list[Path]:
    files = [code_root / name for name in ROOT_FILES if (code_root / name).is_file()]
    for directory in SCAN_DIRS:
        root = code_root / directory
        if root.is_dir():
            files.extend(
                path
                for path in root.rglob("*")
                if path.is_file()
                and path.suffix.lower() in SCAN_SUFFIXES
                and "98_非生产工具" not in path.parts
                and "99_兼容入口" not in path.parts
                and "tests" not in path.parts
                and path.name != "migrate_production_paths.py"
            )
    return sorted(set(files))


def scan(code_root: Path) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for path in candidate_files(code_root):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for pattern in FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    issues.append(
                        {
                            "file": path.relative_to(code_root).as_posix(),
                            "line": line_number,
                            "pattern": pattern.pattern,
                            "text": line.strip()[:500],
                        }
                    )
    return {
        "status": "ready" if not issues else "blocked",
        "codeRoot": str(code_root.resolve()),
        "scannedFileCount": len(candidate_files(code_root)),
        "issueCount": len(issues),
        "issues": issues,
    }


def main() -> None:
    args = parse_args()
    result = scan(args.code_root.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "ready" else 2)


if __name__ == "__main__":
    main()
