from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from build_minimal_publish_set import (
    ASSET_FILES,
    PROJECT_ROOT,
    PUBLIC_PAGES,
    entry_html,
    file_manifest,
    minimal_nav,
    rewrite_page,
    write_text,
)


def presentation_build_id(
    publish_root: Path,
    presentation_assets: list[Path],
    presentation_fragments: list[str] | None = None,
) -> tuple[str, str]:
    manifest = json.loads((publish_root / "deployment_manifest.json").read_text(encoding="utf-8-sig"))
    data_build_id = str(manifest.get("dataBuildId") or manifest.get("buildId") or "minimal-data")
    digest = hashlib.sha256(data_build_id.encode("utf-8"))
    inputs = presentation_assets
    for path in inputs:
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    for fragment in presentation_fragments or []:
        digest.update(fragment.encode("utf-8"))
    return f"minimal-{digest.hexdigest()[:12]}", data_build_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh minimal-set pages and presentation assets without rebuilding or changing data packs."
    )
    parser.add_argument("--source-basic-data", type=Path, required=True)
    parser.add_argument("--publish-root", type=Path, required=True)
    return parser.parse_args()


def refresh(source_basic_data: Path, publish_root: Path) -> dict[str, object]:
    source = source_basic_data.resolve()
    target = publish_root.resolve()
    basic_target = target / "basic_data"
    code_asset_dir = PROJECT_ROOT / "basic_data" / "assets"
    if not code_asset_dir.is_dir():
        code_asset_dir = PROJECT_ROOT / "程序代码" / "basic_data" / "assets"
    refresh_asset_names = ASSET_FILES
    refresh_assets = [code_asset_dir / name for name in refresh_asset_names]
    required = [
        source / page for page in PUBLIC_PAGES
    ] + [*refresh_assets, target / "package_validation.json", target / "deployment_manifest.json"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Required no-data refresh inputs are missing: " + ", ".join(missing))

    for asset in refresh_assets:
        shutil.copy2(asset, basic_target / "assets" / asset.name)
    removed_monthly_pages = 0
    for stale_page in basic_target.glob("monthly-rebalance-report-*.html"):
        stale_page.unlink()
        removed_monthly_pages += 1
    removed_monthly_asset_dirs = 0
    for stale_assets in (basic_target / "assets").glob("monthly-rebalance-report-*"):
        if stale_assets.is_dir():
            shutil.rmtree(stale_assets)
            removed_monthly_asset_dirs += 1

    version, data_build_id = presentation_build_id(
        target,
        [basic_target / "assets" / name for name in refresh_asset_names],
        [minimal_nav(page) for page in PUBLIC_PAGES],
    )
    for page in PUBLIC_PAGES:
        rewrite_page(source / page, basic_target / page, page, version)
    write_text(basic_target / "index.html", entry_html("./institutions.html"))
    write_text(target / "index.html", entry_html("./basic_data/institutions.html"))

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    write_text(
        target / "version.json",
        json.dumps(
            {
                "version": 1,
                "buildId": version,
                "generatedAt": now,
                "entry": "basic_data/institutions.html",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    validation_path = target / "package_validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8-sig"))
    checks = validation.setdefault("checks", {})
    for key in list(checks):
        if re.fullmatch(r"monthly-rebalance-report-\d{6}(?:FileCount|Bytes)", key):
            del checks[key]
    validation["presentationRefreshedAt"] = now
    write_text(validation_path, json.dumps(validation, ensure_ascii=False, indent=2) + "\n")

    for readme_name in ("README.md", "README_部署说明.md"):
        readme_path = target / readme_name
        if readme_path.is_file():
            text = readme_path.read_text(encoding="utf-8-sig")
            text = re.sub(
                r"^- 调仓月报：发布静态报告 .*?\r?\n",
                "- 机构总览：按销售渠道和投顾管理人查看策略规模、调仓走势、基准风险资产权重及数据完整性。\n",
                text,
                flags=re.MULTILINE,
            )
            text = text.replace("策略列表、调仓月报、策略对比", "策略列表、机构总览、策略对比")
            text = text.replace("策略列表、机构总览、策略对比", "机构总览、策略列表、策略对比")
            text = text.replace(
                "本目录包含机构总览、策略列表、策略对比、全市场产品排名、AI选策略，以及策略/基金详情下钻。策略对比既可从一级菜单进入，也可从策略列表勾选产品后进入。",
                "本目录包含机构总览、策略列表、全市场产品排名、AI选策略，以及策略/基金详情下钻。策略对比保留为策略列表和 AI 选策略的下钻功能，不在一级菜单单独展示。",
            )
            text = text.replace("/basic_data/strategies.html", "/basic_data/institutions.html")
            write_text(readme_path, text)

    old_manifest = json.loads((target / "deployment_manifest.json").read_text(encoding="utf-8-sig"))
    stats = old_manifest.setdefault("stats", {})
    stats.pop("monthlyRebalanceReportAssetCount", None)
    stats.pop("monthlyRebalanceReportAssetBytes", None)
    files = file_manifest(target)
    old_manifest.update(
        {
            "buildId": version,
            "dataBuildId": data_build_id,
            "generatedAt": now,
            "pageSet": ["机构总览", "策略列表", "策略对比", "全市场产品排名", "AI选策略", "策略详情", "基金详情"],
            "entry": "basic_data/institutions.html",
            "fileCount": len(files),
            "totalBytes": sum(int(row["size"]) for row in files),
            "files": files,
        }
    )
    write_text(target / "deployment_manifest.json", json.dumps(old_manifest, ensure_ascii=False, indent=2) + "\n")
    return {
        "buildId": version,
        "removedMonthlyPages": removed_monthly_pages,
        "removedMonthlyAssetDirectories": removed_monthly_asset_dirs,
        "fileCount": len(files),
    }


def main() -> None:
    args = parse_args()
    print(json.dumps(refresh(args.source_basic_data, args.publish_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
