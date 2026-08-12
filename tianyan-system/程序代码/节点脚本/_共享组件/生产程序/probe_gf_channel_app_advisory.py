from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors.gffunds_public import (  # noqa: E402
    API_BASE,
    USER_AGENT,
    build_sign,
    extract_home_strategy_codes,
)


@dataclass(frozen=True)
class ProbeProfile:
    profile_id: str
    channel_guess: str
    params: dict[str, Any] = field(default_factory=dict)


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe GFFunds advisory public APIs with guessed GF Bank/GF Securities app channel params."
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "gf_channel_probe" / "channel_param_probe",
    )
    return parser.parse_args()


def candidate_profiles() -> list[ProbeProfile]:
    return [
        ProbeProfile("default_html5", "gffunds", {}),
        ProbeProfile("gffunds_app", "gffunds", {"market": "GffundsApp", "app_channel": "APP"}),
        ProbeProfile("gffunds_wx", "gffunds", {"market": "GffundsWechat", "app_channel": "WECHAT"}),
        ProbeProfile("gfsec_yitaojin", "gfsec", {"market": "GFZQ", "app_channel": "YITAOJIN"}),
        ProbeProfile("gfsec_yitaojin_netno", "gfsec", {"market": "GFZQ", "app_channel": "NETNO_YITAOJIN"}),
        ProbeProfile("gfsec_yitajin", "gfsec", {"market": "GFZQ", "app_channel": "YITAJIN"}),
        ProbeProfile("gfsec_gfsec", "gfsec", {"market": "GFSEC", "app_channel": "GFSEC"}),
        ProbeProfile("gfsec_gfzq_app", "gfsec", {"market": "GFZQAPP", "app_channel": "GFZQ"}),
        ProbeProfile("gfsec_ytj", "gfsec", {"market": "YITAOJIN", "app_channel": "YTJ"}),
        ProbeProfile("gfsec_extra_channel", "gfsec", {"market": "GffundsHtml5", "app_channel": "NETNO_HTML5", "channel": "GFZQ"}),
        ProbeProfile(
            "gfsec_extra_sale_channel",
            "gfsec",
            {"market": "GffundsHtml5", "app_channel": "NETNO_HTML5", "sale_channel": "GFZQ"},
        ),
        ProbeProfile(
            "gfsec_extra_source_channel",
            "gfsec",
            {"market": "GffundsHtml5", "app_channel": "NETNO_HTML5", "source_channel": "GFZQ"},
        ),
        ProbeProfile("gfbank_cgb", "gfbank", {"market": "CGB", "app_channel": "CGB"}),
        ProbeProfile("gfbank_gfyh", "gfbank", {"market": "GFYH", "app_channel": "GFYH"}),
        ProbeProfile("gfbank_gfbank", "gfbank", {"market": "GFBANK", "app_channel": "GFBANK"}),
        ProbeProfile("gfbank_cgbchina", "gfbank", {"market": "CGBCHINA", "app_channel": "CGBCHINA"}),
        ProbeProfile("gfbank_netno_cgb", "gfbank", {"market": "CGB", "app_channel": "NETNO_CGB"}),
        ProbeProfile("gfbank_netno_gfyh", "gfbank", {"market": "GFYH", "app_channel": "NETNO_GFYH"}),
        ProbeProfile("gfbank_extra_channel", "gfbank", {"market": "GffundsHtml5", "app_channel": "NETNO_HTML5", "channel": "CGB"}),
        ProbeProfile(
            "gfbank_extra_sale_channel",
            "gfbank",
            {"market": "GffundsHtml5", "app_channel": "NETNO_HTML5", "sale_channel": "CGB"},
        ),
        ProbeProfile(
            "gfbank_extra_source_channel",
            "gfbank",
            {"market": "GffundsHtml5", "app_channel": "NETNO_HTML5", "source_channel": "CGB"},
        ),
    ]


