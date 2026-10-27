"""Retry, validation and reply-fallback behavior for OpenAI-compatible failures."""

from unittest.mock import Mock

import pytest
import requests

from hh_applicant_tool.ai.openai import ChatOpenAI, OpenAIError, _normalize_captcha_text
from hh_applicant_tool.automation.reply_fallback import (
    DEFAULT_REPLY_FALLBACK_MESSAGE,
    FallbackChatAI,
    ReplyFallbackConfig,
    load_reply_fallback_config,
)


@pytest.fixture(autouse=True)
def _legacy_transport(monkeypatch):
    """Эти тесты проверяют legacy OpenAI-транспорт (мок session.post).
    По умолчанию complete() ходит через цепочку humanizer-framework —
    фиксируем файл на HH_AI_LEGACY=1."""
    monkeypatch.setenv("HH_AI_LEGACY", "1")


def make_client(session, max_retries=2):
    return ChatOpenAI(
        api_key="test-key",
        base_url="https://example.test/v1/chat/completions",
        model="test-model",
        max_retries=max_retries,
        rate_limit=0,
        session=session,
    )


def response(status_code, payload=None, *, headers=None):
    result = Mock(spec=requests.Response)
    result.status_code = status_code
    result.headers = headers or {}
    result.json.return_value = {} if payload is None else payload
    if status_code >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    return result


def test_retries_transient_server_error(monkeypatch):
    session = Mock()
    session.post.side_effect = [
        response(503),
        response(200, {"choices": [{"message": {"content": "ok"}}]}),
    ]
    monkeypatch.setattr("hh_applicant_tool.ai.openai.time.sleep", Mock())

    assert make_client(session).complete("hello") == "ok"
    assert session.post.call_count == 2


def test_ssl_verification_is_forwarded_to_transport() -> None:
    session = Mock()
    session.post.return_value = response(200, {"choices": [{"message": {"content": "ok"}}]})
    client = ChatOpenAI(
        api_key="test-key",
        base_url="https://example.test/v1/chat/completions",
        model="test-model",
        rate_limit=0,
        session=session,
        verify_ssl=False,
    )

    assert client.complete("hello") == "ok"
    assert session.post.call_args.kwargs["verify"] is False


def test_legacy_transport_bypasses_framework_chain(monkeypatch) -> None:
    monkeypatch.delenv("HH_AI_LEGACY", raising=False)
    session = Mock()
    session.post.return_value = response(200, {"choices": [{"message": {"content": "ok"}}]})
    client = ChatOpenAI(
        api_key="test-key",
        base_url="https://example.test/v1/chat/completions",
        model="test-model",
        rate_limit=0,
        session=session,
        legacy_transport=True,
    )

    assert client.complete("hello") == "ok"
    assert session.post.call_count == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Ab 1-cD"', "Ab1cD"),
        ("The answer is xY7kQ2", "xY7kQ2"),
        ("```QwEr12```", "QwEr12"),
        ("автов ожидание", "автов ожидание"),
        ("Текст на изображении: «автов ожидание»", "автов ожидание"),
        ("```text\nРусская фраза\n```", "Русская фраза"),
        ("abc", ""),
    ],
)
def test_normalize_captcha_text(raw: str, expected: str) -> None:
    assert _normalize_captcha_text(raw) == expected


def test_retries_network_error(monkeypatch):
    session = Mock()
    session.post.side_effect = [
        requests.ConnectionError("temporary"),
        response(200, {"choices": [{"message": {"content": "ok"}}]}),
    ]
    monkeypatch.setattr("hh_applicant_tool.ai.openai.time.sleep", Mock())

    assert make_client(session).complete("hello") == "ok"
    assert session.post.call_count == 2


def test_non_retryable_client_error_fails_immediately() -> None:
    session = Mock()
    session.post.return_value = response(400, {"error": {"message": "bad request"}})

    with pytest.raises(OpenAIError, match="Network error"):
        make_client(session).complete("hello")

    assert session.post.call_count == 1


