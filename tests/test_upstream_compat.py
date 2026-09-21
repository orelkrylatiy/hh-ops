from __future__ import annotations

import argparse
import asyncio
import html
import json
from http.cookiejar import Cookie, CookieJar
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from hh_applicant_tool.main import HHApplicantTool
from hh_applicant_tool.operations.apply_vacancies import Operation as ApplyOperation
from hh_applicant_tool.operations.authorize import Operation as AuthorizeOperation
from hh_applicant_tool.operations.config import parse_scalar


class FakeResponse:
    def __init__(self, text: str, *, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code
        self.url = "https://hh.ru/test"


class FakeSession:
    def __init__(
        self,
        *,
        cookies: dict[str, str] | None = None,
        page_text: str = "",
    ) -> None:
        self.cookies = _cookie_jar(cookies or {})
        self.page_text = page_text
        self.get_calls = 0

    def get(self, _url: str, **_kwargs):
        self.get_calls += 1
        return FakeResponse(self.page_text)


def _cookie_jar(data: dict[str, str]) -> CookieJar:
    jar = CookieJar()
    for name, value in data.items():
        jar.set_cookie(
            Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=".hh.ru",
                domain_specified=True,
                domain_initial_dot=True,
                path="/",
                path_specified=True,
                secure=False,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )
    return jar


def _tool_with_session(session: FakeSession) -> HHApplicantTool:
    tool = HHApplicantTool.__new__(HHApplicantTool)
    tool.session = session
    return tool


def _xsrf_html(*tokens: str, escaped: bool = False) -> str:
    quote = "&quot;" if escaped else '"'
    return "".join(
        f",{quote}xsrfToken{quote}:{quote}{token}{quote}" for token in tokens
    )


def test_xsrf_prefers_cookie_and_skips_network() -> None:
    session = FakeSession(cookies={"_xsrf": "REAL_TOKEN"})
    tool = _tool_with_session(session)

    assert tool.xsrf_token == "REAL_TOKEN"
    assert session.get_calls == 0


def test_xsrf_unescapes_html_and_matches_cookie() -> None:
    session = FakeSession(
        cookies={"_xsrf": "REAL_TOKEN"},
        page_text=_xsrf_html("ROTATING", "REAL_TOKEN", escaped=True),
    )
    tool = _tool_with_session(session)

    assert tool._extract_xsrf_token(session.page_text) == "REAL_TOKEN"


def test_redirect_config_parses_escaped_hh_initial_state() -> None:
    payload = {
        "redirectConfig": {"vacancyTests": {"123": {"required": True}}},
        "account": {"firstName": "Test"},
    }
    escaped = html.escape(json.dumps(payload), quote=True)
    response = FakeResponse(
        f'<template id="HH-Lux-InitialState">{escaped}</template>'
    )
    tool = HHApplicantTool.__new__(HHApplicantTool)

    parsed = tool.parse_redirect_config(response)

    assert parsed["redirectConfig"]["vacancyTests"]["123"]["required"] is True


def test_authorize_supports_current_magritte_fields() -> None:
    assert "magritte-phone-input" in AuthorizeOperation.SEL_PHONE_INPUT
    assert "applicant-login-input-email" in AuthorizeOperation.SEL_EMAIL_INPUT
    assert AuthorizeOperation._national_phone("+7 999 123-45-67") == "9991234567"


class _AuthLocator:
    def __init__(self, *, count: int = 1) -> None:
        self._count = count
        self.clicked: list[bool] = []
        self.filled: list[str] = []
        self.pressed: list[str] = []

    async def count(self) -> int:
        return self._count

    @property
    def first(self):
        return self

    async def click(self, *, force: bool = False) -> None:
        self.clicked.append(force)

    async def fill(self, value: str) -> None:
        self.filled.append(value)

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class _AuthPage:
    def __init__(self, selectors: dict[str, _AuthLocator]) -> None:
        self.selectors = selectors
        self.waited: list[str] = []
        self.fallback_fills: list[tuple[str, str]] = []

    async def wait_for_selector(self, selector: str, **_kwargs):
        self.waited.append(selector)
        return self.selectors.get(selector)

    def locator(self, selector: str) -> _AuthLocator:
        return self.selectors.setdefault(selector, _AuthLocator(count=0))

    async def fill(self, selector: str, value: str) -> None:
        self.fallback_fills.append((selector, value))


def _authorize_operation() -> AuthorizeOperation:
    operation = AuthorizeOperation()
    operation._args = SimpleNamespace(
        no_headless=True,
        manual=False,
        use_kitty=False,
        use_sixel=False,
    )
    return operation


def test_authorize_fills_current_email_field() -> None:
    operation = _authorize_operation()
    email_tab = _AuthLocator()
    email_input = _AuthLocator()
    page = _AuthPage(
        {
            operation.SEL_EMAIL_TAB: email_tab,
            operation.SEL_EMAIL_INPUT: email_input,
            operation.SEL_PHONE_INPUT: _AuthLocator(count=1),
        }
    )

    asyncio.run(operation._fill_username(page, "person@example.com"))

    assert email_tab.clicked == [True]
    assert email_input.filled == ["person@example.com"]
    assert page.fallback_fills == []


def test_authorize_fills_current_phone_field_without_country_prefix() -> None:
    operation = _authorize_operation()
    phone_input = _AuthLocator()
    page = _AuthPage(
        {
            operation.SEL_EMAIL_TAB: _AuthLocator(count=0),
            operation.SEL_EMAIL_INPUT: _AuthLocator(count=0),
            operation.SEL_PHONE_INPUT: phone_input,
        }
    )

    asyncio.run(operation._fill_username(page, "+7 999 123-45-67"))

    assert phone_input.filled == ["9991234567"]


def test_authorize_password_flow_submits_current_password_field() -> None:
    operation = _authorize_operation()
    expand = _AuthLocator()
    password = _AuthLocator()
    page = _AuthPage(
        {
            operation.SEL_EXPAND_PASSWORD: expand,
            operation.SEL_PASSWORD_INPUT: password,
        }
    )
    operation._handle_captcha = AsyncMock()

    asyncio.run(operation._direct_login(page, "secret-password"))

    assert expand.clicked == [True]
    operation._handle_captcha.assert_awaited_once_with(page)
    assert password.filled == ["secret-password"]
    assert password.pressed == ["Enter"]


def test_authorize_captcha_without_interactive_renderer_fails_closed() -> None:
    operation = _authorize_operation()
    captcha = AsyncMock()
    page = _AuthPage({operation.SEL_CAPTCHA_IMAGE: captcha})

    try:
        asyncio.run(operation._handle_captcha(page))
    except RuntimeError as ex:
        assert "Требуется ввод капчи" in str(ex)
    else:
        raise AssertionError("captcha must not be silently bypassed")


def test_apply_parser_accepts_work_format() -> None:
    parser = argparse.ArgumentParser()
    operation = ApplyOperation()
    operation.setup_parser(parser)

    args = parser.parse_args(["--work-format", "REMOTE", "HYBRID"])

    assert args.work_format == ["REMOTE", "HYBRID"]


def test_vacancy_test_parse_failure_falls_back_to_normal_apply() -> None:
    operation = ApplyOperation()
    operation.tool = MagicMock()
    operation.tool.api_client.post.return_value = {}
    operation.dry_run = False
    operation.response_delay_min = 0.0
    operation.response_delay_max = 0.0
    operation._solve_vacancy_test = MagicMock(
        side_effect=ValueError("HH vacancy test state is unavailable")
    )
    vacancy = {
        "id": "123",
        "alternate_url": "https://hh.ru/vacancy/123",
        "has_test": True,
    }

    result = operation._send_vacancy_response(vacancy, "resume-1", "hello")

    assert result.accepted is True
    operation.tool.api_client.post.assert_called_once()


def test_parse_scalar_is_case_insensitive() -> None:
    assert parse_scalar("TRUE") is True
    assert parse_scalar("False") is False
    assert parse_scalar("NULL") is None
