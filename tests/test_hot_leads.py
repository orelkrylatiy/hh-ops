from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import requests

from hh_applicant_tool.automation.hot_leads import (
    HotLeadDetectionError,
    HotLeadDetector,
    TelegramNotificationError,
    TelegramNotifier,
    format_hot_lead_alert,
    prefilter_hot_lead,
)


@pytest.mark.parametrize(
    "text",
    [
        "Давайте созвонимся завтра в 15:00.",
        "Приглашаю вас на собеседование, когда вам удобно?",
        "Вот мой Telegram: @recruiter, напишите мне.",
        "Мой номер +7 900 123-45-67, позвоните после 16:00.",
        "Телефон для связи: +7 900 123-45-67.",
        "Ссылка на встречу: https://meet.google.com/abc-defg-hij",
    ],
)
def test_prefilter_keeps_concrete_interview_signals(text: str) -> None:
    result = prefilter_hot_lead(text)
    assert result.candidate is True
    assert result.signals


@pytest.mark.parametrize(
    "text",
    [
        "Спасибо за отклик, мы рассмотрим резюме.",
        "Расскажите подробнее про опыт с React.",
        "Какие зарплатные ожидания?",
        "Выполните тестовое задание.",
    ],
)
def test_prefilter_rejects_non_hot_conversation(text: str) -> None:
    assert prefilter_hot_lead(text).candidate is False


@pytest.mark.parametrize(
    "text",
    [
        "Робот-рекрутер приглашает вас ответить на несколько вопросов.",
        "Ваши ответы отправлены работодателю. Если заинтересует, он позвонит.",
        "Нажмите кнопку ниже и выберите вариант интервью.",
    ],
)
def test_prefilter_rejects_explicit_automation_even_with_hot_words(text: str) -> None:
    result = prefilter_hot_lead(text)
    assert result.candidate is False
    assert result.reason == "explicit_bot_or_automation"


def test_detector_confirms_high_confidence_human_hot_lead() -> None:
    ai = Mock()
    ai.complete.return_value = json.dumps(
        {
            "hot": True,
            "confidence": 0.94,
            "human_likelihood": "high",
            "reason": "Рекрутер предлагает созвон и спрашивает время.",
            "next_step": "Согласовать время звонка.",
        },
        ensure_ascii=False,
    )

    result = HotLeadDetector(ai, min_confidence=0.85).evaluate(
        context=["Я: Добрый день", "Работодатель: Давайте созвонимся завтра."],
        latest_message="Давайте созвонимся завтра.",
        vacancy_name="AI Engineer",
        employer_name="Acme",
    )

    assert result.hot is True
    assert result.confidence == 0.94
    assert result.human_likelihood == "high"


def test_detector_fails_closed_on_medium_human_likelihood() -> None:
    ai = Mock()
    ai.complete.return_value = json.dumps(
        {
            "hot": True,
            "confidence": 0.99,
            "human_likelihood": "medium",
            "reason": "Возможно автоматическое приглашение.",
            "next_step": "",
        },
        ensure_ascii=False,
    )

    result = HotLeadDetector(ai).evaluate(
        context=["Работодатель: Приглашаем на интервью."],
        latest_message="Приглашаем на интервью.",
        vacancy_name="Developer",
        employer_name="Acme",
    )

    assert result.hot is False


def test_detector_fails_closed_below_confidence_threshold() -> None:
    ai = Mock()
    ai.complete.return_value = json.dumps(
        {
            "hot": True,
            "confidence": 0.84,
            "human_likelihood": "high",
            "reason": "Есть приглашение, но контекст слабый.",
            "next_step": "",
        },
        ensure_ascii=False,
    )

    result = HotLeadDetector(ai, min_confidence=0.85).evaluate(
        context=["Работодатель: Созвонимся?"],
        latest_message="Созвонимся?",
        vacancy_name="Developer",
        employer_name="Acme",
    )

    assert result.hot is False


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        '{"hot":"yes","confidence":0.9,"human_likelihood":"high","reason":"x","next_step":""}',
        '{"hot":true,"confidence":2,"human_likelihood":"high","reason":"x","next_step":""}',
        '{"hot":true,"confidence":0.9,"human_likelihood":"maybe","reason":"x","next_step":""}',
        '{"hot":true,"confidence":0.9,"human_likelihood":"high","reason":"","next_step":""}',
    ],
)
def test_detector_rejects_malformed_classifier_output(payload: str) -> None:
    ai = Mock()
    ai.complete.return_value = payload

    with pytest.raises(HotLeadDetectionError):
        HotLeadDetector(ai).evaluate(
            context=["Работодатель: Давайте созвонимся."],
            latest_message="Давайте созвонимся.",
            vacancy_name="Developer",
            employer_name="Acme",
        )


