from __future__ import annotations

import argparse
import asyncio
from http.cookiejar import Cookie, CookieJar
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hh_applicant_tool.operations._apply_vacancies_apply_flow import (
    VacancyResponseResult,
)
from hh_applicant_tool.operations.apply_vacancies import (
    CAPTCHA_SUCCESS_BACKURL,
    Operation,
    _captcha_browser_url,
    _effective_captcha_proxy,
    _playwright_cookies,
    _wait_for_captcha_image,
)


def test_captcha_browser_url_adds_required_applicant_callback() -> None:
    result = _captcha_browser_url("https://hh.ru/account/captcha?state=abc123")

    assert result.startswith("https://hh.ru/account/captcha?")
    assert "state=abc123" in result
    assert "backurl=hh%3A%2F%2Fapplicant%2FcaptchaSuccessBackUrl" in result


def test_captcha_browser_url_preserves_existing_callback() -> None:
    result = _captcha_browser_url(
        "https://hh.ru/account/captcha?state=abc&backurl=https%3A%2F%2Fexample.test"
    )

    assert "backurl=https%3A%2F%2Fexample.test" in result
    assert CAPTCHA_SUCCESS_BACKURL not in result


def _vacancy(vacancy_id: str) -> dict[str, object]:
    return {
        "id": vacancy_id,
        "name": f"Vacancy {vacancy_id}",
        "alternate_url": f"https://example.test/vacancies/{vacancy_id}",
        "employer": {"id": f"employer-{vacancy_id}", "name": "Example"},
        "relations": [],
        "archived": False,
        "has_test": False,
        "response_url": None,
    }


def _resume() -> dict[str, object]:
    return {
        "id": "resume-1",
        "title": "Backend developer",
        "alternate_url": "https://example.test/resumes/resume-1",
    }


def test_effective_captcha_proxy_includes_environment_proxy(monkeypatch) -> None:
    monkeypatch.setattr(
        "hh_applicant_tool.operations.apply_vacancies.get_environ_proxies",
        Mock(return_value={"https": "http://127.0.0.1:10809"}),
    )
    session = SimpleNamespace(proxies={}, trust_env=True)

    assert (
        _effective_captcha_proxy(session, "https://hh.ru/account/captcha")
        == "http://127.0.0.1:10809"
    )


def test_effective_captcha_proxy_prefers_explicit_session_proxy(monkeypatch) -> None:
    monkeypatch.setattr(
        "hh_applicant_tool.operations.apply_vacancies.get_environ_proxies",
        Mock(return_value={"https": "http://127.0.0.1:10809"}),
    )
    session = SimpleNamespace(proxies={"https": "http://explicit-proxy.test:8080"}, trust_env=True)

    assert (
        _effective_captcha_proxy(session, "https://hh.ru/account/captcha")
        == "http://explicit-proxy.test:8080"
    )


def test_playwright_cookies_uses_current_in_memory_cookie_jar() -> None:
    jar = CookieJar()
    jar.set_cookie(
        Cookie(
            version=0,
            name="hhuid",
            value="fresh-value",
            port=None,
            port_specified=False,
            domain=".hh.ru",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
        )
    )

    assert _playwright_cookies(jar) == [
        {
            "name": "hhuid",
            "value": "fresh-value",
            "domain": ".hh.ru",
            "path": "/",
            "expires": -1,
            "secure": True,
        }
    ]


def test_wait_for_captcha_image_passes_selector_as_keyword_argument() -> None:
    page = SimpleNamespace(wait_for_function=AsyncMock())

    asyncio.run(_wait_for_captcha_image(page, "img[data-qa='captcha']"))

    _, kwargs = page.wait_for_function.await_args
    assert kwargs == {"arg": "img[data-qa='captcha']", "timeout": 15000}


def _operation_for_resume(
    vacancies: list[dict[str, object]],
    outcomes: list[VacancyResponseResult],
    *,
    max_responses: int | None,
) -> Operation:
    operation = Operation()
    operation.dry_run = False
    operation.max_responses = max_responses
    operation.responses_sent = 0
    operation.force_message = False
    operation._init_ai_filter_for_resume = Mock()
    operation._get_vacancies = Mock(return_value=iter(vacancies))
    operation._save_vacancy_data = Mock()
    operation._should_skip_vacancy_basic = Mock(return_value=False)
    operation._should_skip_by_ai = Mock(return_value=False)
    operation._load_employer_contacts = Mock(return_value=None)
    operation._build_cover_letter = Mock(return_value="letter")
    operation._send_vacancy_response = Mock(side_effect=outcomes)
    operation._send_vacancy_email_if_needed = Mock()
    return operation


