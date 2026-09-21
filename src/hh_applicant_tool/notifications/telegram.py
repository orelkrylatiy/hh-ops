"""Best-effort Telegram notifications for successful HH applications."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_FAILURE_COOLDOWN_S = 60.0
_failure_cooldown_until = 0.0


def _enabled() -> bool:
    return os.getenv("HH_NOTIFY_TELEGRAM_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def build_application_success_message(
    *,
    profile: str,
    vacancy: dict[str, Any],
    resume_title: str,
    resume_id: str,
    resume_alias: str | None = None,
) -> str:
    """Build a concise plain-text success notification."""
    vacancy_name = str(vacancy.get("name") or "Без названия").strip()
    employer = vacancy.get("employer") or {}
    employer_name = str(employer.get("name") or "Не указан").strip()
    vacancy_url = str(vacancy.get("alternate_url") or "").strip()

    selector = (
        f"alias: {resume_alias}"
        if resume_alias
        else f"id: {resume_id}"
    )
    parts = [
        "✅ Отклик отправлен",
        f"Профиль: {profile}",
        f"Вакансия: {vacancy_name[:240]}",
        f"Компания: {employer_name[:180]}",
        f"Резюме: {resume_title[:220]} ({selector})",
    ]
    if vacancy_url:
        parts.append(f"HH: {vacancy_url}")
    return "\n".join(parts)


def _send_message(text: str) -> bool:
    global _failure_cooldown_until

    if not _enabled():
        return False

    now = time.monotonic()
    if now < _failure_cooldown_until:
        return False

    token = os.getenv("HH_NOTIFY_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("HH_NOTIFY_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning(
            "HH Telegram notifications enabled, but bot token/chat id is missing"
        )
        _failure_cooldown_until = now + _FAILURE_COOLDOWN_S
        return False

    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            body = json.loads(response.read().decode("utf-8"))
        if bool(body.get("ok")):
            return True
        logger.warning("HH Telegram sendMessage returned ok=false")
    except (OSError, ValueError, urllib.error.URLError):
        # Never include the request URL in logs: it contains the bot token.
        logger.warning("HH Telegram notification failed (%s)", "transport/error")
    _failure_cooldown_until = time.monotonic() + _FAILURE_COOLDOWN_S
    return False


def notify_application_success(
    *,
    profile: str,
    vacancy: dict[str, Any],
    resume_title: str,
    resume_id: str,
    resume_alias: str | None = None,
) -> bool:
    """Send one application-success notification when configured."""
    return _send_message(
        build_application_success_message(
            profile=profile,
            vacancy=vacancy,
            resume_title=resume_title,
            resume_id=resume_id,
            resume_alias=resume_alias,
        )
    )
