from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import Mock

import pytest

from hh_applicant_tool.ai.openai import OpenAIError
from hh_applicant_tool.automation.reply_worker import (
    ACTION_IGNORE,
    ACTION_MANUAL,
    ACTION_REPLY,
    APPLICANT_ROLE,
    EMPLOYER_ROLE,
    HHCLI,
    MAX_REPLY_CHARS,
    HHCLIError,
    ReplyDecision,
    ReplyWorker,
    ReplyWorkerConfig,
    build_ai_client,
    build_context,
    classify_chat,
    load_json_config,
    reply_quality_issues,
    sanitize_reply_text,
    select_ai_config,
)


def _message(message_id: str, role: str, text: str, timestamp: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "created_at": timestamp,
        "author": {"participant_type": role.lower()},
        "text": text,
    }


def _decision(**overrides: Any) -> ReplyDecision:
    values: dict[str, Any] = {
        "chat_id": "chat-1",
        "expected_last_message_id": "employer-1",
        "context": ["Работодатель: Когда удобно созвониться?"],
        "initiated_by_us": True,
        "vacancy_name": "Frontend developer",
        "employer_name": "Acme",
        "action": ACTION_REPLY,
        "reason": "employer_message",
        "latest_message_text": "Когда удобно созвониться?",
    }
    values.update(overrides)
    return ReplyDecision(**values)


def _live_worker(hh=None, ai=None, **config_overrides) -> ReplyWorker:
    return ReplyWorker(
        ReplyWorkerConfig(dry_run=False, **config_overrides),
        hh=hh or Mock(),
        ai=ai or Mock(),
        system_prompt="system",
    )


def test_reply_ai_config_falls_back_to_cover_letter() -> None:
    config = {
        "openai_cover_letter": {
            "api_key": "key",
            "base_url": "https://example.test/v1/chat/completions",
            "model": "model",
        }
    }

    section, provider = select_ai_config(config)

    assert section == "openai_cover_letter"
    assert provider["model"] == "model"


def test_reply_ai_config_prefers_dedicated_reply_section() -> None:
    config = {
        "openai_cover_letter": {"model": "cover"},
        "openai_reply": {"model": "reply"},
    }

    section, provider = select_ai_config(config)

    assert section == "openai_reply"
    assert provider["model"] == "reply"


def test_build_ai_client_forwards_provider_settings() -> None:
    client = build_ai_client(
        {
            "openai_reply": {
                "api_key": " reply-key ",
                "base_url": "https://example.test/v1/chat/completions",
                "model": "reply-model",
                "temperature": 0.2,
                "max_completion_tokens": 321,
                "rate_limit": 17,
                "timeout": 12,
                "max_retries": 4,
            }
        },
        "system prompt",
    )

    assert client.api_key == "reply-key"
    assert client.base_url == "https://example.test/v1/chat/completions"
    assert client.model == "reply-model"
    assert client.system_prompt == "system prompt"
    assert client.temperature == 0.2
    assert client.max_completion_tokens == 321
    assert client.rate_limit == 17
    assert client.timeout == 12
    assert client.max_retries == 4


@pytest.mark.parametrize("missing", ["api_key", "base_url", "model"])
def test_build_ai_client_rejects_incomplete_provider(missing: str) -> None:
    provider = {
        "api_key": "key",
        "base_url": "https://example.test/v1/chat/completions",
        "model": "model",
    }
    provider[missing] = ""

    with pytest.raises(ValueError, match=missing):
        build_ai_client({"openai_reply": provider}, "system")


