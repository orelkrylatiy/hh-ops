"""Regression tests for the upstream-inspired HH chat policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hh_applicant_tool.automation.upstream_chat_policy import (
    choose_quick_reply,
    is_bot_message,
    is_followup_due,
    is_incoming_reply_due,
    message_age_seconds,
    quick_reply_options,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def msg(*, minutes: float = 0, bot: bool = False) -> dict:
    return {
        "created_at": (NOW - timedelta(minutes=minutes)).isoformat(),
        "author": {"is_bot": bot},
    }


def test_bot_metadata_in_both_hh_formats() -> None:
    assert is_bot_message({"participantDisplay": {"isBot": True}})
    assert is_bot_message({"author": {"is_bot": True}})
    assert not is_bot_message({"author": {"participant_type": "employer"}})


def test_human_cooldown_and_bot_bypass() -> None:
    assert not is_incoming_reply_due(msg(minutes=14), human_cooldown_minutes=15, now=NOW)
    assert is_incoming_reply_due(msg(minutes=16), human_cooldown_minutes=15, now=NOW)
    assert is_incoming_reply_due(msg(minutes=0, bot=True), human_cooldown_minutes=15, now=NOW)


def test_stale_and_invalid_message_timestamps_fail_closed() -> None:
    assert not is_incoming_reply_due(msg(minutes=31 * 1440), max_chat_age_days=30, now=NOW)
    assert is_incoming_reply_due(msg(minutes=29 * 1440), max_chat_age_days=30, now=NOW)
    assert message_age_seconds({"created_at": "not-a-date"}, now=NOW) is None
    assert not is_incoming_reply_due(
        {"created_at": "not-a-date"}, human_cooldown_minutes=15, now=NOW
    )
    assert not is_incoming_reply_due(
        {"created_at": "2026-11-18T12:00:00"}, max_chat_age_days=30, now=NOW
    )


def test_no_cooldown_keeps_legacy_compatibility() -> None:
    assert is_incoming_reply_due({}, human_cooldown_minutes=0, max_chat_age_days=0)


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(60, False), (2 * 1440, True), (29 * 1440, True), (31 * 1440, False)],
)
def test_bounded_followup_due(minutes: float, expected: bool) -> None:
    assert (
        is_followup_due(msg(minutes=minutes), after_days=2, max_chat_age_days=30, now=NOW)
        is expected
    )


def test_quick_reply_options_only_include_explicit_send_options() -> None:
    message = {
        "payload": {
            "quick_replies": [
                {"type": "send", "text": " Удалённо "},
                {"type": "open_url", "text": "Оплатить"},
                {"type": "send", "text": "Гибрид"},
                {"type": "send", "text": "Гибрид"},
            ]
        }
    }
    assert quick_reply_options(message) == ("Удалённо", "Гибрид")
    assert quick_reply_options({"text": "Выберите один вариант"}) == ()


def test_quick_reply_rejects_low_confidence_and_ambiguity() -> None:
    options = ("Удалённо", "Гибрид", "Офис")
    assert choose_quick_reply("Удалённо.", options) == "Удалённо"
    assert choose_quick_reply("На Марсе", options) is None
    assert choose_quick_reply("Да", ("Да", "да")) is None
    assert choose_quick_reply("что угодно", ()) is None
