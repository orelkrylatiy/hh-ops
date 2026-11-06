"""Configuration-driven vacancy routing and cross-profile claiming."""

from __future__ import annotations

import calendar
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..constants import CONFIG_DIR


class VacancyRouting:
    def __init__(
        self,
        profile_id: str,
        config_path: str | os.PathLike[str] | None = None,
        ledger_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.profile_id = profile_id
        project_root = Path(__file__).resolve().parents[3]
        path = Path(
            config_path
            or os.environ.get(
                "HH_VACANCY_ROUTING_FILE", str(project_root / "rules" / "vacancy-routing.json")
            )
        )
        self.enabled = path.is_file()
        self.rule: dict[str, Any] = {}
        if self.enabled:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.rule = dict(payload.get("profiles", {}).get(profile_id) or {})
            if not self.rule:
                self.enabled = False
        default_base = os.environ.get("CONFIG_DIR") or str(CONFIG_DIR)
        self.ledger_path = Path(
            ledger_path
            or os.environ.get(
                "HH_VACANCY_LEDGER_PATH", str(Path(default_base) / "vacancy_routing.sqlite")
            )
        ).expanduser()

    @staticmethod
    def _text(vacancy: dict[str, Any]) -> str:
        snippet = vacancy.get("snippet") or {}
        skills = " ".join(
            str(x.get("name", "")) for x in vacancy.get("key_skills", []) if isinstance(x, dict)
        )
        return " ".join(
            str(x or "")
            for x in (
                vacancy.get("name"),
                snippet.get("requirement"),
                snippet.get("responsibility"),
                skills,
            )
        ).lower()

    def matches(self, vacancy: dict[str, Any]) -> bool:
        if not self.enabled:
            return True
        text = self._text(vacancy)
        required = self.rule.get("required_any", [])
        excluded = self.rule.get("excluded_any", [])
        return (not required or any(re.search(p, text, re.I) for p in required)) and not any(
            re.search(p, text, re.I) for p in excluded
        )

    def _connect(self) -> sqlite3.Connection:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.ledger_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("""CREATE TABLE IF NOT EXISTS vacancy_claims (
            vacancy_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
        return conn

    def claim(self, vacancy_id: str | int) -> bool:
        conn = self._connect()
        try:
            key = str(vacancy_id)
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO vacancy_claims(vacancy_id, profile_id, status) VALUES (?, ?, 'claimed')",
                    (key, self.profile_id),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT created_at, status FROM vacancy_claims WHERE vacancy_id = ?", (key,)
                ).fetchone()
                if row and row[1] == "sent":
                    conn.rollback()
                    return False
                ttl = int(os.environ.get("HH_VACANCY_CLAIM_TTL_SECONDS", "1800"))
                stale = False
                if row:
                    try:
                        created = calendar.timegm(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
                        stale = time.time() - created > ttl
                    except (TypeError, ValueError, OverflowError):
                        stale = False
                if not stale:
                    conn.rollback()
                    return False
                conn.execute("DELETE FROM vacancy_claims WHERE vacancy_id = ?", (key,))
                conn.execute(
                    "INSERT INTO vacancy_claims(vacancy_id, profile_id, status) VALUES (?, ?, 'claimed')",
                    (key, self.profile_id),
                )
            conn.commit()
            return True
        finally:
            conn.close()

    def release(self, vacancy_id: str | int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM vacancy_claims WHERE vacancy_id = ? AND profile_id = ? AND status = 'claimed'",
                (str(vacancy_id), self.profile_id),
            )
            conn.commit()
        finally:
            conn.close()

    def confirm(self, vacancy_id: str | int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE vacancy_claims SET status = 'sent' WHERE vacancy_id = ? AND profile_id = ?",
                (str(vacancy_id), self.profile_id),
            )
