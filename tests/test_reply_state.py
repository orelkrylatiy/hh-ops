from __future__ import annotations

from hh_applicant_tool.automation.reply_state import ManualChatQueue


def test_manual_chat_queue_upserts_and_resolves(tmp_path) -> None:
    queue = ManualChatQueue(tmp_path / "data")

    queue.enqueue(
        chat_id="chat-1",
        message_id="msg-1",
        message_text="Нажмите кнопку ниже",
        vacancy_name="Frontend developer",
        employer_name="Acme",
        reason="ui_action_hint",
    )
    queue.enqueue(
        chat_id="chat-1",
        message_id="msg-1",
        message_text="Нажмите кнопку ниже",
        vacancy_name="Frontend developer",
        employer_name="Acme",
        reason="repeated_after_applicant_reply",
    )

    pending = queue.pending()
    assert len(pending) == 1
    assert pending[0]["chat_id"] == "chat-1"
    assert pending[0]["reason"] == "repeated_after_applicant_reply"

    assert queue.resolve_chat("chat-1") == 1
    assert queue.pending() == []


def test_manual_chat_queue_keeps_current_message_when_resolving_old(tmp_path) -> None:
    queue = ManualChatQueue(tmp_path / "data")
    for message_id in ("msg-1", "msg-2"):
        queue.enqueue(
            chat_id="chat-1",
            message_id=message_id,
            message_text=message_id,
            vacancy_name="Frontend",
            employer_name="Acme",
            reason="ui_action_hint",
        )

    assert queue.resolve_chat("chat-1", keep_message_id="msg-2") == 1
    pending = queue.pending()

    assert [item["message_id"] for item in pending] == ["msg-2"]
