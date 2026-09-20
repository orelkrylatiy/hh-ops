from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hh_applicant_tool.ai.base import AIError
from hh_applicant_tool.operations._apply_vacancies_apply_flow import VacancyResponseResult
from hh_applicant_tool.operations.apply_safe import Operation


def _vacancy() -> dict[str, object]:
    return {
        "id": "vacancy-1",
        "name": "Frontend developer",
        "alternate_url": "https://example.test/vacancies/1",
        "employer": {"name": "Example"},
        "relations": [],
        "archived": False,
        "has_test": False,
        "response_url": None,
    }


def _resume() -> dict[str, object]:
    return {
        "id": "resume-1",
        "title": "Frontend developer",
        "alternate_url": "https://example.test/resumes/1",
    }


def _placeholders() -> dict[str, str]:
    return {
        "first_name": "Максим",
        "last_name": "",
        "email": "",
        "phone": "",
        "resume_hash": "resume-1",
        "resume_title": "Frontend developer",
        "resume_url": "https://example.test/resumes/1",
        "vacancy_name": "Frontend developer",
        "employer_name": "Example",
    }


def _args(*extra: str) -> argparse.Namespace:
    operation = Operation()
    parser = argparse.ArgumentParser()
    operation.setup_parser(parser)
    return parser.parse_args(list(extra))


def test_ai_initialization_failure_keeps_application_batch_running() -> None:
    operation = Operation()
    operation._apply_vacancies = Mock()
    args = _args("--ai", "--force-message")
    tool = SimpleNamespace(
        config={
            "cover_letter_fallback": {
                "enabled": True,
                "message": "Здравствуйте, %(first_name)s. Рассмотрите мое резюме.",
            }
        },
        get_cover_letter_ai=Mock(side_effect=ValueError("provider unavailable")),
    )

    result = operation.run(tool, args)

    assert result is None
    assert operation.cover_letter_ai is None
    operation._apply_vacancies.assert_called_once_with()


def test_runtime_ai_failure_uses_static_configured_cover_letter() -> None:
    operation = Operation()
    operation.force_message = True
    operation.cover_letter_fallback_enabled = True
    operation.cover_letter = "Здравствуйте, %(first_name)s. Рассмотрите мое резюме."
    operation.cover_fallback_count = 0
    operation.message_prompt = "Сгенерируй письмо"
    operation._build_cover_letter_context = Mock(return_value="context")
    operation.cover_letter_ai = Mock()
    operation.cover_letter_ai.complete.side_effect = AIError("LLM down")

    letter = operation._build_cover_letter(_vacancy(), _resume(), _placeholders())

    assert letter == "Здравствуйте, Максим. Рассмотрите мое резюме."
    assert operation.cover_fallback_count == 1


def test_empty_ai_cover_letter_uses_static_fallback() -> None:
    operation = Operation()
    operation.force_message = True
    operation.cover_letter_fallback_enabled = True
    operation.cover_letter = "Fallback for %(vacancy_name)s"
    operation.cover_fallback_count = 0
    operation.message_prompt = "Сгенерируй письмо"
    operation._build_cover_letter_context = Mock(return_value="context")
    operation.cover_letter_ai = Mock()
    operation.cover_letter_ai.complete.return_value = "   "

    letter = operation._build_cover_letter(_vacancy(), _resume(), _placeholders())

    assert letter == "Fallback for Frontend developer"
    assert operation.cover_fallback_count == 1


def test_generated_cover_letter_is_sent_as_negotiation_message() -> None:
    operation = Operation()
    operation.dry_run = False
    operation.force_message = True
    operation.response_delay_min = 0
    operation.response_delay_max = 0
    api_client = Mock()
    api_client.post.return_value = {}
    operation.tool = SimpleNamespace(api_client=api_client)

    result = operation._send_vacancy_response(
        _vacancy(),
        "resume-1",
        "Конкретное сопроводительное письмо",
    )

    assert result == VacancyResponseResult(should_continue=True, accepted=True)
    endpoint, params = api_client.post.call_args.args[:2]
    assert endpoint == "/negotiations"
    assert params["resume_id"] == "resume-1"
    assert params["vacancy_id"] == "vacancy-1"
    assert params["message"] == "Конкретное сопроводительное письмо"


def test_force_message_refuses_to_apply_with_empty_cover_letter() -> None:
    operation = Operation()
    operation.dry_run = False
    operation.force_message = True
    operation.tool = SimpleNamespace(api_client=Mock())

    result = operation._send_vacancy_response(_vacancy(), "resume-1", "   ")

    assert result == VacancyResponseResult(should_continue=True, accepted=False)
    operation.tool.api_client.post.assert_not_called()


def test_fallback_can_be_explicitly_disabled() -> None:
    operation = Operation()
    operation._apply_vacancies = Mock()
    args = _args("--ai", "--force-message")
    tool = SimpleNamespace(
        config={
            "cover_letter_fallback": {
                "enabled": False,
                "message": "Static fallback",
            }
        },
        get_cover_letter_ai=Mock(side_effect=ValueError("provider unavailable")),
    )

    with pytest.raises(ValueError, match="provider unavailable"):
        operation.run(tool, args)

    operation._apply_vacancies.assert_not_called()


def test_apply_safe_resolves_resume_alias_before_batch() -> None:
    operation = Operation()
    operation._apply_vacancies = Mock()
    args = _args("--resume-alias", "ai-engineer")
    tool = SimpleNamespace(
        config={
            "resume_aliases": {
                "primary": "resume-front",
                "ai-engineer": "resume-ai",
            },
            "cover_letter_fallback": {
                "enabled": True,
                "message": "Static fallback",
            },
        },
        get_cover_letter_ai=Mock(),
    )

    result = operation.run(tool, args)

    assert result is None
    assert operation.resume_id == "resume-ai"
    operation._apply_vacancies.assert_called_once_with()
