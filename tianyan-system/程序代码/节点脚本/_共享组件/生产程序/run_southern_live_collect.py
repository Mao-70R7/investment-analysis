from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, Response, sync_playwright

from runtime_workspace import load_workspace
from southern_dpapi_credentials import read_credentials


PROJECT_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "AGENTS.md").is_file() and (parent / "本机配置" / "runtime.local.json").is_file()
)
WORKSPACE = load_workspace(PROJECT_ROOT)
PROFILE_DIR = WORKSPACE.output_root.parent / "state" / "southern-profile"
OUT_DIR = WORKSPACE.raw_root / "southern" / "live_collect"
DEFAULT_INVENTORY_ROOT = WORKSPACE.raw_root / "southern" / "public_h5"
DEFAULT_DPAPI_PATH = PROJECT_ROOT / "本机配置" / "southern_login.dpapi"
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
REQUIRED_RESPONSE_NAMES = ("webIAqueryCombInfo", "webIAcombFundMarketQuery")
DETAIL_ROUTES = (
    {
        "name": "official_customization_result",
        "path": "/new/iainvest/zxlc_buydetail",
        "menu_id": "50000",
    },
    {
        "name": "legacy_scene6",
        "path": "/new/iainvest/scene6",
        "menu_id": "80000",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect authenticated Southern advisor plan facts.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true")
    group.add_argument("--strategy-ids", default="")
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--result-path", type=Path)
    parser.add_argument("--login-wait-seconds", type=int, default=60)
    parser.add_argument("--detail-wait-seconds", type=int, default=20)
    parser.add_argument("--dpapi-input", type=Path, default=DEFAULT_DPAPI_PATH)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def redact_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\b1\d{10}\b", "[PHONE]", text)
    text = re.sub(r"[\u4e00-\u9fff]{2,4}，您好", "[NAME]，您好", text)
    text = re.sub(r"(logpassword|password|passwd|newCrpPwd|encryptPwd)=([^&\s]+)", r"\1=[SECRET]", text, flags=re.I)
    text = re.sub(r"SECURE_TOKEN=[A-Za-z0-9]+", "SECURE_TOKEN=[TOKEN]", text)
    text = re.sub(r"SUBMIT_TOKEN=[A-Za-z0-9]+", "SUBMIT_TOKEN=[TOKEN]", text)
    return text


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(redact(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_artifact(name: str, value: Any) -> Path:
    timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds").replace(":", "-").replace("+", "_")
    path = OUT_DIR / f"{name}-{timestamp}.json"
    atomic_json(path, value)
    return path


def assert_no_conflicting_run() -> None:
    own_run_id = str(os.environ.get("SOUTHERN_DAILY_RUN_ID") or "").strip()
    candidates = (
        WORKSPACE.lock_root / "daily_update.lock",
        WORKSPACE.database_root / "daily_update.lock",
        WORKSPACE.lock_root / "main_db_write.lock",
    )
    active = []
    for path in candidates:
        if not path.is_file():
            continue
        if path.name == "daily_update.lock" and own_run_id:
            try:
                if str(json.loads(path.read_text(encoding="utf-8-sig")).get("runId") or "").strip() == own_run_id:
                    continue
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        active.append(str(path))
    if active:
        raise RuntimeError(f"Production lock is active; Southern collection aborted: {active}")


def latest_inventory() -> Path:
    files = list(DEFAULT_INVENTORY_ROOT.rglob("strategy_inventory.json")) if DEFAULT_INVENTORY_ROOT.is_dir() else []
    if not files:
        raise FileNotFoundError(f"No Southern strategy inventory below {DEFAULT_INVENTORY_ROOT}")
    return max(files, key=lambda path: path.stat().st_mtime_ns)


def load_targets(args: argparse.Namespace) -> tuple[Path, list[dict[str, str]]]:
    inventory_path = (args.inventory or latest_inventory()).resolve()
    payload = json.loads(inventory_path.read_text(encoding="utf-8-sig"))
    inventory = payload.get("strategies") or []
    requested = {
        item.strip()
        for item in str(args.strategy_ids or "").split(",")
        if item.strip()
    }
    if args.all:
        requested = {str(row.get("source_strategy_id") or "").strip() for row in inventory}
    if not requested:
        requested = {"79"}
    targets = [
        {
            "source_strategy_id": str(row.get("source_strategy_id") or "").strip(),
            "strategy_name": str(row.get("strategy_name") or ""),
            "sceneno": str(row.get("sceneno") or "").strip(),
        }
        for row in inventory
        if str(row.get("source_strategy_id") or "").strip() in requested
    ]
    found = {row["source_strategy_id"] for row in targets}
    if found != requested:
        raise RuntimeError(f"Strategy IDs missing from inventory: {sorted(requested - found)}")
    if any(not row["sceneno"] for row in targets):
        raise RuntimeError("Every Southern strategy target must have sceneno.")
    return inventory_path, targets


def page_summary(page: Page, limit: int = 3000) -> dict[str, Any]:
    text = page.locator("body").inner_text(timeout=5000) if page.locator("body").count() else ""
    return {"title": page.title(), "url": page.url, "text_sample": text[:limit]}


def isolated_work_page(context: Any) -> Page:
    """Discard restored tabs so stale SECURE_TOKEN pages cannot race the run."""
    for existing in list(context.pages):
        try:
            existing.close()
        except Exception:
            pass
    return context.new_page()


def credentials(args: argparse.Namespace) -> tuple[str, str] | None:
    login_id = str(os.environ.get("SOUTHERN_LOGIN_ID") or "").strip()
    password = str(os.environ.get("SOUTHERN_LOGIN_PASSWORD") or "")
    if login_id and password:
        return login_id, password
    dpapi_path = args.dpapi_input.resolve()
    if dpapi_path.is_file():
        return read_credentials(dpapi_path)
    return None


def unlock_login_input(page: Page, login: Any) -> bool:
    """Use the site's own label handler to enable the account input.

    The Southern login page initially renders ``lognumber`` as disabled and
    removes that attribute from the click handler bound to ``#lableLT``.
    Playwright's actionability checks reject a normal click on a label that is
    associated with a disabled input, so invoke the label's native DOM click
    after the official handler is bound and require that handler to enable the
    field.
    """
    if not login.is_disabled():
        return False
    label = page.locator("#lableLT").first
    if not label.count():
        raise RuntimeError("Southern login account label was not found.")
    page.wait_for_function(
        """() => {
          const label = document.querySelector('#lableLT');
          if (!label || !window.jQuery || typeof window.jQuery._data !== 'function') return false;
          const events = window.jQuery._data(label, 'events');
          return Boolean(events && events.click && events.click.length);
        }""",
        timeout=30_000,
    )
    label.evaluate("(element) => element.click()")
    page.wait_for_function(
        "() => { const x=document.querySelector('#loginForm_lognumber'); return x && !x.disabled; }",
        timeout=30_000,
    )
    return True


def try_login(page: Page, secret: tuple[str, str] | None) -> dict[str, Any]:
    if not secret or "/account/login" not in page.url:
        return {"attempted": False, "reason": "not_needed_or_no_secure_credential"}
    login_id, password = secret
    login = page.locator("#loginForm_lognumber").first
    password_input = page.locator("#loginForm_logpassword").first
    login.wait_for(state="attached", timeout=30_000)
    password_input.wait_for(state="visible", timeout=30_000)
    input_unlocked = unlock_login_input(page, login)
    login.fill(login_id)
    page.wait_for_timeout(random.randint(800, 1500))
    password_input.fill(password)
    privacy = page.locator("#privacyPolicy").first
    if privacy.count() and not privacy.is_checked():
        privacy.check(force=True)
    page.locator("#loginForm_submit").first.click(timeout=15_000)
    page.wait_for_timeout(3000)
    body = page.locator("body").inner_text(timeout=5000)
    challenge_type = manual_challenge_type(body)
    return {
        "attempted": True,
        "submitted": True,
        "input_unlocked": input_unlocked,
        "manual_check_required": challenge_type is not None,
        "challenge_type": challenge_type,
    }


def manual_challenge_type(body: str) -> str | None:
    text = str(body or "")
    if "短信" in text and "验证码" in text:
        return "sms_verification"
    if "人脸" in text:
        return "face_verification"
    if "扫码" in text:
        return "qr_verification"
    if "滑块" in text or "图形验证码" in text:
        return "captcha_verification"
    if "安全验证" in text or "二次验证" in text:
        return "security_verification"
    return None


def wait_for_login(page: Page, seconds: int) -> bool:
    deadline = time.monotonic() + max(15, min(720, seconds))
    while time.monotonic() < deadline:
        body = page.locator("body").inner_text(timeout=5000) if page.locator("body").count() else ""
        if "安全退出" in body and "/account/login" not in page.url:
            return True
        page.wait_for_timeout(2000)
    return False


def ensure_token_page(page: Page) -> str:
    token_expression = """() => window.$hs_secure_token || new URL(location.href).searchParams.get('SECURE_TOKEN') ||
        (() => { const a=document.querySelector('a[href*="SECURE_TOKEN"]'); return a ? new URL(a.href,location.href).searchParams.get('SECURE_TOKEN') : ''; })()"""
    try:
        token = page.evaluate(token_expression)
    except Exception:
        token = ""
    if token:
        return str(token)
    for url in (
        "https://trade.southernfund.com/new/go?menuId=10000",
        "https://trade.southernfund.com/new/account/main/init?menuId=10000",
        "https://trade.southernfund.com/new/account/main/init",
    ):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2500)
        except Exception:
            continue
        if "/account/login" not in page.url:
            break
    token = page.evaluate(token_expression)
    if not token:
        raise RuntimeError("Authenticated SECURE_TOKEN was not found.")
    return str(token)


def establish_advisor_context(page: Page, token: str) -> dict[str, Any]:
    """Enter the official advisor flow before opening a strategy detail.

    Southern's detail page may send a newly created browser tab back to the
    account home page until the authenticated session has visited
    ``iainvest/init_inner``.  The public introduction page exposes that route
    as its official "立即开启" link, so follow it rather than manufacturing
    browser state or depending on a restored tab.
    """
    entry = f"https://trade.southernfund.com/new/iainvest/init?menuId=80000&SECURE_TOKEN={token}"
    entry_reused = "/iainvest/init?" in page.url and f"SECURE_TOKEN={token}" in page.url
    if not entry_reused:
        page.goto(entry, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
    inner = page.locator('a[href*="/iainvest/init_inner"]').first
    used_official_link = bool(inner.count())
    if used_official_link:
        inner.click(timeout=20_000)
        page.wait_for_load_state("domcontentloaded", timeout=60_000)
    elif "/iainvest/init_inner" not in page.url:
        page.goto(
            f"https://trade.southernfund.com/new/iainvest/init_inner?menuId=80000&SECURE_TOKEN={token}",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
    page.wait_for_timeout(3000)
    if "/account/login" in page.url:
        raise RuntimeError("Southern advisor context redirected to login.")
    return {
        "entry_reused": entry_reused,
        "used_official_link": used_official_link,
        "ready": "/iainvest/" in page.url,
        "page": page_summary(page, limit=500),
    }


def required_response_validation(events: list[dict[str, Any]]) -> dict[str, Any]:
    diagnostics: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    unsuccessful: list[str] = []
    for name in REQUIRED_RESPONSE_NAMES:
        matches = [item for item in events if name in str(item.get("url") or "")]
        if not matches:
            missing.append(name)
            diagnostics[name] = {"captured": False, "successful": False}
            continue

        successful = False
        last_error_code = None
        last_error_message = None
        for item in matches:
            payload = None
            try:
                payload = json.loads(str(item.get("response_text") or ""))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            if isinstance(payload, dict):
                last_error_code = payload.get("errorcode")
                last_error_message = payload.get("errormessage")
            result = payload.get("result") if isinstance(payload, dict) else None
            if name == "webIAqueryCombInfo":
                has_required_result = isinstance(result, dict) and bool(result.get("combcode"))
            else:
                info = result.get("info") if isinstance(result, dict) else None
                has_required_result = isinstance(info, dict) and bool(info.get("comblist"))
            if (
                int(item.get("status") or 0) == 200
                and isinstance(payload, dict)
                and payload.get("success") is True
                and has_required_result
            ):
                successful = True
                break
        if not successful:
            unsuccessful.append(name)
        diagnostics[name] = {
            "captured": True,
            "response_count": len(matches),
            "successful": successful,
            "last_error_code": last_error_code,
            "last_error_message": last_error_message,
        }
    return {
        "required_response_names": list(REQUIRED_RESPONSE_NAMES),
        "missing_required_responses": missing,
        "unsuccessful_required_responses": unsuccessful,
        "response_diagnostics": diagnostics,
        "passed": not missing and not unsuccessful,
    }


def wait_for_required_responses(
    page: Page,
    events: list[dict[str, Any]],
    start: int,
    seconds: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    deadline = time.monotonic() + max(5, min(120, seconds))
    plan_events = events[start:]
    validation = required_response_validation(plan_events)
    while not validation["passed"] and time.monotonic() < deadline:
        page.wait_for_timeout(500)
        plan_events = events[start:]
        validation = required_response_validation(plan_events)
    return plan_events, validation


def detail_url(token: str, target: dict[str, str], route: dict[str, str]) -> str:
    return (
        f"https://trade.southernfund.com{route['path']}"
        f"?menuId={route['menu_id']}&combcode={target['source_strategy_id']}"
        f"&sceneno={target['sceneno']}&SECURE_TOKEN={token}"
    )


def main() -> int:
    args = parse_args()
    assert_no_conflicting_run()
    inventory_path, targets = load_targets(args)
    if args.dry_run:
        result = {"status": "dry_run", "inventory_path": str(inventory_path), "target_count": len(targets), "targets": targets}
        if args.result_path:
            atomic_json(args.result_path.resolve(), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    edge = Path(os.environ.get("SOUTHERN_BROWSER_EXECUTABLE") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            executable_path=str(edge) if edge.is_file() else None,
            viewport={"width": 1365, "height": 900},
        )
        context.route(re.compile(r"cconline|piwik|wap\.southernfund"), lambda route: route.abort())
        page = isolated_work_page(context)

        def capture(response: Response) -> None:
            url = response.url
            if "southernfund.com" not in url or not re.search(
                r"webia|iainvest|comb|portfolio|fund|strategy|asset|risk|question|plan|scene|query", url, re.I
            ):
                return
            item: dict[str, Any] = {
                "at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "status": response.status,
                "method": response.request.method,
                "url": url,
                "response_content_type": response.headers.get("content-type"),
                "response_text": None,
            }
            if re.search(r"webIAqueryCombInfo|webIAcombFundMarketQuery|webIAqueryTradeRate|ia_report", url, re.I):
                try:
                    body = response.body()
                    item["response_bytes"] = len(body)
                    item["response_truncated"] = len(body) > MAX_RESPONSE_BYTES
                    if not item["response_truncated"]:
                        item["response_text"] = body.decode("utf-8", errors="replace")
                except Exception:
                    pass
            events.append(item)

        page.on("response", capture)
        secret = credentials(args)
        entry = (
            "https://trade.southernfund.com/new/account/login/init?from=web&url=%2Fiainvest%2Finit%3FmenuId%3D80000"
            if secret
            else "https://trade.southernfund.com/new/account/main/init?menuId=10000"
        )
        page.goto(entry, wait_until="domcontentloaded", timeout=30_000)
        login_attempt = try_login(page, secret)
        if not wait_for_login(page, args.login_wait_seconds):
            body = page.locator("body").inner_text(timeout=5000) if page.locator("body").count() else ""
            challenge_type = manual_challenge_type(body) or login_attempt.get("challenge_type")
            failure = {
                "status": "auth_challenge_required" if challenge_type else "auth_required",
                "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "page": page_summary(page),
                "login_attempt": login_attempt,
                "challenge_type": challenge_type,
            }
            save_artifact("live_login_not_detected", failure)
            if args.result_path:
                atomic_json(args.result_path.resolve(), failure)
            context.close()
            return 2

        token = ensure_token_page(page)
        advisor_context = establish_advisor_context(page, token)
        plan_results = []
        for target in targets:
            assert_no_conflicting_run()
            route_attempts = []
            selected_events: list[dict[str, Any]] = []
            validation = required_response_validation([])
            attempted_events: list[dict[str, Any]] = []
            for route in DETAIL_ROUTES:
                for attempt in range(1, 3):
                    start = len(events)
                    navigation_error = None
                    try:
                        page.goto(
                            detail_url(token, target, route),
                            wait_until="domcontentloaded",
                            timeout=60_000,
                        )
                    except Exception as exc:
                        navigation_error = f"{type(exc).__name__}: {exc}"
                    plan_events, validation = wait_for_required_responses(
                        page,
                        events,
                        start,
                        args.detail_wait_seconds,
                    )
                    attempted_events.extend(plan_events)
                    route_attempts.append(
                        {
                            "route": route["name"],
                            "attempt": attempt,
                            "navigation_error": navigation_error,
                            "validation": validation,
                        }
                    )
                    if validation["passed"]:
                        selected_events = plan_events
                        break
                if validation["passed"]:
                    break

            artifact_events = selected_events or attempted_events
            artifact = {
                "captured_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "strategy": target,
                "page_url": page.url,
                "page": page_summary(page),
                "events": artifact_events,
                "route_attempts": route_attempts,
                "validation": validation,
            }
            artifact_path = save_artifact(f"southern_plan_detail-{target['source_strategy_id']}", artifact)
            plan_results.append(
                {
                    "source_strategy_id": target["source_strategy_id"],
                    "output_path": str(artifact_path),
                    "validation": artifact["validation"],
                }
            )
        collection_passed = bool(plan_results) and all(
            (item.get("validation") or {}).get("passed") is True for item in plan_results
        )
        result = {
            "status": "success" if collection_passed else "validation_failed",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "inventory_path": str(inventory_path),
            "login_attempt": login_attempt,
            "advisor_context": advisor_context,
            "plan_results": plan_results,
            "event_summary": [
                {key: item.get(key) for key in ("at", "status", "method", "url", "response_content_type", "response_bytes", "response_truncated")}
                for item in events
            ],
        }
        summary_path = save_artifact("southern_live_collect", result)
        if args.result_path:
            atomic_json(args.result_path.resolve(), result)
        context.close()
    print(json.dumps({"output_path": str(summary_path), "target_count": len(targets)}, ensure_ascii=False, indent=2))
    return 0 if collection_passed else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise
