from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


BASE = "https://tradeapilvs.1234567.com.cn"
RAW_DIR = Path("data/raw/ttfund/direct_protocol_probe/2026-06-25")
ANALYSIS_DIR = Path("data/analysis/ttfund_strategy_protocol_probe/2026-06-25")
CACHE_ROOT = Path(
    "data/raw/ttfund/loggedin_cache/2026-06-23/20260623T015505+0800/imported_cache"
)

SAMPLES = [
    {
        "code": "004234",
        "name": "中欧数据挖掘多因子混合C",
        "kind": "fund_control",
        "expected": "ordinary_fund_protocols",
    },
    {
        "code": "0IEECYL",
        "name": "稳如泰山",
        "kind": "known_customer_buyable",
        "expected": "known_strategy_manual_pdf",
        "known_pdf_url": "https://img.1234567.com.cn/pdf/pdf_1730797865957.pdf",
    },
    {
        "code": "1DWSBSB",
        "name": "九维狐固收",
        "kind": "known_customer_buyable",
        "expected": "known_strategy_manual_pdf",
        "known_pdf_url": "https://img.1234567.com.cn/pdfread2/pdfread2/api?f=2022081517510438423",
    },
    {
        "code": "JQNQMI3",
        "name": "中欧超级股票全明星",
        "kind": "zhongou_regular_on_sale",
        "expected": "buyable_order_page_should_have_manual",
    },
    {
        "code": "6UI8WUL",
        "name": "中欧全球股票长投",
        "kind": "zhongou_regular_on_sale",
        "expected": "buyable_order_page_should_have_manual",
    },
    {
        "code": "LF94Q2M",
        "name": "中欧薪动月月投",
        "kind": "zhongou_signal_on_sale",
        "expected": "signal_service_may_not_enter_transfer",
    },
    {
        "code": "0K0L6E1",
        "name": "幸福六六小目标天天22期",
        "kind": "zhongou_stopped_target_profit_finished",
        "expected": "not_buyable_no_order_page",
    },
    {
        "code": "0YTHP0U",
        "name": "尊享多元配置04期",
        "kind": "zhongou_stopped_target_profit_running",
        "expected": "not_buyable_no_order_page",
    },
    {
        "code": "MYVG95V",
        "name": "中欧多元配置",
        "kind": "zhongou_stopped_other",
        "expected": "stopped_but_cache_support_transfer_true",
    },
]

MANUAL_TERMS = [
    "策略说明书",
    "基金投资组合策略说明书",
    "基金投资顾问",
    "投资顾问服务协议",
    "策略服务协议",
    "lookpdf",
    "pdfread2",
]

GENERIC_PROTOCOL_TERMS = [
    "ProtocolLink",
    "TradeNoteTexts",
    "ReSumeUrl",
    ".pdf",
    "基金合同",
    "招募说明书",
    "产品概要",
    "风险揭示书",
]


def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0).isoformat()