def post_json(endpoint: str, body: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    url = f"{API_BASE}/{endpoint.lstrip('/')}"
    payload = urlencode(build_sign(body)).encode("utf-8")
    raw = b""
    status: int | None = None
    headers: dict[str, str] = {}
    error: str | None = None
    try:
        request = Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "GFF-Charset": "UTF-8",
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            headers = dict(response.headers)
            try:
                raw = response.read()
            except IncompleteRead as exc:
                raw = exc.partial
                error = f"IncompleteRead({len(raw)} bytes)"
    except HTTPError as exc:
        status = exc.code
        headers = dict(exc.headers)
        raw = exc.read()
        error = f"HTTPError({exc.code})"
    except URLError as exc:
        error = f"URLError({exc.reason})"
    except (TimeoutError, OSError) as exc:
        error = f"{type(exc).__name__}({exc})"

    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = None
    return {
        "status": status,
        "headers": headers,
        "error": error,
        "raw_text": text,
        "json": parsed if isinstance(parsed, dict) else {"data": parsed} if parsed is not None else None,
        "raw_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
    }


def strategy_rows_from_list(payload: dict[str, Any] | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in (payload or {}).get("config_list") or []:
        adv_id = str(item.get("adv_id") or "").strip()
        if not adv_id:
            continue
        rows.append(
            {
                "adv_id": adv_id,
                "adv_name": str(item.get("adv_name") or "").strip(),
                "adv_type": str(item.get("adv_type") or "").strip(),
                "adv_status": str(item.get("adv_status") or "").strip(),
                "adv_operate_state": str(item.get("adv_operate_state") or "").strip(),
            }
        )
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    run_at = now_local()
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    raw_index: list[dict[str, Any]] = []
    default_ids: set[str] | None = None

    for profile in candidate_profiles():
        base = {
            "session_id": "",
            "page_no": 1,
            "page_size": args.page_size,
            **profile.params,
        }
        strategy_response = post_json("get_investadvisor_operate_config_list", base, timeout=args.timeout)
        home_response = post_json(
            "get_invest_advisor_config",
            {"session_id": "", **profile.params},
            timeout=args.timeout,
        )
        profile_dir = run_dir / profile.profile_id
        strategy_raw = {
            key: value
            for key, value in strategy_response.items()
            if key != "headers"
        }
        home_raw = {
            key: value
            for key, value in home_response.items()
            if key != "headers"
        }
        write_json(profile_dir / "get_investadvisor_operate_config_list.json", strategy_raw)
        write_json(profile_dir / "get_invest_advisor_config.json", home_raw)

        strategy_payload = strategy_response.get("json") or {}
        home_payload = home_response.get("json") or {}
        strategy_rows = strategy_rows_from_list(strategy_payload)
        ids = [row["adv_id"] for row in strategy_rows]
        home_codes = extract_home_strategy_codes(home_payload)
        if default_ids is None:
            default_ids = set(ids)
        row = {
            "run_id": run_id,
            "profile_id": profile.profile_id,
            "channel_guess": profile.channel_guess,
            "params": json.dumps(profile.params, ensure_ascii=False, sort_keys=True),
            "list_status": strategy_response.get("status"),
            "list_error": strategy_response.get("error") or "",
            "list_retcode": strategy_payload.get("RETCODE"),
            "list_retmsg": strategy_payload.get("RETMSG"),
            "strategy_count": len(ids),
            "strategy_ids": ",".join(ids),
            "strategy_names": "、".join(row["adv_name"] for row in strategy_rows[:20]),
            "home_status": home_response.get("status"),
            "home_error": home_response.get("error") or "",
            "home_retcode": home_payload.get("RETCODE"),
            "home_codes": ",".join(home_codes),
            "list_hash": strategy_response.get("raw_sha256"),
            "home_hash": home_response.get("raw_sha256"),
            "same_ids_as_default": default_ids == set(ids),
            "extra_ids_vs_default": ",".join(sorted(set(ids) - (default_ids or set()))),
            "missing_ids_vs_default": ",".join(sorted((default_ids or set()) - set(ids))),
        }
        rows.append(row)
        raw_index.append(
            {
                "profile_id": profile.profile_id,
                "channel_guess": profile.channel_guess,
                "params": profile.params,
                "raw_dir": str(profile_dir),
            }
        )
        print(
            f"{profile.profile_id}: strategies={len(ids)} ret={row['list_retcode']} "
            f"same_default={row['same_ids_as_default']}",
            flush=True,
        )

    csv_path = run_dir / "channel_param_probe_summary.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    write_json(
        run_dir / "channel_param_probe_summary.json",
        {
            "run_id": run_id,
            "captured_at": run_at.isoformat(timespec="seconds"),
            "api_base": API_BASE,
            "rows": rows,
            "raw_index": raw_index,
        },
    )
    latest = args.output_dir / "latest_channel_param_probe_summary.json"
    write_json(latest, {"latest_run_dir": str(run_dir), "rows": rows})
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
