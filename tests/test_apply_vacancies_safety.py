from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hh_applicant_tool.operations._apply_vacancies_apply_flow import (
    VacancyResponseResult,
)
from hh_applicant_tool.operations.apply_vacancies import Operation


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
    operation.args = SimpleNamespace(skip_tests=False)
    negotiation_repo = Mock()
    negotiation_repo.find.return_value = iter([object()])
    operation.tool = SimpleNamespace(
        storage=SimpleNamespace(negotiations=negotiation_repo)
    )
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
    operation.args = SimpleNamespace(skip_tests=False)
    negotiation_repo = Mock()
    negotiation_repo.find.return_value = iter([])
    operation.tool = SimpleNamespace(
        storage=SimpleNamespace(negotiations=negotiation_repo)
    )
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
    operation.args = SimpleNamespace(skip_tests=False)
    negotiation_repo = Mock()
    negotiation_repo.find.side_effect = TypeError("db unavailable")
    operation.tool = SimpleNamespace(
        storage=SimpleNamespace(negotiations=negotiation_repo)
    )
    operation._is_excluded = Mock(return_value=False)

    should_skip = operation._should_skip_vacancy_basic(
        _vacancy("125"),
        "resume-ai",
    )

    assert should_skip is False
    operation._is_excluded.assert_called_once()
