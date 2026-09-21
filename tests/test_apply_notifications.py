from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

from hh_applicant_tool.notifications import telegram
from hh_applicant_tool.operations._apply_vacancies_apply_flow import (
    VacancyResponseResult,
)
from hh_applicant_tool.operations.apply_vacancies import Operation


def _vacancy() -> dict[str, object]:
    return {
        "id": "vacancy-42",
        "name": "AI Engineer",
        "alternate_url": "https://hh.ru/vacancy/42",
        "employer": {"id": "company-1", "name": "Example AI"},
        "relations": [],
        "archived": False,
        "has_test": False,
        "response_url": None,
    }


def _resume() -> dict[str, object]:
    return {
        "id": "resume-ai",
        "title": "AI Engineer / LLM",
        "alternate_url": "https://hh.ru/resume/resume-ai",
    }


def _flow(*, dry_run: bool, accepted: bool) -> Operation:
    operation = Operation()
    operation.dry_run = dry_run
    operation.max_responses = 10
    operation.responses_sent = 0
    operation.force_message = True
    operation.resume_alias = "ai-engineer"
    operation._init_ai_filter_for_resume = Mock()
    operation._get_vacancies = Mock(return_value=iter([_vacancy()]))
    operation._save_vacancy_data = Mock()
    operation._should_skip_vacancy_basic = Mock(return_value=False)
    operation._should_skip_by_ai = Mock(return_value=False)
    operation._load_employer_contacts = Mock(return_value=None)
    operation._build_cover_letter = Mock(return_value="cover letter")
    operation._send_vacancy_response = Mock(
        return_value=VacancyResponseResult(
            should_continue=True,
            accepted=accepted,
        )
    )
    operation._send_vacancy_email_if_needed = Mock()
    operation._record_application_success = Mock()
    return operation


def test_success_message_contains_vacancy_company_and_resume_alias() -> None:
    message = telegram.build_application_success_message(
        profile="0555",
        vacancy=_vacancy(),
        resume_title="AI Engineer / LLM",
        resume_id="resume-ai",
        resume_alias="ai-engineer",
    )

    assert message.startswith("✅ Отклик отправлен")
    assert "Профиль: 0555" in message
    assert "Вакансия: AI Engineer" in message
    assert "Компания: Example AI" in message
    assert "Резюме: AI Engineer / LLM (alias: ai-engineer)" in message
    assert "https://hh.ru/vacancy/42" in message


def test_disabled_notifier_never_opens_network(monkeypatch) -> None:
    monkeypatch.setenv("HH_NOTIFY_TELEGRAM_ENABLED", "0")
    monkeypatch.setattr(
        telegram.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network must stay disabled")
        ),
    )

    assert telegram.notify_application_success(
        profile="default",
        vacancy=_vacancy(),
        resume_title="AI Engineer",
        resume_id="resume-ai",
    ) is False


def test_enabled_notifier_posts_plain_telegram_message(monkeypatch) -> None:
    telegram._failure_cooldown_until = 0.0
    monkeypatch.setenv("HH_NOTIFY_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("HH_NOTIFY_TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("HH_NOTIFY_TELEGRAM_CHAT_ID", "123")
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)

    assert telegram.notify_application_success(
        profile="0555",
        vacancy=_vacancy(),
        resume_title="AI Engineer / LLM",
        resume_id="resume-ai",
        resume_alias="ai-engineer",
    ) is True
    assert captured["timeout"] == 3.0
    assert captured["body"]["chat_id"] == "123"
    assert "AI Engineer" in captured["body"]["text"]


def test_notifier_failure_never_logs_bot_token(monkeypatch, caplog) -> None:
    telegram._failure_cooldown_until = 0.0
    token = "do-not-log-this-token"
    monkeypatch.setenv("HH_NOTIFY_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("HH_NOTIFY_TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("HH_NOTIFY_TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(
        telegram.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("network down")),
    )

    with caplog.at_level(logging.WARNING):
        assert telegram.notify_application_success(
            profile="0555",
            vacancy=_vacancy(),
            resume_title="AI Engineer",
            resume_id="resume-ai",
        ) is False

    assert token not in caplog.text


def test_live_accepted_application_emits_success_event_once() -> None:
    operation = _flow(dry_run=False, accepted=True)

    operation._apply_resume(_resume(), {}, set())

    assert operation.responses_sent == 1
    operation._record_application_success.assert_called_once_with(
        _vacancy(),
        _resume(),
    )


def test_dry_run_accepted_application_does_not_emit_success_event() -> None:
    operation = _flow(dry_run=True, accepted=True)

    operation._apply_resume(_resume(), {}, set())

    assert operation.responses_sent == 1
    operation._record_application_success.assert_not_called()


def test_failed_application_does_not_emit_success_event() -> None:
    operation = _flow(dry_run=False, accepted=False)

    operation._apply_resume(_resume(), {}, set())

    assert operation.responses_sent == 0
    operation._record_application_success.assert_not_called()


def test_success_audit_survives_telegram_failure(monkeypatch, caplog) -> None:
    operation = Operation()
    operation.tool = SimpleNamespace(profile_id="0555")
    operation.resume_alias = "ai-engineer"
    monkeypatch.setattr(
        telegram,
        "notify_application_success",
        Mock(side_effect=RuntimeError("telegram down")),
    )

    with caplog.at_level(logging.INFO):
        operation._record_application_success(_vacancy(), _resume())

    assert "HH_APPLY_SUCCESS profile=0555 vacancy_id=vacancy-42" in caplog.text
    assert "HH_APPLY_NOTIFY_FAILED profile=0555 vacancy_id=vacancy-42" in caplog.text
