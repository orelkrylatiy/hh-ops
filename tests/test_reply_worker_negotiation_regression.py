from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from hh_applicant_tool.automation.reply_worker import (
    APPLICANT_ROLE,
    EMPLOYER_ROLE,
    ReplyDecision,
    ReplyWorker,
    ReplyWorkerConfig,
    build_context,
    sorted_messages,
)


def _message(message_id: str, role: str, text: str, created_at: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "created_at": created_at,
        "author": {"participant_type": role.lower()},
        "text": text,
    }


def _worker(hh: Any) -> ReplyWorker:
    return ReplyWorker(
        ReplyWorkerConfig(dry_run=False),
        hh=hh,
        framework_writer=Mock(),
    )


def _decision() -> ReplyDecision:
    return ReplyDecision(
        chat_id="chat-1",
        expected_last_message_id="employer-old",
        context=["Работодатель: Нужен Go?"],
        initiated_by_us=True,
        vacancy_name="Backend developer",
        employer_name="Acme",
    )


def test_real_negotiation_schema_is_sorted_by_created_at() -> None:
    messages = [
        _message("applicant-new", APPLICANT_ROLE, "Уже ответил", "2026-09-14T08:20:00+0300"),
        _message("employer-old", EMPLOYER_ROLE, "Нужен Go?", "2026-09-11T08:00:00+0300"),
    ]

    ordered = sorted_messages(messages)

    assert [item["id"] for item in ordered] == ["employer-old", "applicant-new"]
    context, initiated_by_us = build_context(messages)
    assert initiated_by_us is False
    assert context[-1] == "Я: Уже ответил"


def test_collect_candidates_does_not_reply_again_when_our_message_is_newest() -> None:
    hh = Mock()

    def route(endpoint: str, **_kwargs: Any) -> dict[str, Any]:
        if endpoint.startswith("/negotiations/chat-1/messages"):
            # Deliberately newest-first: this reproduces the production failure
            # that was hidden while reply_worker sorted by creation_time.
            return {
                "items": [
                    _message(
                        "applicant-new",
                        APPLICANT_ROLE,
                        "У меня нет коммерческого опыта разработки на Go",
                        "2026-09-14T08:18:00+0300",
                    ),
                    _message(
                        "employer-old",
                        EMPLOYER_ROLE,
                        "Есть коммерческий опыт с Go?",
                        "2026-09-11T08:00:00+0300",
                    ),
                ],
                "pages": 1,
            }
        return {
            "items": [{"id": "chat-1", "messaging_status": "ok"}],
            "pages": 1,
        }

    hh.call_api.side_effect = route

    assert _worker(hh).collect_candidate_chats() == []


def test_revalidation_rejects_old_employer_turn_when_our_reply_is_newer() -> None:
    hh = Mock()
    hh.call_api.return_value = {
        "items": [
            _message(
                "applicant-new",
                APPLICANT_ROLE,
                "Уже ответил вручную",
                "2026-09-14T08:20:00+0300",
            ),
            _message(
                "employer-old",
                EMPLOYER_ROLE,
                "Вопрос",
                "2026-09-11T08:00:00+0300",
            ),
        ],
        "pages": 1,
    }

    assert _worker(hh).is_still_current(_decision()) is False


def test_missing_message_timestamp_fails_closed() -> None:
    malformed = {
        "id": "employer-without-time",
        "author": {"participant_type": "employer"},
        "text": "Вопрос",
    }

    assert sorted_messages([malformed]) == []
