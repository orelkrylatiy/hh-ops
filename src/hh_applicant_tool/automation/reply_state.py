from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

_MANUAL_CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_chat_queue (
    chat_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    message_text TEXT NOT NULL,
    vacancy_name TEXT,
    employer_name TEXT,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_manual_chat_queue_status
    ON manual_chat_queue(status, updated_at);
"""


class ManualChatQueue:
    """Small SQLite queue for chats that require a browser/human decision."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # closing() закрывает соединение, conn-менеджер коммитит транзакцию:
        # без коммита b35772a молча терял все записи очереди
        with closing(self._connect()) as conn, conn:
            conn.executescript(_MANUAL_CHAT_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)


    def enqueue(
        self,
        *,
        chat_id: str,
        message_id: str,
        message_text: str,
        vacancy_name: str,
        employer_name: str,
        reason: str,
    ) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO manual_chat_queue (
                    chat_id,
                    message_id,
                    message_text,
                    vacancy_name,
                    employer_name,
                    reason,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    message_text = excluded.message_text,
                    vacancy_name = excluded.vacancy_name,
                    employer_name = excluded.employer_name,
                    reason = excluded.reason,
                    status = 'pending',
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    chat_id,
                    message_id,
                    message_text,
                    vacancy_name,
                    employer_name,
                    reason,
                ),
            )

    def resolve_chat(
        self,
        chat_id: str,
        *,
        keep_message_id: str | None = None,
    ) -> int:
        """Resolve stale manual items once the chat moved past that message."""
        with closing(self._connect()) as conn, conn:
            if keep_message_id:
                cur = conn.execute(
                    """
                    UPDATE manual_chat_queue
                    SET status = 'resolved', updated_at = CURRENT_TIMESTAMP
                    WHERE chat_id = ?
                      AND status = 'pending'
                      AND message_id != ?
                    """,
                    (chat_id, keep_message_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE manual_chat_queue
                    SET status = 'resolved', updated_at = CURRENT_TIMESTAMP
                    WHERE chat_id = ? AND status = 'pending'
                    """,
                    (chat_id,),
                )
            return cur.rowcount

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT chat_id, message_id, message_text, vacancy_name,
                       employer_name, reason, status, created_at, updated_at
                FROM manual_chat_queue
                WHERE status = 'pending'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


class ReplySendLog:
    """Persistent guard against answering one employer message twice."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn, conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS reply_message_sends (
                    chat_id TEXT NOT NULL,
                    employer_message_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sent_at TEXT,
                    PRIMARY KEY (chat_id, employer_message_id)
                )"""
            )

    def is_recorded(self, chat_id: str, employer_message_id: str) -> bool:
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn:
            row = conn.execute(
                """SELECT 1 FROM reply_message_sends
                   WHERE chat_id=? AND employer_message_id=?""",
                (chat_id, employer_message_id),
            ).fetchone()
        return row is not None

    def claim(self, chat_id: str, employer_message_id: str) -> bool:
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn, conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO reply_message_sends
                   (chat_id, employer_message_id, status) VALUES (?, ?, 'pending')""",
                (chat_id, employer_message_id),
            )
            return cursor.rowcount == 1

    def mark_sent(self, chat_id: str, employer_message_id: str) -> None:
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn, conn:
            conn.execute(
                """UPDATE reply_message_sends SET status='sent', sent_at=CURRENT_TIMESTAMP
                   WHERE chat_id=? AND employer_message_id=?""",
                (chat_id, employer_message_id),
            )