def test_dry_run_never_posts_a_vacancy_response() -> None:
    operation = Operation()
    operation.dry_run = True
    operation.response_delay_min = 0
    operation.response_delay_max = 0
    operation.tool = SimpleNamespace(api_client=Mock())

    result = operation._send_vacancy_response(_vacancy("1"), "resume-1", "letter")

    assert result == VacancyResponseResult(should_continue=True, accepted=True)
    operation.tool.api_client.post.assert_not_called()


@pytest.mark.parametrize("dry_run", [False, True])
def test_empty_search_reports_zero_sent_or_planned(dry_run, capsys):
    operation = Operation()
    operation.dry_run = dry_run
    operation.resume_id = None
    operation.max_responses = None
    operation.responses_sent = 0
    operation.ai_error_count = 0
    resume = {**_resume(), "status": {"id": "published"}}
    operation.tool = SimpleNamespace(
        get_resumes=Mock(return_value=[resume]),
        get_me=Mock(return_value={}),
        get_negotiations=Mock(return_value=iter([])),
        storage=SimpleNamespace(resumes=Mock()),
    )
    operation._apply_resume = Mock()

    operation._apply_vacancies()

    output = capsys.readouterr().out
    assert "откликов — 0" in output
    assert ("запланировано" if dry_run else "отправлено") in output
    assert "разосланы!" not in output


def test_successful_non_empty_api_response_still_counts_as_an_application() -> None:
    operation = Operation()
    operation.dry_run = False
    operation.response_delay_min = 0
    operation.response_delay_max = 0
    operation.tool = SimpleNamespace(api_client=Mock())
    operation.tool.api_client.post.return_value = {"id": "negotiation-1"}

    result = operation._send_vacancy_response(_vacancy("1"), "resume-1", "letter")

    assert result == VacancyResponseResult(should_continue=True, accepted=True)


def test_dry_run_never_sends_vacancy_email() -> None:
    operation = Operation()
    operation.dry_run = True
    operation._args = SimpleNamespace(send_email=True)
    operation._send_email = Mock()

    operation._send_vacancy_email_if_needed(
        _vacancy("1"),
        employer_id=None,
        site_emails={},
        message_placeholders={},
    )

    operation._send_email.assert_not_called()


def test_smtp_helper_defends_against_dry_run_calls() -> None:
    operation = Operation()
    operation.dry_run = True
    smtp = Mock()
    operation.tool = SimpleNamespace(config={"smtp": {}}, smtp=smtp)

    operation._send_email("recruiter@example.test", "Subject", "Body")

    smtp.send_message.assert_not_called()


def test_vacancy_email_reuses_framework_letter_and_deduplicates(monkeypatch, tmp_path) -> None:
    import sqlite3

    monkeypatch.setenv("HH_EMAIL_LEDGER_PATH", str(tmp_path / "global.sqlite"))
    operation = Operation()
    operation.dry_run = False
    operation._args = SimpleNamespace(send_email=True)
    operation.tool = SimpleNamespace(config={}, db=sqlite3.connect(":memory:"))
    operation._send_email = Mock()
    vacancy = _vacancy("1")
    vacancy["contacts"] = {"email": "recruiter@example.test"}
    letter = (
        "Здравствуйте! Более пяти лет разрабатываю интерфейсы на React и TypeScript. "
        "Работал над интерфейсами в финтехе и вижу связь этого опыта с задачами "
        "вашей вакансии. Буду рад обсудить детали проекта."
    )
    placeholders = {"vacancy_name": "Frontend developer", "resume_url": "https://hh.ru/resume/1"}

    for _ in range(2):
        operation._send_vacancy_email_if_needed(vacancy, None, {}, placeholders, letter)

    operation._send_email.assert_called_once()
    recipient, subject, body = operation._send_email.call_args.args
    assert recipient == "recruiter@example.test"
    assert "Frontend developer" in subject
    assert letter in body
    assert "https://hh.ru/resume/1" in body
    status = operation.tool.db.execute(
        "SELECT status FROM vacancy_email_sends WHERE vacancy_id=?", ("1",)
    ).fetchone()[0]
    assert status == "sent"


