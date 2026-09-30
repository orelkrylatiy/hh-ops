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
    FrameworkShadowReplier,
    build_chat_reply_request,
    conversation_from_context,
    framework_shadow_enabled,
)
from hh_applicant_tool.communication.shadow import SHADOW_ENV_FLAG
from hh_applicant_tool.operations._apply_vacancies_apply_flow import letter_quality_issues
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


def test_framework_shadow_enabled_reads_env() -> None:
    assert not framework_shadow_enabled({})
    assert framework_shadow_enabled({SHADOW_ENV_FLAG: "1"})
    assert framework_shadow_enabled({SHADOW_ENV_FLAG: "yes"})
    assert not framework_shadow_enabled({SHADOW_ENV_FLAG: "0"})


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


def test_shadow_flags_smiley_from_framework_output() -> None:
    # Второй выход нужен на repair-pass фреймворка; он тоже со смайлом,
    # поэтому исходный ответ с hard-issue остаётся итогом сравнения.
    replier = FrameworkShadowReplier(
        Mock(),
        provider=MockProvider(["Отлично, берусь:) Завтра удобно после 15:00.", "Хорошо:)"]),
    )
    comparison = replier.compare(_decision(), "Завтра удобно после 15:00.")
    assert comparison is not None
    assert "H:forbidden_smiley" in comparison.issue_codes


def test_shadow_compare_returns_comparison_without_sending() -> None:
    replier = FrameworkShadowReplier(
        Mock(),  # never used: provider is injected
        provider=MockProvider(["Завтра после 15:00 удобно, расскажу про опыт с React 19."]),
    )
    comparison = replier.compare(_decision(), "Завтра удобно после 15:00.")
    assert comparison is not None
    assert comparison.chat_id == "chat-1"
    assert comparison.primary_reply == "Завтра удобно после 15:00."
    assert comparison.framework_text.startswith("Завтра после 15:00")
    assert comparison.plan_action in {"answer", "schedule", "acknowledge"}
    assert replier.comparisons == 1


def test_shadow_compare_swallows_provider_failure() -> None:
    replier = FrameworkShadowReplier(
        Mock(),
        provider=MockProvider([]),
    )
    assert replier.compare(_decision(), "Здравый ответ.") is None
    assert replier.comparisons == 0


class _RecordingShadowReplier:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []

    def compare(self, decision: Any, primary_reply: str) -> None:
        self.calls.append((decision, primary_reply))


def _live_worker(shadow_replier: Any, ai: Any) -> ReplyWorker:
    return ReplyWorker(
        ReplyWorkerConfig(dry_run=False),
        hh=Mock(),
        ai=ai,
        system_prompt="system",
        shadow_replier=shadow_replier,
    )


def test_worker_runs_shadow_on_healthy_reply() -> None:
    shadow = _RecordingShadowReplier()
    ai = Mock()
    ai.complete.return_value = "Здравствуйте! Отвечу на вопрос: да, работаю с React 19."
    worker = _live_worker(shadow, ai)
    reply = worker.generate_reply(_decision())
    assert reply is not None
    assert len(shadow.calls) == 1
    assert shadow.calls[0][1] == reply


def test_worker_dry_run_never_calls_shadow() -> None:
    shadow = _RecordingShadowReplier()
    worker = ReplyWorker(
        ReplyWorkerConfig(dry_run=True),
        hh=Mock(),
        ai=Mock(),
        system_prompt="system",
        shadow_replier=shadow,
    )
    reply = worker.generate_reply(_decision())
    assert reply is not None
    assert shadow.calls == []


def test_worker_survives_shadow_failure() -> None:
    class _BrokenReplier:
        def compare(self, decision: Any, primary_reply: str) -> None:
            raise RuntimeError("boom")

    ai = Mock()
    ai.complete.return_value = "Здравствуйте! Отвечу на вопрос: да, работаю с React 19."
    worker = _live_worker(_BrokenReplier(), ai)
    assert worker.generate_reply(_decision()) is not None
