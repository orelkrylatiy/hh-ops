from __future__ import annotations

import argparse
import html
import json
from http.cookiejar import Cookie, CookieJar
from unittest.mock import MagicMock

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