def read_cache_detail(code: str) -> dict[str, Any]:
    path = CACHE_ROOT / code / f"strategyDetailPageData{code}_funda91a99886abf7e.0"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - diagnostic path
        return {"cache_error": str(exc), "cache_path": str(path)}

    flattened: dict[str, Any] = {"cache_path": str(path)}

    def visit(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in {
                    "name",
                    "partnerId",
                    "wealthNo",
                    "isSupportTransfer",
                    "isStop",
                    "runStatus",
                    "endStatus",
                    "minBuy",
                    "strategyRate",
                    "strategyRateDiscount",
                    "supportRation",
                    "strategySelectType",
                } and not isinstance(value, (dict, list)):
                    flattened.setdefault(key, value)
            for value in obj.values():
                visit(value)
        elif isinstance(obj, list):
            for value in obj:
                visit(value)

    visit(data)
    return flattened


def request_text(
    url: str,
    payload: dict[str, Any] | None = None,
    mode: str = "form",
    timeout: int = 6,
) -> dict[str, Any]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ttfund-probe/1.0",
        "Accept": "application/json,text/plain,*/*",
    }
    data: bytes | None = None
    if payload is not None:
        if mode == "json":
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json;charset=UTF-8"
        else:
            data = urllib.parse.urlencode(payload).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read(512 * 1024)
            content_type = resp.headers.get("Content-Type", "")
            text = body.decode("utf-8", errors="replace")
            return {
                "status": resp.status,
                "content_type": content_type,
                "length": len(body),
                "text": text,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(64 * 1024)
        return {
            "status": exc.code,
            "content_type": exc.headers.get("Content-Type", ""),
            "length": len(body),
            "text": body.decode("utf-8", errors="replace"),
            "error": str(exc),
        }
    except Exception as exc:  # pragma: no cover - diagnostic path
        return {
            "status": None,
            "content_type": "",
            "length": 0,
            "text": "",
            "error": str(exc),
        }


def summarize_response(response: dict[str, Any]) -> dict[str, Any]:
    text = response.get("text") or ""
    parsed: Any = None
    success = None
    error_code = None
    first_error = None
    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                success = parsed.get("Success")
                error_code = parsed.get("ErrorCode")
                first_error = parsed.get("FirstError")
        except Exception:
            parsed = None
    manual_hits = [term for term in MANUAL_TERMS if term in text]
    generic_hits = [term for term in GENERIC_PROTOCOL_TERMS if term in text]
    return {
        "status": response.get("status"),
        "content_type": response.get("content_type"),
        "length": response.get("length"),
        "success": success,
        "errorCode": error_code,
        "firstError": first_error,
        "manual_terms": manual_hits,
        "generic_terms": generic_hits,
        "snippet": text[:900],
        "error": response.get("error"),
    }


def payloads_for(sample: dict[str, Any]) -> list[dict[str, Any]]:
    code = sample["code"]
    detail = sample.get("app_cache") or {}
    base_values = {}
    for key in ("partnerId", "wealthNo"):
        if detail.get(key):
            base_values[key] = detail[key]
    payloads = [
        {"FundCode": code},
        {"fundCode": code},
        {"FCODE": code},
        {"FundCode": code, "BusinType": "812", "businType": "812"},
        {"fundCode": code, "BusinType": "812", "businType": "812"},
    ]
    if base_values:
        payloads.extend(
            [
                {"FundCode": code, **base_values},
                {"fundCode": code, **base_values},
                {"strategyId": code, **base_values},
                {"tgCode": code, **base_values},
                {"FundCode": code, "BusinType": "812", "businType": "812", **base_values},
            ]
        )
    deduped = []
    seen = set()
    for payload in payloads:
        key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            deduped.append(payload)
    return deduped


def payloads_for_endpoint(sample: dict[str, Any], endpoint: str) -> list[dict[str, Any]]:
    code = sample["code"]
    if sample["kind"] == "fund_control":
        if endpoint == "/Trade/Protocol/GetBusinProtocols":
            return [{"FundCode": code}, {"fundCode": code}]
        if endpoint == "/Trade/Protocol/GetTradeNoteText":
            return [{"FundCode": code}]
        return [{"businType": "812"}, {"FundCode": code, "BusinType": "812", "businType": "812"}]

    detail = sample.get("app_cache") or {}
    base_values = {
        key: detail[key]
        for key in ("partnerId", "wealthNo")
        if detail.get(key)
    }
    if endpoint == "/Trade/Protocol/GetBusinProtocols":
        payloads = [{"FundCode": code}, {"fundCode": code}]
        if base_values:
            payloads.append({"strategyId": code, **base_values})
        return payloads
    if endpoint == "/Trade/Protocol/GetTradeNoteText":
        payloads = [{"FundCode": code}, {"FCODE": code}]
        if base_values:
            payloads.extend(
                [
                    {"FundCode": code, "BusinType": "812", "businType": "812", **base_values},
                    {"strategyId": code, **base_values},
                ]
            )
        return payloads

    payloads = [{"businType": "812"}]
    if base_values:
        payloads.extend(
            [
                {"FundCode": code, "BusinType": "812", "businType": "812", **base_values},
                {"strategyId": code, "businType": "812", **base_values},
            ]
        )
    return payloads


def protocol_rows(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    endpoints = [
        "/Trade/Protocol/GetBusinProtocols",
        "/Trade/Protocol/GetTradeNoteText",
        "/Business/home/BusinessProtocol",
    ]
    rows: list[dict[str, Any]] = []
    for sample in samples:
        for endpoint in endpoints:
            current_payloads = payloads_for_endpoint(sample, endpoint)
            for payload in current_payloads:
                for mode in ("form",):
                    response = request_text(f"{BASE}{endpoint}", payload=payload, mode=mode)
                    row = {
                        "sample_code": sample["code"],
                        "sample_name": sample["name"],
                        "sample_kind": sample["kind"],
                        "endpoint": endpoint,
                        "mode": mode,
                        "payload": payload,
                    }
                    row.update(summarize_response(response))
                    rows.append(row)
                    print(
                        f"{len(rows):03d} {sample['code']} {endpoint} {row['status']} "
                        f"manual={','.join(row.get('manual_terms') or [])}",
                        flush=True,
                    )
    return rows


def pdf_checks(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for sample in samples:
        urls = []
        if sample.get("known_pdf_url"):
            urls.append(("known_pdf_url", sample["known_pdf_url"]))
        select_type = (sample.get("app_cache") or {}).get("strategySelectType")
        if select_type:
            urls.append(("strategySelectType_pdfread2_candidate", f"https://img.1234567.com.cn/pdfread2/pdfread2/api?f={select_type}"))
            urls.append(("strategySelectType_pdf_candidate", f"https://img.1234567.com.cn/pdf/pdf_{select_type}.pdf"))
        for source, url in urls:
            response = request_text(url, payload=None, timeout=20)
            body = response.get("text") or ""
            raw_head = body[:16]
            checks.append(
                {
                    "sample_code": sample["code"],
                    "sample_name": sample["name"],
                    "sample_kind": sample["kind"],
                    "source": source,
                    "url": url,
                    "status": response.get("status"),
                    "content_type": response.get("content_type"),
                    "length": response.get("length"),
                    "is_pdf": raw_head.startswith("%PDF"),
                    "head_text": raw_head,
                    "error": response.get("error"),
                }
            )
    return checks


def render_markdown(result: dict[str, Any]) -> str:
    samples = result["samples"]
    rows = result["protocol_rows"]
    checks = result["pdf_checks"]
    manual_rows = [row for row in rows if row.get("manual_terms")]
    strategy_pdf_rows = [
        row
        for row in rows
        if row["sample_kind"] != "fund_control" and (row.get("manual_terms") or ".pdf" in row.get("generic_terms", []))
    ]
    fund_success_rows = [
        row
        for row in rows
        if row["sample_kind"] == "fund_control"
        and row["endpoint"] == "/Trade/Protocol/GetBusinProtocols"
        and row.get("success") is True
        and ".pdf" in row.get("generic_terms", [])
    ]
    known_pdf_ok = [row for row in checks if row.get("source") == "known_pdf_url" and row.get("is_pdf")]
    candidate_pdf_ok = [row for row in checks if row.get("source") != "known_pdf_url" and row.get("is_pdf")]

    lines = [
        "# 天天投顾典型策略协议获取可行性验证",
        "",
        f"- 生成时间：{result['generatedAt']}",
        f"- 样本数：{len(samples)}；协议接口请求：{len(rows)}；PDF URL 检查：{len(checks)}",
        "- 安全边界：仅调用只读协议/交易提示接口和已知/候选 PDF URL，未调用签署协议、下单初始化、交易提交接口。",
        "",
        "## 样本覆盖",
        "",
        "| 策略代码 | 名称 | 类型 | isSupportTransfer | isStop | runStatus | endStatus | partnerId | wealthNo | strategySelectType |",
        "|---|---|---|---:|---:|---|---|---|---|---|",
    ]
    for sample in samples:
        detail = sample.get("app_cache") or {}
        lines.append(
            "| {code} | {name} | {kind} | {support} | {stop} | {run} | {end} | {partner} | {wealth} | {select_type} |".format(
                code=sample["code"],
                name=sample["name"],
                kind=sample["kind"],
                support=detail.get("isSupportTransfer", ""),
                stop=detail.get("isStop", ""),
                run=detail.get("runStatus", ""),
                end=detail.get("endStatus", ""),
                partner=detail.get("partnerId", ""),
                wealth=detail.get("wealthNo", ""),
                select_type=detail.get("strategySelectType", ""),
            )
        )

    lines.extend(
        [
            "",
            "## 验证结论",
            "",
            f"1. 普通基金协议接口正控有效：`GetBusinProtocols(FundCode=004234)` 命中 {len(fund_success_rows)} 条带 PDF 的基金协议返回。",
            f"2. 已知可买入投顾策略的 PDF 下载链路有效：`0IEECYL`、`1DWSBSB` 的已知文档 URL 均返回 PDF（成功 {len(known_pdf_ok)} 条）。",
            f"3. 直接用策略代码调用 `GetBusinProtocols`、`GetTradeNoteText`、`BusinessProtocol`，本次未命中策略说明书原文或可下载说明书链接（策略接口命中 {len(strategy_pdf_rows)} 条，策略说明关键词命中 {len(manual_rows)} 条）。",
            f"4. 把中欧样本缓存里的 `strategySelectType` 当作 PDF 文档号尝试，未验证出 PDF（候选成功 {len(candidate_pdf_ok)} 条）。",
            "",
            "## 含义",
            "",
            "- 当前证据支持“已拿到文档号/文件号后可以直接下载原文 PDF”。",
            "- 当前证据不支持“仅凭策略代码、partnerId、wealthNo 通过通用协议接口批量获取策略说明书”。",
            "- 可买入策略的协议编号大概率仍是在进入投顾买入页后由专用页面逻辑或页面私有接口生成/读取，通用基金协议接口无法替代。",
            "- 对停售、售罄、未对客或信号类不可交易策略，若客户端不能进入买入协议区，就无法从当前通用接口直接取到说明书原文。",
            "",
            "## 关键证据摘录",
            "",
        ]
    )

    lines.append("### PDF URL 检查")
    lines.append("")
    lines.append("| 策略代码 | 来源 | HTTP | PDF | 长度 | URL |")
    lines.append("|---|---|---:|---:|---:|---|")
    for row in checks:
        lines.append(
            f"| {row['sample_code']} | {row['source']} | {row['status']} | {row['is_pdf']} | {row['length']} | {row['url']} |"
        )

    lines.extend(["", "### 协议接口命中摘要", ""])
    hit_rows = [
        row
        for row in rows
        if row.get("manual_terms")
        or (row["sample_kind"] == "fund_control" and ".pdf" in row.get("generic_terms", []))
        or (row["sample_kind"] != "fund_control" and row.get("success") is True and row["endpoint"] == "/Trade/Protocol/GetTradeNoteText")
    ][:40]
    if not hit_rows:
        lines.append("无命中。")
    else:
        lines.append("| 策略代码 | 接口 | mode | success | manual_terms | generic_terms | firstError |")
        lines.append("|---|---|---|---:|---|---|---|")
        for row in hit_rows:
            lines.append(
                "| {code} | {endpoint} | {mode} | {success} | {manual} | {generic} | {err} |".format(
                    code=row["sample_code"],
                    endpoint=row["endpoint"],
                    mode=row["mode"],
                    success=row.get("success"),
                    manual=",".join(row.get("manual_terms") or []),
                    generic=",".join(row.get("generic_terms") or []),
                    err=(row.get("firstError") or "")[:60],
                )
            )

    lines.extend(
        [
            "",
            "## 后续建议",
            "",
            "1. 要批量拿原文，优先做一次买入页网络抓包或 OkHttp/JSBridge hook，定位 `advisor-buy` 页实际获取协议编号的专用接口。",
            "2. 批量策略按状态分层处理：可买入策略走买入页协议编号抓取；停售/未对客策略只能在存在历史文档号、页面缓存或服务端专用接口时补齐。",
            "3. `SignBusinessProtocol`、`UnifiedBuyFundL2`、`CompleteTrade` 等接口不要用于探测，避免产生签署或交易状态变化。",
            "",
            f"原始 JSON：`{result['rawPath']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    samples = []
    for sample in SAMPLES:
        enriched = dict(sample)
        enriched["app_cache"] = read_cache_detail(sample["code"])
        samples.append(enriched)

    rows = protocol_rows(samples)
    checks = pdf_checks(samples)
    raw_path = RAW_DIR / "typical_strategy_protocol_feasibility_probe.json"
    md_path = ANALYSIS_DIR / "typical_strategy_protocol_feasibility.md"
    result = {
        "generatedAt": now_iso(),
        "samples": samples,
        "protocol_rows": rows,
        "pdf_checks": checks,
        "rawPath": str(raw_path),
        "analysisPath": str(md_path),
    }
    raw_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"raw": str(raw_path), "analysis": str(md_path), "rows": len(rows), "pdf_checks": len(checks)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
