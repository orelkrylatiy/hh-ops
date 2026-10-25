from __future__ import annotations

import base64
import json
import time
from email import message_from_bytes
from pathlib import Path
from unittest.mock import Mock

import pytest

from hh_applicant_tool.communication.gmail_api import GmailAPISender


def _mail_dir(path: Path) -> Path:
    (path / "config.json").write_text(json.dumps({"email": "sender@example.com"}))
    (path / "credentials.json").write_text(json.dumps({"installed": {}}))
    (path / "token.json").write_text(
        json.dumps({"access_token": "test-token", "expires_at": time.time() + 3600})
    )
    return path


def test_gmail_api_sender_checks_identity_and_sends_mime(monkeypatch, tmp_path: Path) -> None:
    sender = GmailAPISender(_mail_dir(tmp_path))
    get = Mock(return_value=Mock(json=lambda: {"emailAddress": "sender@example.com"}))
    post = Mock(return_value=Mock(json=lambda: {"id": "gmail-message-1"}))
    monkeypatch.setattr("hh_applicant_tool.communication.gmail_api.requests.get", get)
    monkeypatch.setattr("hh_applicant_tool.communication.gmail_api.requests.post", post)

    assert sender.profile_email() == "sender@example.com"
    assert sender.send("employer@example.com", "Отклик", "Текст письма") == "gmail-message-1"

    raw = post.call_args.kwargs["json"]["raw"]
    message = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert message["From"] == "sender@example.com"
    assert message["To"] == "employer@example.com"
    assert "Текст письма" in message.get_payload(decode=True).decode("utf-8")


def test_gmail_api_sender_rejects_different_account(monkeypatch, tmp_path: Path) -> None:
    sender = GmailAPISender(_mail_dir(tmp_path))
    get = Mock(return_value=Mock(json=lambda: {"emailAddress": "other@example.com"}))
    monkeypatch.setattr("hh_applicant_tool.communication.gmail_api.requests.get", get)

    with pytest.raises(ValueError, match="does not match"):
        sender.profile_email()