def test_retry_after_header_is_respected(monkeypatch) -> None:
    session = Mock()
    session.post.side_effect = [
        response(429, headers={"Retry-After": "3"}),
        response(200, {"choices": [{"message": {"content": "ok"}}]}),
    ]
    sleep = Mock()
    monkeypatch.setattr("hh_applicant_tool.ai.openai.time.sleep", sleep)

    assert make_client(session).complete("hello") == "ok"
    sleep.assert_any_call(3.0)


def test_invalid_json_is_wrapped_in_openai_error() -> None:
    session = Mock()
    invalid = response(200)
    invalid.json.side_effect = ValueError("not json")
    session.post.return_value = invalid

    with pytest.raises(OpenAIError, match="Invalid JSON response"):
        make_client(session).complete("hello")


def test_non_object_json_is_rejected() -> None:
    session = Mock()
    session.post.return_value = response(200, ["unexpected"])

    with pytest.raises(OpenAIError, match="expected an object"):
        make_client(session).complete("hello")


def test_provider_error_string_is_wrapped_without_key_error() -> None:
    session = Mock()
    session.post.return_value = response(200, {"error": "provider unavailable"})

    with pytest.raises(OpenAIError, match="provider unavailable"):
        make_client(session).complete("hello")


def test_provider_error_dict_without_message_is_still_wrapped() -> None:
    session = Mock()
    session.post.return_value = response(200, {"error": {"code": "bad_model"}})

    with pytest.raises(OpenAIError, match="bad_model"):
        make_client(session).complete("hello")


def test_missing_choices_is_wrapped_in_openai_error() -> None:
    session = Mock()
    session.post.return_value = response(200, {"id": "completion-1"})

    with pytest.raises(OpenAIError, match="Invalid response format"):
        make_client(session).complete("hello")


def test_reply_fallback_defaults_to_enabled_safe_message() -> None:
    fallback = load_reply_fallback_config({})

    assert fallback.enabled is True
    assert fallback.message == DEFAULT_REPLY_FALLBACK_MESSAGE


def test_reply_fallback_can_be_customized_and_disabled() -> None:
    custom = load_reply_fallback_config(
        {
            "reply_fallback": {
                "enabled": True,
                "message": "Здравствуйте! Вакансия интересна. Готов обсудить детали.",
            }
        }
    )
    disabled = load_reply_fallback_config({"reply_fallback": {"enabled": False}})

    assert custom.message == "Здравствуйте! Вакансия интересна. Готов обсудить детали."
    assert disabled.enabled is False


def test_reply_fallback_rejects_unsafe_template() -> None:
    with pytest.raises(ValueError, match="quality checks"):
        load_reply_fallback_config(
            {
                "reply_fallback": {
                    "enabled": True,
                    "message": "Важно отметить — готов обсудить [вакансию].",
                }
            }
        )


def test_llm_retries_before_using_reply_fallback(monkeypatch) -> None:
    session = Mock()
    session.post.side_effect = [response(503), response(503)]
    monkeypatch.setattr("hh_applicant_tool.ai.openai.time.sleep", Mock())
    ai = FallbackChatAI(
        make_client(session, max_retries=1),
        ReplyFallbackConfig(),
    )

    assert ai.complete("hello") == DEFAULT_REPLY_FALLBACK_MESSAGE
    assert session.post.call_count == 2
    assert ai.fallback_uses == 1


def test_disabled_reply_fallback_preserves_fail_closed_behavior(monkeypatch) -> None:
    session = Mock()
    session.post.side_effect = [response(503), response(503)]
    monkeypatch.setattr("hh_applicant_tool.ai.openai.time.sleep", Mock())
    ai = FallbackChatAI(
        make_client(session, max_retries=1),
        ReplyFallbackConfig(enabled=False),
    )

    with pytest.raises(OpenAIError):
        ai.complete("hello")

    assert ai.fallback_uses == 0


def test_reply_fallback_does_not_mask_programming_errors() -> None:
    primary = Mock()
    primary.complete.side_effect = RuntimeError("bug")
    ai = FallbackChatAI(primary, ReplyFallbackConfig())

    with pytest.raises(RuntimeError, match="bug"):
        ai.complete("hello")

    assert ai.fallback_uses == 0