def test_detector_accepts_json_code_fence() -> None:
    ai = Mock()
    fence = chr(96) * 3
    ai.complete.return_value = (
        fence
        + "json\n"
        + '{"hot":true,"confidence":0.95,"human_likelihood":"high",'
        + '"reason":"Живой рекрутер предлагает звонок.","next_step":"Ответить."}'
        + "\n"
        + fence
    )

    result = HotLeadDetector(ai).evaluate(
        context=["Работодатель: Давайте созвонимся."],
        latest_message="Давайте созвонимся.",
        vacancy_name="Developer",
        employer_name="Acme",
    )

    assert result.hot is True


def test_telegram_notifier_sends_plain_text_without_parse_mode() -> None:
    session = Mock()
    response = Mock()
    response.json.return_value = {"ok": True}
    session.post.return_value = response
    notifier = TelegramNotifier(
        bot_token="secret-token",
        chat_id="123",
        session=session,
    )

    notifier.send("hello")

    url = session.post.call_args.args[0]
    payload = session.post.call_args.kwargs["json"]
    assert url.endswith("/botsecret-token/sendMessage")
    assert payload == {
        "chat_id": "123",
        "text": "hello",
        "disable_web_page_preview": True,
    }
    assert "parse_mode" not in payload
    response.raise_for_status.assert_called_once()


def test_telegram_notifier_wraps_network_errors() -> None:
    session = Mock()
    session.post.side_effect = requests.ConnectionError("offline")
    notifier = TelegramNotifier(
        bot_token="secret-token",
        chat_id="123",
        session=session,
    )

    with pytest.raises(TelegramNotificationError, match="ConnectionError") as exc_info:
        notifier.send("hello")

    assert "secret-token" not in str(exc_info.value)


def test_telegram_notifier_rejects_api_error_payload() -> None:
    session = Mock()
    response = Mock()
    response.json.return_value = {"ok": False, "description": "chat not found"}
    session.post.return_value = response
    notifier = TelegramNotifier(
        bot_token="secret-token",
        chat_id="123",
        session=session,
    )

    with pytest.raises(TelegramNotificationError, match="chat not found"):
        notifier.send("hello")


def test_alert_contains_profile_context_and_truncates_message() -> None:
    alert = format_hot_lead_alert(
        "0555",
        {
            "vacancy_name": "AI Engineer",
            "employer_name": "Acme",
            "confidence": 0.95,
            "reason": "Предложен созвон.",
            "next_step": "Ответить рекрутеру.",
            "message_text": "x" * 900,
        },
    )

    assert "Аккаунт: 0555" in alert
    assert "AI Engineer" in alert
    assert "Acme" in alert
    assert "95%" in alert
    assert len(alert) < 1400


def test_telegram_http_error_never_exposes_bot_token() -> None:
    session = Mock()
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError(
        "500 Server Error for url: https://api.telegram.org/botsecret-token/sendMessage"
    )
    session.post.return_value = response
    notifier = TelegramNotifier(
        bot_token="secret-token",
        chat_id="123",
        session=session,
    )

    with pytest.raises(TelegramNotificationError) as exc_info:
        notifier.send("hello")

    assert "secret-token" not in str(exc_info.value)
    assert "HTTPError" in str(exc_info.value)


def test_telegram_network_error_never_contains_bot_token() -> None:
    session = Mock()
    token = "super-secret-token"
    session.post.side_effect = requests.ConnectionError(
        f"failed for https://api.telegram.org/bot{token}/sendMessage"
    )
    notifier = TelegramNotifier(
        bot_token=token,
        chat_id="123",
        session=session,
    )

    with pytest.raises(TelegramNotificationError) as exc_info:
        notifier.send("hello")

    assert token not in str(exc_info.value)


def test_hot_lead_system_prompt_marks_chat_as_untrusted_data() -> None:
    from hh_applicant_tool.automation.hot_leads import HOT_LEAD_SYSTEM_PROMPT

    lowered = HOT_LEAD_SYSTEM_PROMPT.lower()
    assert "недоверенными данными" in lowered
    assert "игнорируй предыдущие инструкции" in lowered
