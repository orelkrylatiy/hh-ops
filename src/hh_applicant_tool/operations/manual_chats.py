from __future__ import annotations

import argparse
import json
import sqlite3
from typing import TYPE_CHECKING

from ..automation.reply_state import ManualChatQueue
from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool


class Namespace(BaseNamespace):
    limit: int
    resolve: str | None
    message_id: str | None


class Operation(BaseOperation):
    """Показать или закрыть очередь чатов, требующих ручной обработки."""

    __aliases__ = ["manual-chat-queue"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument(
            "--resolve",
            metavar="CHAT_ID",
            help="Пометить pending-записи чата обработанными",
        )
        parser.add_argument(
            "--message-id",
            help="При --resolve закрыть только конкретное сообщение",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        ManualChatQueue(tool.db_path)

        if args.resolve:
            if args.message_id:
                cur = tool.db.execute(
                    """
                    UPDATE manual_chat_queue
                    SET status = 'resolved', updated_at = CURRENT_TIMESTAMP
                    WHERE chat_id = ? AND message_id = ? AND status = 'pending'
                    """,
                    (args.resolve, args.message_id),
                )
            else:
                cur = tool.db.execute(
                    """
                    UPDATE manual_chat_queue
                    SET status = 'resolved', updated_at = CURRENT_TIMESTAMP
                    WHERE chat_id = ? AND status = 'pending'
                    """,
                    (args.resolve,),
                )
            tool.db.commit()
            print(f"Resolved: {cur.rowcount}")
            return

        tool.db.row_factory = sqlite3.Row
        rows = tool.db.execute(
            """
            SELECT chat_id, message_id, message_text, vacancy_name,
                   employer_name, reason, created_at, updated_at
            FROM manual_chat_queue
            WHERE status = 'pending'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(args.limit, 1),),
        ).fetchall()
        payload = [dict(row) for row in rows]
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        if not payload:
            print("Manual chat queue is empty.")
            return
        for item in payload:
            print(
                f"[{item['chat_id']}] {item.get('vacancy_name') or 'vacancy'} "
                f"({item.get('employer_name') or 'employer'})"
            )
            print(f"  reason: {item['reason']}")
            print(f"  message: {item['message_text']}")
