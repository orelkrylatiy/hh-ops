"""Local send ledger for optional vacancy emails."""

from __future__ import annotations

import sqlite3


class EmailSendLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.execute(
            """CREATE TABLE IF NOT EXISTS vacancy_email_sends (
                vacancy_id TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT,
                PRIMARY KEY (vacancy_id, recipient)
            )"""
        )
        conn.commit()

    def claim(self, vacancy_id: str, recipient: str, subject: str) -> bool:
        cursor = self.conn.execute(
            """INSERT OR IGNORE INTO vacancy_email_sends
               (vacancy_id, recipient, subject, status) VALUES (?, ?, ?, 'pending')""",
            (vacancy_id, recipient, subject),
        )
        if cursor.rowcount == 0:
            cursor = self.conn.execute(
                """UPDATE vacancy_email_sends SET status='pending', subject=?, created_at=CURRENT_TIMESTAMP
                   WHERE vacancy_id=? AND recipient=? AND status='failed'""",
                (subject, vacancy_id, recipient),
            )
        self.conn.commit()
        return cursor.rowcount == 1

    def mark_sent(self, vacancy_id: str, recipient: str) -> None:
        self.conn.execute(
            """UPDATE vacancy_email_sends SET status='sent', sent_at=CURRENT_TIMESTAMP
               WHERE vacancy_id=? AND recipient=?""",
            (vacancy_id, recipient),
        )
        self.conn.commit()

    def mark_failed(self, vacancy_id: str, recipient: str) -> None:
        self.conn.execute(
            """UPDATE vacancy_email_sends SET status='failed'
               WHERE vacancy_id=? AND recipient=? AND status='pending'""",
            (vacancy_id, recipient),
        )
        self.conn.commit()