def test_vacancy_email_uses_local_gmail_api_when_configured(monkeypatch, tmp_path) -> None:
    import sqlite3

    monkeypatch.setenv("HH_EMAIL_LEDGER_PATH", str(tmp_path / "global.sqlite"))
    gmail = Mock()
    monkeypatch.setenv("HH_GMAIL_DIR", "test-gmail-dir")
    monkeypatch.setattr(
        "hh_applicant_tool.operations._apply_vacancies_apply_flow.GmailAPISender",
        Mock(return_value=gmail),
    )
    operation = Operation()
    operation.dry_run = False
    operation._args = SimpleNamespace(send_email=True)
    operation.tool = SimpleNamespace(config={}, db=sqlite3.connect(":memory:"))
    operation._send_email = Mock()
    vacancy = _vacancy("1")
    vacancy["contacts"] = {"email": "recruiter@example.test"}
    letter = (
        "Здравствуйте! Более пяти лет разрабатываю интерфейсы на React и TypeScript. "
        "Работал над интерфейсами в финтехе и вижу связь этого опыта с задачами "
        "вашей вакансии. Буду рад обсудить детали проекта."
    )

    operation._send_vacancy_email_if_needed(
        vacancy, None, {}, {"vacancy_name": "Frontend developer"}, letter
    )

    gmail.send.assert_called_once()
    operation._send_email.assert_not_called()


def test_invalid_gmail_oauth_disables_email_without_blocking_apply(monkeypatch) -> None:
    monkeypatch.setenv("HH_GMAIL_DIR", "test-gmail-dir")
    gmail = Mock()
    gmail.profile_email.side_effect = ValueError("expired refresh token")
    monkeypatch.setattr(
        "hh_applicant_tool.operations._apply_vacancies_apply_flow.GmailAPISender",
        Mock(return_value=gmail),
    )
    operation = Operation()
    operation._args = SimpleNamespace(send_email=True)
    operation.tool = SimpleNamespace(config={})

    operation._validate_email_config()

    assert operation.args.send_email is False


def test_global_email_ledger_blocks_same_recipient_across_profiles(tmp_path) -> None:
    from hh_applicant_tool.communication.global_email_log import GlobalEmailSendLog

    path = tmp_path / "global.sqlite"
    first = GlobalEmailSendLog(path)
    second = GlobalEmailSendLog(path)

    assert first.claim("Recruiter@Example.test", "1", "profile-a", "Subject")
    first.mark_sent("Recruiter@Example.test")
    assert not second.claim("recruiter@example.test", "2", "profile-b", "Subject")

    first.close()
    second.close()


def test_max_responses_stops_after_successful_responses_only() -> None:
    operation = _operation_for_resume(
        [_vacancy("1"), _vacancy("2"), _vacancy("3")],
        [
            VacancyResponseResult(should_continue=True, accepted=False),
            VacancyResponseResult(should_continue=True, accepted=True),
            VacancyResponseResult(should_continue=True, accepted=True),
        ],
        max_responses=1,
    )

    operation._apply_resume(_resume(), {}, set())

    assert operation.responses_sent == 1
    assert operation._send_vacancy_response.call_count == 2
    assert operation._send_vacancy_email_if_needed.call_count == 1


def test_zero_max_responses_does_not_inspect_or_send_vacancies() -> None:
    operation = _operation_for_resume(
        [_vacancy("1")],
        [VacancyResponseResult(should_continue=True, accepted=True)],
        max_responses=0,
    )

    operation._apply_resume(_resume(), {}, set())

    operation._get_vacancies.assert_not_called()
    operation._send_vacancy_response.assert_not_called()
    assert operation.responses_sent == 0


def test_max_responses_is_shared_by_all_resumes_in_one_run() -> None:
    operation = Operation()
    operation.dry_run = False
    operation.max_responses = 1
    operation.responses_sent = 0
    operation.resume_id = None
    operation.tool = Mock()
    operation.tool.get_resumes.return_value = [
        {**_resume(), "status": {"id": "published"}},
        {
            **_resume(),
            "id": "resume-2",
            "status": {"id": "published"},
        },
    ]
    operation.tool.get_me.return_value = {}
    operation.tool.get_negotiations.return_value = []

    def accept_one_response(**_: object) -> None:
        operation.responses_sent += 1

    operation._apply_resume = Mock(side_effect=accept_one_response)

    operation._apply_vacancies()

    assert operation._apply_resume.call_count == 1