def test_load_json_config_requires_object(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text('["not", "an", "object"]', encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        load_json_config(path)


def test_humanizer_rejects_placeholders_long_dash_and_ai_cliches() -> None:
    issues = reply_quality_issues("Важно отметить — могу обсудить [название компании] завтра.")

    assert "contains a long dash" in issues
    assert "contains a placeholder" in issues
    assert "contains an AI-style cliche" in issues


def test_sanitize_reply_replaces_long_dashes_with_hyphen() -> None:
    assert sanitize_reply_text("Да — удобно. Также–проверка — ок.") == (
        "Да - удобно. Также-проверка - ок."
    )
    assert sanitize_reply_text("") == ""


def test_humanizer_accepts_short_natural_reply() -> None:
    assert reply_quality_issues("Да, завтра после 12 удобно. Могу созвониться.") == []


def test_humanizer_rejects_empty_and_oversized_reply() -> None:
    assert reply_quality_issues("   ") == ["empty reply"]
    assert "reply is too long" in reply_quality_issues("а" * (MAX_REPLY_CHARS + 1))


def test_build_context_sorts_real_negotiation_messages_and_ignores_unknown_roles() -> None:
    context, initiated_by_us = build_context(
        [
            _message("2", EMPLOYER_ROLE, "Второе", "2026-01-01T10:02:00+0300"),
            _message("1", APPLICANT_ROLE, "Первое", "2026-01-01T10:01:00+0300"),
            _message("3", "SYSTEM", "Системное", "2026-01-01T10:03:00+0300"),
        ]
    )

    assert initiated_by_us is True
    assert context == ["Я: Первое", "Работодатель: Второе"]


def test_hhcli_builds_profile_command() -> None:
    assert HHCLI("account-10")._base_command() == [
        "hh-applicant-tool",
        "--no-auto-auth",
        "--profile-id",
        "account-10",
    ]


def test_hhcli_call_api_formulates_negotiation_message_post(monkeypatch) -> None:
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='{"id":"sent"}',
        stderr="",
    )
    run = Mock(return_value=completed)
    monkeypatch.setattr("hh_applicant_tool.automation.reply_worker.subprocess.run", run)

    result = HHCLI("account-1").call_api(
        "/negotiations/chat-1/messages",
        method="POST",
        form_params={"message": "Привет"},
    )

    assert result == {"id": "sent"}
    command = run.call_args.args[0]
    assert command[:4] == [
        "hh-applicant-tool",
        "--no-auto-auth",
        "--profile-id",
        "account-1",
    ]
    assert "--method" in command
    assert "message=Привет" in command
    assert "--data" not in command


def test_hhcli_call_api_wraps_process_and_json_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "hh_applicant_tool.automation.reply_worker.subprocess.run",
        Mock(
            return_value=subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HH failed"
            )
        ),
    )
    with pytest.raises(HHCLIError, match="HH failed"):
        HHCLI().call_api("/me")

    monkeypatch.setattr(
        "hh_applicant_tool.automation.reply_worker.subprocess.run",
        Mock(
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="not-json", stderr=""
            )
        ),
    )
    with pytest.raises(HHCLIError, match="invalid HH JSON"):
        HHCLI().call_api("/me")


def test_collect_candidate_chats_only_keeps_unblocked_employer_turns() -> None:
    hh = Mock()

    def route(endpoint: str, **_kwargs: Any) -> dict[str, Any]:
        if "/messages" in endpoint:
            messages = {
                "reply-me": [_message("1", EMPLOYER_ROLE, "Привет", "2026-01-01T10:00:00+0300")],
                "already-replied": [
                    _message("2", APPLICANT_ROLE, "Ответ", "2026-01-01T10:01:00+0300")
                ],
            }
            negotiation_id = endpoint.split("/")[2]
            return {"items": messages.get(negotiation_id, []), "pages": 1}
        return {
            "items": [
                {"id": "reply-me", "messaging_status": "ok"},
                {"id": "already-replied", "messaging_status": "ok"},
                {"id": "discarded", "state": {"id": "discard"}, "messaging_status": "ok"},
                {"id": "muted", "messaging_status": "disabled_by_employer"},
            ],
            "pages": 1,
        }

    hh.call_api.side_effect = route
    worker = ReplyWorker(
        ReplyWorkerConfig(max_chats=100),
        hh=hh,
        ai=None,
        system_prompt="prompt",
    )

    chats = worker.collect_candidate_chats()

    assert [chat["id"] for chat in chats] == ["reply-me"]


