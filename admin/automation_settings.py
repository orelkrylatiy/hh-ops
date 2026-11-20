"""Linux cron controls exposed by the authenticated admin API.

The JSON file is the source of truth for the UI. A generated, shell-quoted
environment file lets existing cron launchers consume the same settings without
restarting Docker or evaluating arbitrary user-supplied shell fragments.
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SETTINGS_FILENAME = "admin_automation.json"
ENV_FILENAME = "admin_automation.env"


class AutomationSettings(BaseModel):
    mode: Literal["off", "dry-run", "live"] = "off"
    # Pause keeps the chosen mode so the operator can resume without reconfiguring it.
    paused: bool = False
    search_query: str = Field(default="Frontend разработчик", min_length=1, max_length=160)
    apply_limit: int = Field(default=10, ge=1, le=100)
    apply_pages: int = Field(default=5, ge=1, le=100)
    apply_per_page: int = Field(default=50, ge=1, le=100)
    reply_chats: int = Field(default=100, ge=1, le=500)
    human_cooldown_minutes: float = Field(default=15, ge=0, le=120)
    max_chat_age_days: float = Field(default=30, ge=1, le=365)
    followup_after_days: float = Field(default=2, ge=1, le=60)
    followups_enabled: bool = False
    auto_quick_replies: bool = False
    send_email: bool = False
    cleanup_delete_chat: bool = False

    @field_validator("search_query")
    @classmethod
    def validate_search(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ord(char) < 32 for char in cleaned):
            raise ValueError("Search query cannot contain control characters")
        return cleaned

    def to_env(self) -> dict[str, str]:
        if self.followup_after_days >= self.max_chat_age_days:
            raise ValueError("Follow-up delay must be shorter than maximum chat age")
        return {
            "HH_AUTOMATION_MODE": "off" if self.paused else self.mode,
            "HH_AUTOMATION_PAUSED": "on" if self.paused else "off",
            "SEARCH_QUERY": self.search_query,
            "APPLY_LIMIT": str(self.apply_limit),
            "APPLY_PAGES": str(self.apply_pages),
            "APPLY_PER_PAGE": str(self.apply_per_page),
            "REPLY_CHATS": str(self.reply_chats),
            "HH_REPLY_HUMAN_COOLDOWN_MINUTES": str(self.human_cooldown_minutes),
            "HH_REPLY_MAX_CHAT_AGE_DAYS": str(self.max_chat_age_days),
            "HH_REPLY_FOLLOWUP_AFTER_DAYS": str(self.followup_after_days),
            "HH_REPLY_FOLLOWUPS_ENABLED": "on" if self.followups_enabled else "off",
            "HH_REPLY_AUTO_QUICK_REPLIES": "on" if self.auto_quick_replies else "off",
            "APPLY_SEND_EMAIL": "1" if self.send_email else "0",
            "CLEANUP_DELETE_CHAT": "1" if self.cleanup_delete_chat else "0",
        }


def default_settings() -> AutomationSettings:
    """Build the first form from runtime environment, not hard-coded UI guesses."""
    def number(key: str, fallback: int) -> int:
        try:
            return int(os.getenv(key, str(fallback)))
        except ValueError:
            return fallback

    def fractional(key: str, fallback: float) -> float:
        try:
            return float(os.getenv(key, str(fallback)))
        except ValueError:
            return fallback

    def enabled(key: str, fallback: str = "off") -> bool:
        return os.getenv(key, fallback).lower() in {"1", "true", "yes", "on"}

    values = {
        "mode": os.getenv("HH_AUTOMATION_MODE", "off"),
        "paused": enabled("HH_AUTOMATION_PAUSED"),
        "search_query": os.getenv("SEARCH_QUERY", "Frontend разработчик"),
        "apply_limit": number("APPLY_LIMIT", 10),
        "apply_pages": number("APPLY_PAGES", 5),
        "apply_per_page": number("APPLY_PER_PAGE", 50),
        "reply_chats": number("REPLY_CHATS", 100),
        "human_cooldown_minutes": fractional("HH_REPLY_HUMAN_COOLDOWN_MINUTES", 15),
        "max_chat_age_days": fractional("HH_REPLY_MAX_CHAT_AGE_DAYS", 30),
        "followup_after_days": fractional("HH_REPLY_FOLLOWUP_AFTER_DAYS", 2),
        "followups_enabled": enabled("HH_REPLY_FOLLOWUPS_ENABLED"),
        "auto_quick_replies": enabled("HH_REPLY_AUTO_QUICK_REPLIES"),
        "send_email": enabled("APPLY_SEND_EMAIL", "0"),
        "cleanup_delete_chat": enabled("CLEANUP_DELETE_CHAT", "0"),
    }
    try:
        return AutomationSettings.model_validate(values)
    except ValueError:
        # Invalid legacy env values must not enable real outbound actions.
        return AutomationSettings()


def read_settings(config_root: Path) -> tuple[AutomationSettings, str]:
    path = config_root / SETTINGS_FILENAME
    if not path.exists():
        return default_settings(), "environment"
    return AutomationSettings.model_validate_json(path.read_text(encoding="utf-8")), "admin_override"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".admin-automation-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def save_settings(config_root: Path, settings: AutomationSettings) -> None:
    env_values = settings.to_env()
    env_file = "# Generated by HH Admin. Do not edit; change settings in the admin UI.\n"
    env_file += "".join(
        f"export {key}={shlex.quote(value)}\n" for key, value in env_values.items()
    )
    # Generate the shell file first; JSON is the last, visible commit marker.
    _atomic_write(config_root / ENV_FILENAME, env_file)
    _atomic_write(
        config_root / SETTINGS_FILENAME,
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2) + "\n",
    )
