"""Regression coverage for selected upstream 2.5.8 compatibility changes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hh_applicant_tool.main import HHApplicantTool


@pytest.mark.parametrize("status", [401, 403])
def test_hh_web_auth_failure_is_actionable(status: int) -> None:
    tool = HHApplicantTool.__new__(HHApplicantTool)
    response = SimpleNamespace(
        status_code=status,
        url="https://hh.ru/applicant/vacancy_response",
        text="<html>no initial state</html>",
    )

    with pytest.raises(RuntimeError, match="web authorization failed") as exc:
        tool.parse_redirect_config(response)
    assert f"(HTTP {status})" in str(exc.value)


def test_unexpected_hh_web_failure_keeps_status_and_url() -> None:
    tool = HHApplicantTool.__new__(HHApplicantTool)
    response = SimpleNamespace(status_code=500, url="https://hh.ru/test", text="")

    with pytest.raises(RuntimeError, match="Unexpected HH response: 500"):
        tool.parse_redirect_config(response)


def test_cli_config_dir_and_profile_override_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "wrong"))
    monkeypatch.setenv("HH_PROFILE_ID", "wrong-profile")

    tool = HHApplicantTool()
    args = tool._parser.parse_args(
        ["--config-dir", str(tmp_path), "--profile-id", "correct", "config", "--show-path"]
    )
    tool._assign_args(args)

    assert tool.config_path == (tmp_path / "correct").resolve()


def test_webview_user_agent_cli_has_priority(monkeypatch) -> None:
    monkeypatch.setenv("HH_WEBVIEW_USER_AGENT", "env-webview")
    tool = HHApplicantTool.__new__(HHApplicantTool)
    tool.config = {"webview_user_agent": "config-webview"}
    tool.webview_user_agent = "  cli-webview  "

    assert tool.resolved_webview_user_agent == "cli-webview"


def test_webview_user_agent_env_then_profile_config(monkeypatch) -> None:
    tool = HHApplicantTool.__new__(HHApplicantTool)
    tool.config = {"webview_user_agent": "config-webview"}
    tool.webview_user_agent = None
    monkeypatch.setenv("HH_WEBVIEW_USER_AGENT", " env-webview ")

    assert tool.resolved_webview_user_agent == "env-webview"

    monkeypatch.delenv("HH_WEBVIEW_USER_AGENT")
    assert tool.resolved_webview_user_agent == "config-webview"


def test_webview_user_agent_unset_uses_device_defaults(monkeypatch) -> None:
    tool = HHApplicantTool.__new__(HHApplicantTool)
    tool.config = {}
    tool.webview_user_agent = None
    monkeypatch.delenv("HH_WEBVIEW_USER_AGENT", raising=False)

    assert tool.resolved_webview_user_agent is None


def test_http_session_user_agent_cli_overrides_env_and_profile(monkeypatch) -> None:
    monkeypatch.setenv("HH_DESKTOP_USER_AGENT", "env-desktop")
    tool = HHApplicantTool.__new__(HHApplicantTool)
    tool.config = {"user_agent": "config-desktop"}
    tool.user_agent = "cli-desktop"

    session = tool._create_http_session({}, log_label="test")

    assert session.headers["User-Agent"] == "cli-desktop"
    session.close()


def test_http_session_user_agent_env_then_profile(monkeypatch) -> None:
    tool = HHApplicantTool.__new__(HHApplicantTool)
    tool.config = {"user_agent": "config-desktop"}
    tool.user_agent = None
    monkeypatch.setenv("HH_DESKTOP_USER_AGENT", "env-desktop")

    session = tool._create_http_session({}, log_label="test")
    assert session.headers["User-Agent"] == "env-desktop"
    session.close()

    monkeypatch.delenv("HH_DESKTOP_USER_AGENT")
    session = tool._create_http_session({}, log_label="test")
    assert session.headers["User-Agent"] == "config-desktop"
    session.close()
