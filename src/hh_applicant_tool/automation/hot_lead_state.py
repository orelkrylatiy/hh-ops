from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_HOT_LEAD_SCHEMA = """
CREATE TABLE IF NOT EXISTS hot_lead_events (
    chat_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    is_hot INTEGER NOT NULL,
    confidence REAL NOT NULL,
    human_likelihood TEXT NOT NULL,
    reason TEXT NOT NULL,
    next_step TEXT NOT NULL DEFAULT '',
    message_text TEXT NOT NULL,
    vacancy_name TEXT,
    employer_name TEXT,
    notified INTEGER NOT NULL DEFAULT 0,
    notification_attempts INTEGER NOT NULL DEFAULT 0,
    last_notification_error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_hot_lead_pending
    ON hot_lead_events(is_hot, notified, updated_at);
"""


class HotLeadStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_HOT_LEAD_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    def get(self, chat_id: str, message_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM hot_lead_events WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
        return dict(row) if row else None

    def record(
        self,
        *,
        chat_id: str,
        message_id: str,
        is_hot: bool,
        confidence: float,
        human_likelihood: str,
        reason: str,
        next_step: str,
        message_text: str,
        vacancy_name: str,
        employer_name: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hot_lead_events (
                    chat_id, message_id, is_hot, confidence,
                    human_likelihood, reason, next_step, message_text,
                    vacancy_name, employer_name
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    is_hot = excluded.is_hot,
                    confidence = excluded.confidence,
                    human_likelihood = excluded.human_likelihood,
                    reason = excluded.reason,
                    next_step = excluded.next_step,
                    message_text = excluded.message_text,
                    vacancy_name = excluded.vacancy_name,
                    employer_name = excluded.employer_name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    chat_id,
                    message_id,
                    int(is_hot),
                    confidence,
                    human_likelihood,
                    reason,
                    next_step,
                    message_text,
                    vacancy_name,
                    employer_name,
                ),
            )

    def pending_notifications(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT *
                FROM hot_lead_events
                WHERE is_hot = 1 AND notified = 0
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (max(limit, 1),),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_notified(self, chat_id: str, message_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE hot_lead_events
                SET notified = 1,
                    notification_attempts = notification_attempts + 1,
                    last_notification_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = ? AND message_id = ?
                """,
                (chat_id, message_id),
            )

    def mark_notification_failed(
        self,
        chat_id: str,
        message_id: str,
        error: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE hot_lead_events
                SET notification_attempts = notification_attempts + 1,
                    last_notification_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = ? AND message_id = ?
                """,
                (error[:500], chat_id, message_id),
            )

    def recent_hot(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT *
                FROM hot_lead_events
                WHERE is_hot = 1
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(limit, 1),),
            ).fetchall()
        return [dict(row) for row in rows]