def test_max_responses_parser_rejects_negative_values() -> None:
    operation = Operation()
    parser = argparse.ArgumentParser()
    operation.setup_parser(parser)

    assert parser.parse_args(["--max-responses", "0"]).max_responses == 0
    with pytest.raises(SystemExit):
        parser.parse_args(["--max-responses", "-1"])


def test_resume_id_and_alias_are_mutually_exclusive() -> None:
    operation = Operation()
    parser = argparse.ArgumentParser()
    operation.setup_parser(parser)

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--resume-id",
                "resume-1",
                "--resume-alias",
                "ai-engineer",
            ]
        )


def test_resume_alias_resolves_to_exact_resume_id() -> None:
    operation = Operation()
    operation.resume_alias = "ai-engineer"
    operation.resume_id = None
    tool = SimpleNamespace(
        config={
            "resume_aliases": {
                "primary": "resume-front",
                "ai-engineer": "resume-ai",
            }
        }
    )

    operation._resolve_resume_selector(tool)

    assert operation.resume_id == "resume-ai"


def test_unknown_resume_alias_fails_closed() -> None:
    operation = Operation()
    operation.resume_alias = "missing"
    operation.resume_id = None
    tool = SimpleNamespace(config={"resume_aliases": {"primary": "resume-front"}})

    with pytest.raises(ValueError, match="Unknown resume alias"):
        operation._resolve_resume_selector(tool)


def test_existing_local_negotiation_skips_vacancy_before_apply() -> None:
    operation = Operation()
    operation.dry_run = False
    operation._args = SimpleNamespace(skip_tests=False)
    negotiation_repo = Mock()
    negotiation_repo.find.return_value = iter([object()])
    operation.tool = SimpleNamespace(storage=SimpleNamespace(negotiations=negotiation_repo))
    operation._is_excluded = Mock(return_value=False)

    should_skip = operation._should_skip_vacancy_basic(
        _vacancy("123"),
        "resume-ai",
    )

    assert should_skip is True
    negotiation_repo.find.assert_called_once_with(vacancy_id=123)
    operation._is_excluded.assert_not_called()


def test_missing_local_negotiation_keeps_existing_hh_relation_logic() -> None:
    operation = Operation()
    operation.dry_run = False
    operation._args = SimpleNamespace(skip_tests=False)
    negotiation_repo = Mock()
    negotiation_repo.find.return_value = iter([])
    operation.tool = SimpleNamespace(storage=SimpleNamespace(negotiations=negotiation_repo))
    operation._is_excluded = Mock(return_value=False)

    vacancy = _vacancy("124")
    vacancy["relations"] = ["got_response"]

    should_skip = operation._should_skip_vacancy_basic(
        vacancy,
        "resume-ai",
    )

    assert should_skip is True
    operation._is_excluded.assert_not_called()


def test_negotiation_lookup_failure_falls_back_to_hh_checks() -> None:
    operation = Operation()
    operation.dry_run = False
    operation._args = SimpleNamespace(skip_tests=False)
    negotiation_repo = Mock()
    negotiation_repo.find.side_effect = TypeError("db unavailable")
    operation.tool = SimpleNamespace(storage=SimpleNamespace(negotiations=negotiation_repo))
    operation._is_excluded = Mock(return_value=False)

    should_skip = operation._should_skip_vacancy_basic(
        _vacancy("125"),
        "resume-ai",
    )

    assert should_skip is False
    operation._is_excluded.assert_called_once()


def test_missing_primary_alias_infers_only_published_resume() -> None:
    operation = Operation()
    operation.resume_alias = "primary"
    operation.resume_id = None
    tool = SimpleNamespace(
        config={"resume_aliases": {}},
        get_resumes=lambda: [
            {
                "id": "resume-front",
                "status": {"id": "published"},
            }
        ],
    )

    operation._resolve_resume_selector(tool)

    assert operation.resume_id == "resume-front"


def test_missing_primary_alias_with_multiple_published_resumes_fails_closed() -> None:
    operation = Operation()
    operation.resume_alias = "primary"
    operation.resume_id = None
    tool = SimpleNamespace(
        config={"resume_aliases": {}},
        get_resumes=lambda: [
            {"id": "resume-front", "status": {"id": "published"}},
            {"id": "resume-ai", "status": {"id": "published"}},
        ],
    )

    with pytest.raises(ValueError, match="cannot be inferred safely"):
        operation._resolve_resume_selector(tool)