def test_make_decision_builds_context_and_vacancy_metadata() -> None:
    hh = Mock()
    hh.call_api.return_value = {
        "items": [
            _message(
                "applicant-1",
                APPLICANT_ROLE,
                "Здравствуйте",
                "2026-01-01T10:00:00+0300",
            ),
            _message(
                "employer-1",
                EMPLOYER_ROLE,
                "Когда созвон?",
                "2026-01-01T10:01:00+0300",
            ),
        ],
        "pages": 1,
    }
    worker = ReplyWorker(ReplyWorkerConfig(), hh=hh, ai=None, system_prompt="prompt")

    decision = worker.make_decision(
        {
            "id": "chat-1",
            "vacancy": {"name": "React developer", "employer": {"name": "Acme"}},
        }
    )

    assert decision is not None
    assert decision.expected_last_message_id == "employer-1"
    assert decision.initiated_by_us is True
    assert decision.context[-1] == "Работодатель: Когда созвон?"
    assert decision.vacancy_name == "React developer"
    assert decision.employer_name == "Acme"


def test_make_decision_skips_when_applicant_is_latest() -> None:
    hh = Mock()
    hh.call_api.return_value = {
        "items": [
            _message(
                "applicant-2",
                APPLICANT_ROLE,
                "Уже ответил",
                "2026-01-01T10:02:00+0300",
            ),
            _message(
                "employer-1",
                EMPLOYER_ROLE,
                "Привет",
                "2026-01-01T10:01:00+0300",
            ),
        ],
        "pages": 1,
    }
    worker = ReplyWorker(ReplyWorkerConfig(), hh=hh, ai=None, system_prompt="prompt")

    assert worker.make_decision({"id": "chat-1"}) is None


def test_generate_reply_returns_safe_first_attempt() -> None:
    ai = Mock()
    ai.complete.return_value = "Да, завтра после 12 удобно."
    worker = _live_worker(ai=ai)

    assert worker.generate_reply(_decision()) == "Да, завтра после 12 удобно."
    ai.complete.assert_called_once()


def test_generate_reply_retries_humanizer_failure_with_correction() -> None:
    ai = Mock()
    ai.complete.side_effect = [
        "Важно отметить — буду рад обсудить.",
        "Да, завтра после 12 удобно.",
    ]
    worker = _live_worker(ai=ai, ai_retries=1)

    assert worker.generate_reply(_decision()) == "Да, завтра после 12 удобно."
    assert ai.complete.call_count == 2
    assert "Исправь предыдущую попытку" in ai.complete.call_args_list[1].args[0]


def test_generate_reply_fails_closed_after_second_unsafe_result() -> None:
    ai = Mock()
    ai.complete.side_effect = ["Важно отметить — отвечу.", "Таким образом — отвечу."]
    worker = _live_worker(ai=ai, ai_retries=1)

    assert worker.generate_reply(_decision()) is None


def test_generate_reply_fails_closed_on_ai_error() -> None:
    ai = Mock()
    ai.complete.side_effect = OpenAIError("provider down")
    worker = _live_worker(ai=ai)

    assert worker.generate_reply(_decision()) is None


def test_is_still_current_accepts_same_employer_turn() -> None:
    hh = Mock()
    hh.call_api.return_value = {
        "items": [
            _message(
                "employer-1",
                EMPLOYER_ROLE,
                "Вопрос",
                "2026-01-01T10:00:00+0300",
            )
        ],
        "pages": 1,
    }

    assert _live_worker(hh=hh).is_still_current(_decision()) is True


