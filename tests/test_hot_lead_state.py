from __future__ import annotations

from hh_applicant_tool.automation.hot_lead_state import HotLeadStore


def test_hot_lead_store_records_and_deduplicates(tmp_path) -> None:
    store = HotLeadStore(tmp_path / "data")

    store.record(
        chat_id="chat-1",
        message_id="msg-1",
        is_hot=True,
        confidence=0.92,
        human_likelihood="high",
        reason="Созвон",
        next_step="Ответить",
        message_text="Давайте созвонимся.",
        vacancy_name="AI Engineer",
        employer_name="Acme",
    )
    store.record(
        chat_id="chat-1",
        message_id="msg-1",
        is_hot=True,
        confidence=0.95,
        human_likelihood="high",
        reason="Интервью",
        next_step="Согласовать время",
        message_text="Давайте созвонимся завтра.",
        vacancy_name="AI Engineer",
        employer_name="Acme",
    )

    row = store.get("chat-1", "msg-1")
    assert row is not None
    assert row["confidence"] == 0.95
    assert row["reason"] == "Интервью"
    assert len(store.recent_hot()) == 1


def test_pending_notification_retries_until_marked_notified(tmp_path) -> None:
    store = HotLeadStore(tmp_path / "data")
    store.record(
        chat_id="chat-1",
        message_id="msg-1",
        is_hot=True,
        confidence=0.95,
        human_likelihood="high",
        reason="Интервью",
        next_step="Согласовать время",
        message_text="Давайте созвонимся завтра.",
        vacancy_name="AI Engineer",
        employer_name="Acme",
    )

    pending = store.pending_notifications()
    assert [item["message_id"] for item in pending] == ["msg-1"]

    store.mark_notification_failed("chat-1", "msg-1", "telegram unavailable")
    row = store.get("chat-1", "msg-1")
    assert row is not None
    assert row["notified"] == 0
    assert row["notification_attempts"] == 1
    assert "telegram unavailable" in row["last_notification_error"]

    store.mark_notified("chat-1", "msg-1")
    row = store.get("chat-1", "msg-1")
    assert row is not None
    assert row["notified"] == 1
    assert row["notification_attempts"] == 2
    assert row["last_notification_error"] is None
    assert store.pending_notifications() == []


def test_non_hot_evaluation_is_not_pending_notification(tmp_path) -> None:
    store = HotLeadStore(tmp_path / "data")
    store.record(
        chat_id="chat-1",
        message_id="msg-1",
        is_hot=False,
        confidence=0.91,
        human_likelihood="medium",
        reason="Похоже на автоматизацию",
        next_step="",
        message_text="Приглашаем на интервью.",
        vacancy_name="Developer",
        employer_name="Acme",
    )

    assert store.pending_notifications() == []
    assert store.recent_hot() == []
