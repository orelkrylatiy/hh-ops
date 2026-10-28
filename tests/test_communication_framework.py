from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from humanizer_framework import CommunicationFramework
from humanizer_framework.providers.mock import MockProvider

from hh_applicant_tool.automation.reply_worker import (
    ACTION_REPLY,
    ReplyWorker,
    ReplyWorkerConfig,
)
from hh_applicant_tool.communication import (
    CHAT_REPLY_BUSINESS_RULES,
    FrameworkWriter,
    build_application_request,
    build_chat_reply_request,
    conversation_from_context,
)
from hh_applicant_tool.operations._apply_vacancies_apply_flow import (
    APPLICATION_CONTACT_LINE,
    APPLICATION_CONTACT_URL,
    append_application_contact,
    letter_quality_issues,
)
from hh_applicant_tool.utils.string import contains_smiley


def _decision(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "chat_id": "chat-1",
        "action": ACTION_REPLY,
        "context": [
            "Работодатель: С React 19 работали?",
            "Я: Да, основной стек React и TypeScript.",
            "Работодатель: Когда удобно созвониться?",
        ],
        "initiated_by_us": True,
        "vacancy_name": "Frontend developer",
        "employer_name": "Acme",
        "latest_message_text": "Когда удобно созвониться?",
        "resume_context": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_chat_reply_request_maps_hh_job_search_domain() -> None:
    request = build_chat_reply_request(
        context=_decision().context,
        initiated_by_us=True,
        vacancy_name="Frontend developer",
        employer_name="Acme",
        latest_message_text="Когда удобно созвониться?",
    )
    assert request.domain == "job_search"
    assert request.channel == "hh"
    assert str(request.message_type) == "chat_reply"
    assert request.profile == "frontend-react"
    assert request.language == "ru"
    assert request.business_rules == CHAT_REPLY_BUSINESS_RULES
    assert request.voice is not None
    assert request.context["vacancy"] == {"name": "Frontend developer"}
    assert request.context["initiated_by_candidate"] is True


def test_conversation_from_context_maps_roles() -> None:
    messages = conversation_from_context(
        [
            "Работодатель: С React 19 работали?",
            "Я: Да, основной стек React.",
            "Служебная строка без роли",
        ]
    )
    assert [(m.role, m.content) for m in messages] == [
        ("user", "С React 19 работали?"),
        ("assistant", "Да, основной стек React."),
    ]


def test_framework_prepare_builds_policy_and_bounded_history() -> None:
    request = build_chat_reply_request(
        context=[f"Работодатель: вопрос {n}" for n in range(12)],
        initiated_by_us=False,
        vacancy_name="Frontend developer",
        employer_name="Acme",
        latest_message_text="вопрос 11",
    )
    package = CommunicationFramework().prepare(request)
    assert package.messages[0]["role"] == "system"
    assert len(package.messages) <= 2 + 8 + 1
    assert package.plan.max_chars > 0


def test_live_writer_generates_application_and_chat_reply() -> None:
    letter = (
        "Здравствуйте! Более пяти лет разрабатываю интерфейсы на React и TypeScript. "
        "В вакансии важна работа с продуктовым интерфейсом, и этот опыт подходит "
        "для ваших задач. Буду рад рассказать подробнее о проектах."
    )

    provider = MockProvider([letter, letter, "Да, работал с React и TypeScript."])
    writer = FrameworkWriter(CommunicationFramework(provider=provider))
    application = writer.application(
        vacancy_name="Frontend developer",
        employer_name="Acme",
        source_context="Вакансия: Frontend developer. Резюме: пять лет React и TypeScript.",
    )
    reply = writer.chat_reply(
        context=["Работодатель: Работали с React?"],
        initiated_by_us=True,
        vacancy_name="Frontend developer",
        employer_name="Acme",
        latest_message_text="Работали с React?",
        resume_context="Пять лет React и TypeScript",
    )

    assert application.text == letter
    assert reply.text == "Да, работал с React и TypeScript."
    assert provider.calls


def test_contains_smiley_detects_text_smileys_but_not_parens() -> None:
    assert contains_smiley("Хорошо)")
    assert contains_smiley("Понял :)")
    assert contains_smiley("Отлично! 😀")
    assert not contains_smiley("Есть опыт (финтех, терминалы).")
    assert not contains_smiley("1) опыт 2) стек 3) сроки")
    assert not contains_smiley("")


def test_reply_quality_issues_rejects_smileys() -> None:
    from hh_applicant_tool.automation.reply_worker import reply_quality_issues

    assert "contains a smiley or emoji" in reply_quality_issues("Хорошо)")
    assert "contains a smiley or emoji" in reply_quality_issues("Здравствуйте! 🙂")
    assert (
        reply_quality_issues("Здравствуйте! Спасибо за сообщение, отвечу на вопросы по стеку.")
        == []
    )


def test_letter_quality_issues_rejects_smileys() -> None:
    base = (
        "Здравствуйте! Пишу по вакансии: больше пяти лет делаю интерфейсы на React "
        "и TypeScript, сейчас торговый терминал в финтехе. Стек совпадает, задачи "
        "знакомы, готов рассказать подробнее"
    )
    assert "contains a smiley or emoji" in letter_quality_issues(base + ")")
    assert "contains a smiley or emoji" not in letter_quality_issues(
        base + " (подробности в резюме)."
    )


def test_application_contact_is_appended_once_and_passes_quality_checks() -> None:
    letter = (
        "Здравствуйте! Заинтересовали задачи по развитию продуктового интерфейса. "
        "Более пяти лет работаю с React и TypeScript и готов обсудить опыт подробнее."
    )

    with_contact = append_application_contact(letter)

    assert with_contact.endswith(APPLICATION_CONTACT_LINE)
    assert append_application_contact(with_contact) == with_contact
    assert letter_quality_issues(with_contact) == []


def test_application_request_uses_selected_profile_voice() -> None:
    request = build_application_request(
        vacancy_name="LLM Engineer",
        employer_name="Acme",
        source_context="Python и OpenAI-compatible API",
        profile="ai-engineer",
    )

    assert request.profile == "ai-engineer"
    assert request.voice is not None
    assert request.voice.id == "maxim-ai-engineer"


def test_live_worker_sends_framework_reply_without_legacy_ai() -> None:
    writer = Mock()
    writer.chat_reply.return_value = SimpleNamespace(
        text="Да, работаю с React и TypeScript.",
        plan=SimpleNamespace(action=SimpleNamespace(value="answer")),
        provider="mock",
        issues=[],
    )
    worker = ReplyWorker(
        ReplyWorkerConfig(dry_run=False),
        hh=Mock(),
        framework_writer=writer,
    )
    assert worker.generate_reply(_decision()) == "Да, работаю с React и TypeScript."
    assert writer.chat_reply.call_args.kwargs["resume_context"] == ""


def test_live_worker_skips_failed_framework_reply() -> None:
    writer = Mock()
    writer.chat_reply.side_effect = RuntimeError("provider unavailable")
    worker = ReplyWorker(
        ReplyWorkerConfig(dry_run=False),
        hh=Mock(),
        framework_writer=writer,
    )
    assert worker.generate_reply(_decision()) is None
