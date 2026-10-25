from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from hh_applicant_tool.operations.apply_vacancies import Operation as ApplyOperation
from hh_applicant_tool.operations.reply_employers import Operation as ReplyOperation


def make_apply_args(**overrides):
    defaults = {
        "resume_id": None,
        "letter_file": None,
        "ignore_employers": None,
        "force_message": False,
        "use_ai": False,
        "ai_filter": None,
        "ai_rate_limit": 40,
        "system_prompt": "system",
        "message_prompt": "message",
        "response_delay": "1-3",
        "order_by": None,
        "search": None,
        "search_field": None,
        "schedule": None,
        "dry_run": False,
        "response_delay_min": 0.0,
        "response_delay_max": 0.0,
        "experience": None,
        "employment": None,
        "area": None,
        "metro": None,
        "professional_role": None,
        "industry": None,
        "employer_id": None,
        "excluded_employer_id": None,
        "currency": None,
        "salary": None,
        "only_with_salary": False,
        "label": None,
        "period": None,
        "date_from": None,
        "date_to": None,
        "top_lat": None,
        "bottom_lat": None,
        "left_lng": None,
        "right_lng": None,
        "sort_point_lat": None,
        "sort_point_lng": None,
        "no_magic": False,
        "premium": False,
        "per_page": 100,
        "total_pages": 20,
        "excluded_filter": None,
        "max_responses": None,
        "send_email": False,
        "skip_tests": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_reply_args(**overrides):
    defaults = {
        "resume_id": None,
        "reply_message": None,
        "max_pages": 5,
        "dry_run": False,
        "only_invitations": False,
        "message_prompt": "prompt",
        "use_ai": False,
        "system_prompt": "system",
        "period": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_apply_vacancies_parses_single_delay_value() -> None:
    operation = ApplyOperation()

    assert operation._parse_response_delay("2.5") == (2.5, 2.5)


def test_apply_vacancies_falls_back_on_invalid_delay() -> None:
    operation = ApplyOperation()

    assert operation._parse_response_delay("oops") == (1.0, 3.0)


def test_apply_vacancies_run_assigns_selected_args(monkeypatch) -> None:
    operation = ApplyOperation()
    tool = Mock()
    tool.get_cover_letter_ai.return_value = Mock()
    monkeypatch.setattr(operation, "_apply_vacancies", Mock())
    args = make_apply_args(
        search="frontend",
        per_page=50,
        response_delay="4-6",
        use_ai=True,
    )

    operation.run(tool, args)

    assert operation.search == "frontend"
    assert operation.per_page == 50
    assert operation.response_delay_min == 4.0
    assert operation.response_delay_max == 6.0
    assert operation.cover_letter_ai is tool.get_cover_letter_ai.return_value


def test_apply_vacancies_run_fails_when_ai_letters_fail(monkeypatch) -> None:
    operation = ApplyOperation()
    tool = Mock()
    tool.get_cover_letter_ai.return_value = Mock()

    def record_ai_failure():
        operation.ai_error_count = 1

    monkeypatch.setattr(operation, "_apply_vacancies", record_ai_failure)
    args = make_apply_args(use_ai=True)

    assert operation.run(tool, args) == 1


def test_apply_dry_run_does_not_blacklist_or_persist_excluded_vacancy() -> None:
    operation = ApplyOperation()
    operation.dry_run = True
    operation.tool = Mock()
    operation._args = SimpleNamespace(skip_tests=False)
    operation._is_excluded = Mock(return_value=True)
    operation._save_skipped_vacancy = Mock()
    vacancy = {
        "id": "vacancy-1",
        "alternate_url": "https://example.test/vacancy-1",
        "relations": [],
        "archived": False,
        "has_test": False,
        "response_url": None,
    }

    assert operation._should_skip_vacancy_basic(vacancy, "resume-1") is True
    operation.tool.api_client.put.assert_not_called()
    operation._save_skipped_vacancy.assert_not_called()


def test_reply_employers_prefers_explicit_resume_id() -> None:
    operation = ReplyOperation()
    tool = Mock()
    tool.api_client = Mock()
    tool.config = {}
    operation.reply_employers = Mock()

    operation.run(tool, make_reply_args(resume_id="chosen"))

    assert operation.resume_id == "chosen"


def test_reply_employers_without_resume_id_keeps_all_resumes_mode() -> None:
    operation = ReplyOperation()
    tool = Mock()
    tool.api_client = Mock()
    tool.config = {}
    operation.reply_employers = Mock()

    operation.run(tool, make_reply_args())

    assert operation.resume_id is None


def test_reply_employers_ai_uses_framework_without_configured_provider() -> None:
    operation = ReplyOperation()
    tool = Mock()
    tool.api_client = Mock()
    tool.config = {}
    operation.reply_employers = Mock()

    operation.run(tool, make_reply_args(use_ai=True))

    assert operation.reply_ai is True
    tool.get_reply_ai.assert_not_called()
    tool.get_cover_letter_ai.assert_not_called()


def test_reply_employers_ai_ignores_missing_legacy_providers() -> None:
    operation = ReplyOperation()
    tool = Mock()
    tool.api_client = Mock()
    tool.config = {}
    tool.get_reply_ai.side_effect = ValueError("openai_reply missing")
    tool.get_cover_letter_ai.side_effect = ValueError("cover provider missing")
    operation.reply_employers = Mock()

    operation.run(tool, make_reply_args(use_ai=True))

    assert operation.reply_ai is True
    tool.get_reply_ai.assert_not_called()
    tool.get_cover_letter_ai.assert_not_called()
    operation.reply_employers.assert_called_once()