def test_is_still_current_fails_closed_when_chat_changed_even_if_api_is_newest_first() -> None:
    hh = Mock()
    hh.call_api.return_value = {
        "items": [
            _message(
                "applicant-2",
                APPLICANT_ROLE,
                "Уже ответил вручную",
                "2026-01-01T10:01:00+0300",
            ),
            _message(
                "employer-1",
                EMPLOYER_ROLE,
                "Вопрос",
                "2026-01-01T10:00:00+0300",
            ),
        ],
        "pages": 1,
    }

    assert _live_worker(hh=hh).is_still_current(_decision()) is False


def test_send_posts_negotiations_message_form() -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    class FakeHH:
        def call_api(
            self,
            endpoint: str,
            *,
            method: str = "GET",
            json_data: dict[str, Any] | None = None,
            form_params: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            calls.append((endpoint, method, json_data, form_params))
            return {"id": "sent-1"}

    worker = _live_worker(hh=FakeHH())  # type: ignore[arg-type]

    assert worker.send_reply(_decision(), "Готов созвониться завтра") is True
    endpoint, method, json_data, form_params = calls[0]
    assert endpoint == "/negotiations/chat-1/messages"
    assert method == "POST"
    assert form_params == {"message": "Готов созвониться завтра"}
    assert json_data is None


def test_failed_send_is_treated_as_success_if_message_is_already_visible() -> None:
    class FakeHH:
        def call_api(
            self,
            endpoint: str,
            *,
            method: str = "GET",
            json_data: dict[str, Any] | None = None,
            form_params: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if method == "POST":
                raise HHCLIError("network response lost")
            return {
                "items": [
                    _message(
                        "applicant-2",
                        APPLICANT_ROLE,
                        "Готов созвониться завтра",
                        "2026-01-01T10:01:00+0300",
                    )
                ],
                "pages": 1,
            }

    worker = _live_worker(hh=FakeHH(), send_retries=2)  # type: ignore[arg-type]

    assert worker.send_reply(_decision(), "Готов созвониться завтра") is True


def test_failed_send_returns_false_when_message_is_not_visible() -> None:
    class FakeHH:
        def call_api(
            self,
            endpoint: str,
            *,
            method: str = "GET",
            json_data: dict[str, Any] | None = None,
            form_params: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if method == "POST":
                raise HHCLIError("network down")
            return {
                "items": [
                    _message(
                        "employer-1",
                        EMPLOYER_ROLE,
                        "Вопрос",
                        "2026-01-01T10:00:00+0300",
                    )
                ],
                "pages": 1,
            }

    worker = _live_worker(hh=FakeHH(), send_retries=0)  # type: ignore[arg-type]

    assert worker.send_reply(_decision(), "Ответ") is False


def test_run_dry_run_plans_without_revalidation_or_send() -> None:
    worker = ReplyWorker(
        ReplyWorkerConfig(dry_run=True), hh=Mock(), ai=None, system_prompt="prompt"
    )
    worker.collect_candidate_chats = Mock(return_value=[{"id": "chat-1"}])
    worker.make_decision = Mock(return_value=_decision())
    worker.generate_reply = Mock(return_value="preview")
    worker.is_still_current = Mock()
    worker.send_reply = Mock()

    stats = worker.run()

    assert stats["planned"] == 1
    assert stats["sent"] == 0
    worker.is_still_current.assert_not_called()
    worker.send_reply.assert_not_called()


def test_run_live_skips_stale_reply() -> None:
    worker = _live_worker()
    worker.collect_candidate_chats = Mock(return_value=[{"id": "chat-1"}])
    worker.make_decision = Mock(return_value=_decision())
    worker.generate_reply = Mock(return_value="Ответ")
    worker.is_still_current = Mock(return_value=False)
    worker.send_reply = Mock()

    stats = worker.run()

    assert stats["stale"] == 1
    worker.send_reply.assert_not_called()


def test_run_live_counts_success_and_failures() -> None:
    worker = _live_worker()
    worker.collect_candidate_chats = Mock(return_value=[{"id": "one"}, {"id": "two"}])
    worker.make_decision = Mock(side_effect=[_decision(), _decision()])
    worker.generate_reply = Mock(side_effect=["Ответ 1", "Ответ 2"])
    worker.is_still_current = Mock(return_value=True)
    worker.send_reply = Mock(side_effect=[True, False])

    stats = worker.run()

    assert stats["candidates"] == 2
    assert stats["sent"] == 1
    assert stats["errors"] == 1


def test_run_counts_chat_api_error_without_crashing_other_loop() -> None:
    worker = _live_worker()
    worker.collect_candidate_chats = Mock(return_value=[{"id": "chat-1"}])
    worker.make_decision = Mock(side_effect=HHCLIError("bad chat"))

    stats = worker.run()

    assert stats["errors"] == 1


def test_missing_ai_config_fails_closed() -> None:
    with pytest.raises(ValueError):
        select_ai_config({})


def test_classifier_ignores_plain_acknowledgement() -> None:
    messages = [
        _message(
            "employer-1",
            EMPLOYER_ROLE,
            "Спасибо за отклик! Мы рассмотрим резюме и свяжемся с вами.",
            "2026-01-01T10:00:00+0300",
        )
    ]

    action, reason = classify_chat(messages)

    assert action == ACTION_IGNORE
    assert reason == "acknowledgement_without_question"


def test_classifier_marks_explicit_button_flow_manual() -> None:
    messages = [
        _message(
            "employer-1",
            EMPLOYER_ROLE,
            "Пожалуйста, нажмите на кнопку ниже и выберите вариант.",
            "2026-01-01T10:00:00+0300",
        )
    ]

    action, reason = classify_chat(messages)

    assert action == ACTION_MANUAL
    assert reason == "ui_action_hint"


def test_classifier_marks_repeated_question_after_our_reply_manual() -> None:
    question = "Есть коммерческий опыт с React?"
    messages = [
        _message("employer-1", EMPLOYER_ROLE, question, "2026-01-01T10:00:00+0300"),
        _message(
            "applicant-1",
            APPLICANT_ROLE,
            "Да, более пяти лет.",
            "2026-01-01T10:01:00+0300",
        ),
        _message("employer-2", EMPLOYER_ROLE, question, "2026-01-01T10:02:00+0300"),
    ]

    action, reason = classify_chat(messages)

    assert action == ACTION_MANUAL
    assert reason == "repeated_after_applicant_reply"


def test_classifier_keeps_normal_question_as_text_reply() -> None:
    messages = [
        _message(
            "employer-1",
            EMPLOYER_ROLE,
            "Когда вам удобно созвониться?",
            "2026-01-01T10:00:00+0300",
        )
    ]

    assert classify_chat(messages) == (ACTION_REPLY, "employer_message")


def test_run_manual_decision_queues_without_generating_or_sending() -> None:
    manual_queue = Mock()
    worker = ReplyWorker(
        ReplyWorkerConfig(dry_run=False),
        hh=Mock(),
        ai=Mock(),
        system_prompt="prompt",
        manual_queue=manual_queue,
    )
    worker.collect_candidate_chats = Mock(return_value=[{"id": "chat-1"}])
    worker.make_decision = Mock(
        return_value=_decision(
            action=ACTION_MANUAL,
            reason="repeated_after_applicant_reply",
        )
    )
    worker.generate_reply = Mock()
    worker.send_reply = Mock()

    stats = worker.run()

    assert stats["manual"] == 1
    assert stats["sent"] == 0
    manual_queue.enqueue.assert_called_once()
    worker.generate_reply.assert_not_called()
    worker.send_reply.assert_not_called()


def test_run_ignore_decision_does_not_call_ai() -> None:
    worker = _live_worker()
    worker.collect_candidate_chats = Mock(return_value=[{"id": "chat-1"}])
    worker.make_decision = Mock(
        return_value=_decision(
            action=ACTION_IGNORE,
            reason="acknowledgement_without_question",
        )
    )
    worker.generate_reply = Mock()

    stats = worker.run()

    assert stats["ignored"] == 1
    worker.generate_reply.assert_not_called()
