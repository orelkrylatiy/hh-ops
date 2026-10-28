"""Retry and validation behavior of the direct OpenAI transport.

The direct transport is used by vision captcha solving and config probes;
text generation itself goes through the humanizer-framework chain.
"""

from unittest.mock import Mock

import pytest
import requests

from hh_applicant_tool.ai.openai import ChatOpenAI, OpenAIError, _normalize_captcha_text

PROBE_PAYLOAD = {"model": "test-model", "messages": [{"role": "user", "content": "OK?"}]}


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

    data = make_client(session)._execute_with_retry(PROBE_PAYLOAD)
    assert data["choices"][0]["message"]["content"] == "ok"
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

    client._execute_with_retry(PROBE_PAYLOAD)
    assert session.post.call_args.kwargs["verify"] is False


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

    data = make_client(session)._execute_with_retry(PROBE_PAYLOAD)
    assert data["choices"][0]["message"]["content"] == "ok"
    assert session.post.call_count == 2


def test_non_retryable_client_error_fails_immediately() -> None:
    session = Mock()
    session.post.return_value = response(400, {"error": {"message": "bad request"}})

    with pytest.raises(OpenAIError, match="Network error"):
        make_client(session)._execute_with_retry(PROBE_PAYLOAD)

    assert session.post.call_count == 1


def test_retry_after_header_is_respected(monkeypatch) -> None:
    session = Mock()
    session.post.side_effect = [
        response(429, headers={"Retry-After": "3"}),
        response(200, {"choices": [{"message": {"content": "ok"}}]}),
    ]
    sleep = Mock()
    monkeypatch.setattr("hh_applicant_tool.ai.openai.time.sleep", sleep)

    make_client(session)._execute_with_retry(PROBE_PAYLOAD)
    sleep.assert_any_call(3.0)


def test_invalid_json_is_wrapped_in_openai_error() -> None:
    session = Mock()
    invalid = response(200)
    invalid.json.side_effect = ValueError("not json")
    session.post.return_value = invalid

    with pytest.raises(OpenAIError, match="Invalid JSON response"):
        make_client(session)._execute_with_retry(PROBE_PAYLOAD)


def test_non_object_json_is_rejected() -> None:
    session = Mock()
    session.post.return_value = response(200, ["unexpected"])

    with pytest.raises(OpenAIError, match="expected an object"):
        make_client(session)._execute_with_retry(PROBE_PAYLOAD)


def test_provider_error_string_is_wrapped_without_key_error() -> None:
    session = Mock()
    session.post.return_value = response(200, {"error": "provider unavailable"})

    with pytest.raises(OpenAIError, match="provider unavailable"):
        make_client(session)._execute_with_retry(PROBE_PAYLOAD)


def test_provider_error_dict_without_message_is_still_wrapped() -> None:
    session = Mock()
    session.post.return_value = response(200, {"error": {"code": "bad_model"}})

    with pytest.raises(OpenAIError, match="bad_model"):
        make_client(session)._execute_with_retry(PROBE_PAYLOAD)
