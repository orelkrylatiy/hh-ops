"""Regression tests for HTTP client runtime safety."""

from unittest.mock import Mock

import pytest
from requests import Session

from hh_applicant_tool.api.client import DEFAULT_TIMEOUT, ApiClient, BaseClient
from hh_applicant_tool.api.errors import Forbidden


def _successful_response() -> Mock:
    response = Mock()
    response.status_code = 200
    response.text = "{}"
    response.json.return_value = {}
    return response


def test_request_uses_finite_default_timeout() -> None:
    session = Mock(spec=Session)
    session.proxies = {}
    session.request.return_value = _successful_response()
    client = BaseClient(base_url="https://api.example.com/", session=session)

    client.get("/me")

    assert session.request.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT


def test_request_respects_custom_timeout() -> None:
    session = Mock(spec=Session)
    session.proxies = {}
    session.request.return_value = _successful_response()
    client = BaseClient(
        base_url="https://api.example.com/",
        session=session,
        timeout=(3.0, 12.0),
    )

    client.get("/me")

    assert session.request.call_args.kwargs["timeout"] == (3.0, 12.0)


def test_invalid_http_method_is_rejected_at_runtime() -> None:
    client = BaseClient(base_url="https://api.example.com/")

    with pytest.raises(ValueError, match="Unsupported HTTP method"):
        client.request("PATCH", "/me")  # type: ignore[arg-type]


@pytest.mark.parametrize("expires_at", [0, 9999999999])
def test_forbidden_chat_does_not_refresh_or_replay(expires_at: int) -> None:
    response = _successful_response()
    response.status_code = 403
    response.json.return_value = {"errors": [{"type": "negotiations", "value": "no_invitation"}]}
    session = Mock(spec=Session)
    session.request.return_value = response
    client = ApiClient(
        session=session,
        access_token="USERold",
        refresh_token="refresh",
        access_expires_at=expires_at,
    )
    refresh = Mock()
    client.oauth_client.refresh_access_token = refresh

    with pytest.raises(Forbidden) as caught:
        client.post("/negotiations/123/messages", message="hello")

    assert not caught.value.is_authentication_error
    refresh.assert_not_called()
    assert session.request.call_count == 1


def test_oauth_forbidden_refreshes_and_retries_once() -> None:
    forbidden = _successful_response()
    forbidden.status_code = 403
    forbidden.json.return_value = {"errors": [{"type": "oauth", "value": "bad_authorization"}]}
    session = Mock(spec=Session)
    session.request.side_effect = [forbidden, _successful_response()]
    client = ApiClient(session=session, access_token="USERold", refresh_token="old-refresh")
    refresh = Mock(return_value={"access_token": "USERnew", "refresh_token": "new-refresh"})
    client.oauth_client.refresh_access_token = refresh

    assert client.get("/me") == {}

    refresh.assert_called_once_with("old-refresh")
    assert session.request.call_count == 2
    assert session.request.call_args.kwargs["headers"]["authorization"] == "Bearer USERnew"


def test_refresh_failure_preserves_original_oauth_error() -> None:
    forbidden = _successful_response()
    forbidden.status_code = 403
    forbidden.json.return_value = {"errors": [{"type": "oauth", "value": "bad_authorization"}]}
    session = Mock(spec=Session)
    session.request.return_value = forbidden
    client = ApiClient(session=session, access_token="USERold", refresh_token="used-refresh")
    client.oauth_client.refresh_access_token = Mock(side_effect=ValueError("already refreshed"))

    with pytest.raises(Forbidden) as caught:
        client.get("/me")

    assert caught.value.is_authentication_error
    assert session.request.call_count == 1
