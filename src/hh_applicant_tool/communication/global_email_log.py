"""Fleet-wide recipient ledger for vacancy emails."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class GlobalEmailSendLog:
    """One shared SQLite ledger used by all HH profiles."""

    def __init__(self, path: str | Path, legacy_root: str | Path | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS global_email_sends (
                recipient TEXT PRIMARY KEY COLLATE NOCASE,
                vacancy_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT
            )"""
        )
        self.conn.commit()
        if legacy_root is not None:
            self._backfill_legacy_ledgers(Path(legacy_root))

    def _backfill_legacy_ledgers(self, root: Path) -> None:
        marker = "legacy_profile_ledgers_imported"
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS global_email_meta (key TEXT PRIMARY KEY)"
        )
        if self.conn.execute(
            "SELECT 1 FROM global_email_meta WHERE key=?", (marker,)
        ).fetchone():
            return

        candidates = [root / "data"]
        candidates.extend(path / "data" for path in root.iterdir() if path.is_dir())
        for database in candidates:
            if not database.is_file():
                continue
            try:
                legacy = sqlite3.connect(database, timeout=5)
                has_table = legacy.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vacancy_email_sends'"
                ).fetchone()
                if has_table:
                    rows = legacy.execute(
                        "SELECT recipient, vacancy_id, subject FROM vacancy_email_sends WHERE status='sent'"
                    ).fetchall()
                    profile_id = database.parent.name or "default"
                    for recipient, vacancy_id, subject in rows:
                        self.conn.execute(
                            """INSERT OR IGNORE INTO global_email_sends
                               (recipient, vacancy_id, profile_id, subject, status, sent_at)
                               VALUES (?, ?, ?, ?, 'sent', CURRENT_TIMESTAMP)""",
                            (str(recipient).strip().casefold(), str(vacancy_id), profile_id, subject),
                        )
                legacy.close()
            except sqlite3.Error:
                continue
        self.conn.execute("INSERT OR IGNORE INTO global_email_meta(key) VALUES (?)", (marker,))
        self.conn.commit()

    def claim(self, recipient: str, vacancy_id: str, profile_id: str, subject: str) -> bool:
        recipient = recipient.strip().casefold()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT status FROM global_email_sends WHERE recipient=?",
                (recipient,),
            ).fetchone()
            if row and row[0] != "failed":
                self.conn.rollback()
                return False
            if row:
                self.conn.execute(
                    """UPDATE global_email_sends
                       SET vacancy_id=?, profile_id=?, subject=?, status='pending',
                           created_at=CURRENT_TIMESTAMP, sent_at=NULL
                       WHERE recipient=?""",
                    (vacancy_id, profile_id, subject, recipient),
                )
            else:
                self.conn.execute(
                    """INSERT INTO global_email_sends
                       (recipient, vacancy_id, profile_id, subject, status)
                       VALUES (?, ?, ?, ?, 'pending')""",
                    (recipient, vacancy_id, profile_id, subject),
                )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def mark_sent(self, recipient: str) -> None:
        self.conn.execute(
            """UPDATE global_email_sends
               SET status='sent', sent_at=CURRENT_TIMESTAMP
               WHERE recipient=?""",
            (recipient.strip().casefold(),),
        )
        self.conn.commit()

    def mark_failed(self, recipient: str) -> None:
        self.conn.execute(
            """UPDATE global_email_sends SET status='failed'
               WHERE recipient=? AND status='pending'""",
            (recipient.strip().casefold(),),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
