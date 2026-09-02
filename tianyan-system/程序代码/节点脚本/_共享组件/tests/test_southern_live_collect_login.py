from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock


PROGRAM_DIR = Path(__file__).resolve().parents[1] / "生产程序"
sys.path.insert(0, str(PROGRAM_DIR))
SCRIPT = PROGRAM_DIR / "run_southern_live_collect.py"
SPEC = importlib.util.spec_from_file_location("southern_live_collect_login_test", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SouthernLiveCollectLoginTest(unittest.TestCase):
    def test_disabled_account_input_uses_dom_click_handler(self) -> None:
        page = MagicMock()
        login = MagicMock()
        login.is_disabled.return_value = True
        label = MagicMock()
        label.count.return_value = 1
        page.locator.return_value.first = label

        self.assertTrue(MODULE.unlock_login_input(page, login))

        label.evaluate.assert_called_once_with("(element) => element.click()")
        self.assertEqual(2, page.wait_for_function.call_count)

    def test_enabled_account_input_is_left_unchanged(self) -> None:
        page = MagicMock()
        login = MagicMock()
        login.is_disabled.return_value = False

        self.assertFalse(MODULE.unlock_login_input(page, login))

        page.locator.assert_not_called()

    def test_official_detail_route_is_primary(self) -> None:
        target = {"source_strategy_id": "79", "sceneno": "2"}

        url = MODULE.detail_url("token-value", target, MODULE.DETAIL_ROUTES[0])

        self.assertIn("/new/iainvest/zxlc_buydetail", url)
        self.assertIn("menuId=50000", url)
        self.assertIn("combcode=79", url)
        self.assertIn("sceneno=2", url)

    def test_existing_authenticated_token_is_reused_without_home_navigation(self) -> None:
        page = MagicMock()
        page.evaluate.return_value = "current-token"

        token = MODULE.ensure_token_page(page)

        self.assertEqual("current-token", token)
        page.goto.assert_not_called()

    def test_restored_tabs_are_closed_before_work_page_is_created(self) -> None:
        context = MagicMock()
        restored = [MagicMock(), MagicMock()]
        context.pages = restored
        work_page = MagicMock()
        context.new_page.return_value = work_page

        actual = MODULE.isolated_work_page(context)

        self.assertIs(work_page, actual)
        for page in restored:
            page.close.assert_called_once_with()
        context.new_page.assert_called_once_with()

    def test_sms_challenge_is_distinguished_from_password_failure(self) -> None:
        body = "为保障账户安全，本次登录需要进行验证 短信验证码 获取验证码"

        self.assertEqual("sms_verification", MODULE.manual_challenge_type(body))

    def test_normal_authenticated_page_has_no_manual_challenge(self) -> None:
        self.assertIsNone(MODULE.manual_challenge_type("您好！ 安全退出 司南投顾"))

    def test_required_response_validation_rejects_business_error(self) -> None:
        events = [
            {
                "url": "https://trade.southernfund.com/new/webIAqueryCombInfo",
                "status": 200,
                "response_text": json.dumps(
                    {"success": False, "errorcode": "ETS-5BP99999", "errormessage": "invalid source"}
                ),
            },
            {
                "url": "https://trade.southernfund.com/new/webIAcombFundMarketQuery",
                "status": 200,
                "response_text": json.dumps({"success": True, "result": {"info": {"comblist": []}}}),
            },
        ]

        validation = MODULE.required_response_validation(events)

        self.assertFalse(validation["passed"])
        self.assertEqual([], validation["missing_required_responses"])
        self.assertEqual(
            ["webIAqueryCombInfo", "webIAcombFundMarketQuery"],
            validation["unsuccessful_required_responses"],
        )

    def test_required_response_validation_accepts_complete_official_payloads(self) -> None:
        events = [
            {
                "url": "https://trade.southernfund.com/new/webIAqueryCombInfo",
                "status": 200,
                "response_text": json.dumps(
                    {"success": True, "result": {"combcode": "79"}},
                ),
            },
            {
                "url": "https://trade.southernfund.com/new/webIAcombFundMarketQuery",
                "status": 200,
                "response_text": json.dumps(
                    {"success": True, "result": {"info": {"comblist": [{"date": "20260813"}]}}},
                ),
            },
        ]

        validation = MODULE.required_response_validation(events)

        self.assertTrue(validation["passed"])
        self.assertEqual([], validation["missing_required_responses"])
        self.assertEqual([], validation["unsuccessful_required_responses"])


if __name__ == "__main__":
    unittest.main()
