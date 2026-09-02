from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PAGE_DIRS = {
    "minimal_publish": ("basic_data",),
}


def run_command(command: list[str], cwd: Path) -> int:
    print("[RUN] " + " ".join(f'"{x}"' if " " in x else x for x in command), flush=True)
    completed = subprocess.run(command, cwd=str(cwd), text=True)
    return int(completed.returncode)


def required_files(root: Path, page_set: str) -> list[Path]:
    files = [root / "basic_data" / "data" / "fund_detail_pack.js"]
    files.extend([
        root / "basic_data" / "institutions.html",
        root / "basic_data" / "assets" / "institutions.js",
    ])
    return files


def validate_required_files(root: Path, page_set: str) -> int:
    missing = [path for path in required_files(root, page_set) if not path.exists()]
    if missing:
        print("[ERROR] Production report packs were not generated:", file=sys.stderr)
        for path in missing:
            print(f"[ERROR]   missing: {path}", file=sys.stderr)
        return 10
    return 0


def sync_directory(src: Path, dst: Path, monitor_root: Path, dry_run: bool) -> int:
    if dry_run:
        robocopy_args = ["/L"]
    else:
        robocopy_args = []
    command = [
        "robocopy",
        str(src),
        str(dst),
        "/MIR",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        *robocopy_args,
    ]
    code = run_command(command, cwd=monitor_root)
    if code >= 8:
        print(f"[ERROR] Report directory sync failed. src={src} dst={dst} robocopy_exit={code}", file=sys.stderr)
        return code
    print(f"[INFO] Report directory sync completed. src={src} dst={dst} robocopy_exit={code}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync report output and rebuild deploy manifest.")
    parser.add_argument("--monitor-root", required=True)
    parser.add_argument("--prod-site-dir", required=True)
    parser.add_argument("--report-root", required=True)
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--page-set", choices=("minimal_publish",), default="minimal_publish")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    monitor_root = Path(args.monitor_root).resolve()
    prod_site_dir = Path(args.prod_site_dir).resolve()
    report_root = Path(args.report_root).resolve()

    validation_code = validate_required_files(prod_site_dir, args.page_set)
    if validation_code:
        return validation_code

    print(f"[INFO] monitor_root={monitor_root}")
    print(f"[INFO] prod_site_dir={prod_site_dir}")
    print(f"[INFO] report_root={report_root}")
    print(f"[INFO] page_set={args.page_set}")

    if prod_site_dir == report_root:
        print("[INFO] Production site already points to report root; skip robocopy.")
    else:
        for dirname in PAGE_DIRS[args.page_set]:
            src = prod_site_dir / dirname
            dst = report_root / dirname
            code = sync_directory(src, dst, monitor_root=monitor_root, dry_run=args.dry_run)
            if code:
                return code

    validation_code = validate_required_files(report_root, args.page_set)
    if validation_code:
        return validation_code

    if args.dry_run:
        print("[DRY-RUN] Skip deploy manifest write.")
        return 0

    manifest_script = monitor_root / "scripts" / "write_analysis_platform_deploy_manifest.py"
    command = [
        args.python_exe,
        str(manifest_script),
        "--deploy-dir",
        str(report_root),
        "--page-set",
        args.page_set,
    ]
    code = run_command(command, cwd=monitor_root)
    if code != 0:
        print(f"[ERROR] Report deploy manifest validation failed. Exit code: {code}", file=sys.stderr)
        return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
