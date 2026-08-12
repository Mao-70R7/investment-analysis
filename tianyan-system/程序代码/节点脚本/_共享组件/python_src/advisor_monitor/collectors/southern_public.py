from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from advisor_monitor.models import RawSnapshot

CHANNEL_ID = "southern"
CHANNEL_NAME = "南方基金/司南投顾"
COLLECTOR_NAME = "public_index"


class SouthernIndexParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value for key, value in attrs if value}
        if tag.lower() == "area" and "href" in attr_map:
            self.links.append(urljoin(self.base_url, attr_map["href"]))
        if tag.lower() == "a" and "href" in attr_map:
            self.links.append(urljoin(self.base_url, attr_map["href"]))
        if tag.lower() == "img" and "src" in attr_map:
            self.images.append(urljoin(self.base_url, attr_map["src"]))


def fetch_public_index(project_root: Path, channel_config_path: Path) -> dict[str, object]:
    config = json.loads(channel_config_path.read_text(encoding="utf-8"))
    url = config["collectors"]["public_index"]["url"]
    run_at = datetime.now(timezone.utc).astimezone()
    run_id = run_at.strftime("%Y%m%dT%H%M%S")
    day = run_at.strftime("%Y-%m-%d")

    request = Request(url, headers={"User-Agent": "Mozilla/5.0 advisor-monitor/0.1"})
    with urlopen(request, timeout=30) as response:
        raw_bytes = response.read()
        http_status = response.status
        content_type = response.headers.get("Content-Type")

    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    raw_dir = project_root / "data" / "raw" / CHANNEL_ID / COLLECTOR_NAME / day / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "index.html"
    raw_path.write_bytes(raw_bytes)

    text = raw_bytes.decode("gb2312", errors="replace")
    parser = SouthernIndexParser(url)
    parser.feed(text)
    login_urls = [link for link in parser.links if "account/login" in link or "iainvest" in link]
    title_match = re.search(r"<title>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)

    snapshot = RawSnapshot(
        snapshot_id=f"{CHANNEL_ID}-{COLLECTOR_NAME}-{run_id}-{content_hash[:12]}",
        channel_id=CHANNEL_ID,
        collector_name=COLLECTOR_NAME,
        access_level="public",
        captured_at=run_at.isoformat(timespec="seconds"),
        source_url=url,
        http_status=http_status,
        raw_path=str(raw_path),
        content_type=content_type,
        content_hash=content_hash,
        parse_status="partial",
    )
    normalized_path = (
        project_root / "data" / "normalized" / CHANNEL_ID / COLLECTOR_NAME / day / f"{run_id}.jsonl"
    )
    parsed = {
        "snapshot": snapshot.to_dict(),
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "source_url": url,
        "run_id": run_id,
        "captured_at": run_at.isoformat(timespec="seconds"),
        "raw_dir": str(raw_dir),
        "normalized_path": str(normalized_path),
        "title": title_match.group(1).strip() if title_match else None,
        "login_urls": login_urls,
        "image_assets": parser.images,
        "observed_at": run_at.isoformat(timespec="seconds"),
        "available_entities": ["channel_landing", "login_entry"],
        "missing_entities": [
            "strategy_master",
            "strategy_performance_daily",
            "strategy_fund_snapshot",
            "strategy_rebalance_event",
        ],
        "next_step": "需要登录南方基金网上交易系统后验证 /iainvest 页面接口。",
    }

    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    with normalized_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(parsed, ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "run_id": run_id,
        "captured_at": run_at.isoformat(timespec="seconds"),
        "raw_dir": str(raw_dir),
        "normalized_path": str(normalized_path),
        "source_url": url,
        "title": parsed["title"],
        "login_url_total": len(login_urls),
        "image_asset_total": len(parser.images),
        "available_entities": parsed["available_entities"],
        "missing_entities": parsed["missing_entities"],
        "snapshot_id": snapshot.snapshot_id,
    }
    summary_path = project_root / "data" / "normalized" / CHANNEL_ID / "collection_summary" / day / f"{run_id}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    parsed["summary_path"] = str(summary_path)

    return parsed
