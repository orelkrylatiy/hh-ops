from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from hh_applicant_tool.automation.reply_state import ManualChatQueue
from hh_applicant_tool.operations.manual_chats import Operation


def _tool(db_path):
    return SimpleNamespace(db_path=db_path, db=sqlite3.connect(db_path))


def test_manual_chats_lists_pending_as_json(tmp_path, capsys) -> None:
    db_path = tmp_path / "data"
    queue = ManualChatQueue(db_path)
    queue.enqueue(
        chat_id="chat-1",
        message_id="msg-1",
        message_text="Нажмите кнопку ниже",
        vacancy_name="Frontend developer",
        employer_name="Acme",
        reason="ui_action_hint",
    )
    tool = _tool(db_path)
    try:
        Operation().run(
            tool,
            SimpleNamespace(
                resolve=None,
                message_id=None,
                limit=10,
                json=True,
            ),
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["chat_id"] == "chat-1"
        assert payload[0]["reason"] == "ui_action_hint"
    finally:
        tool.db.close()


def test_manual_chats_resolves_one_message(tmp_path, capsys) -> None:
    db_path = tmp_path / "data"
    queue = ManualChatQueue(db_path)
    queue.enqueue(
        chat_id="chat-1",
        message_id="msg-1",
        message_text="Question",
        vacancy_name="Frontend",
        employer_name="Acme",
        reason="repeated_after_applicant_reply",
    )
    tool = _tool(db_path)
    try:
        Operation().run(
            tool,
            SimpleNamespace(
                resolve="chat-1",
                message_id="msg-1",
                limit=10,
                json=False,
            ),
        )
        assert "Resolved: 1" in capsys.readouterr().out
        assert queue.pending() == []
    finally:
        tool.db.close()
