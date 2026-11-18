"""Upstream-inspired HH chat timing and structured-reply safeguards.

This module has no network side effects. Missing or malformed timestamps fail
closed whenever age-dependent behaviour is enabled.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any


def message_age_seconds(message: Mapping[str, Any], now: datetime | None = None) -> float | None:
    raw = message.get("created_at") or message.get("creation_time") or message.get("creationTime")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        created = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        return None
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        return None
    elapsed = (reference - created).total_seconds()
    return max(0.0, elapsed) if elapsed >= -60 else None


def is_bot_message(message: Mapping[str, Any]) -> bool:
    """Recognize HH participantDisplay.isBot and applicant API bot metadata."""
    for obj in (
        message,
        message.get("participantDisplay"),
        message.get("author"),
        message.get("sender"),
        message.get("payload"),
    ):
        if isinstance(obj, Mapping) and (
            obj.get("isBot") is True or obj.get("is_bot") is True
        ):
            return True
    return False


def is_incoming_reply_due(
    message: Mapping[str, Any],
    *,
    human_cooldown_minutes: float = 0,
    max_chat_age_days: float = 0,
    now: datetime | None = None,
) -> bool:
    if human_cooldown_minutes <= 0 and max_chat_age_days <= 0:
        return True
    age = message_age_seconds(message, now)
    if age is None:
        return False
    if max_chat_age_days > 0 and age > max_chat_age_days * 86400:
        return False
    if not is_bot_message(message) and age < max(0, human_cooldown_minutes) * 60:
        return False
    return True


def is_followup_due(
    message: Mapping[str, Any],
    *,
    after_days: float = 2,
    max_chat_age_days: float = 30,
    now: datetime | None = None,
) -> bool:
    """Only follow up on an old enough applicant message in a recent chat."""
    age = message_age_seconds(message, now)
    return bool(
        age is not None
        and age >= max(after_days, 0) * 86400
        and max_chat_age_days > 0
        and age <= max_chat_age_days * 86400
    )


def quick_reply_options(message: Mapping[str, Any]) -> tuple[str, ...]:
    """Read explicit HH send-options; never infer buttons from message prose."""
    payload = message.get("payload")
    objects = (message, payload) if isinstance(payload, Mapping) else (message,)
    for obj in objects:
        for key in ("quick_replies", "quickReplies", "reply_options"):
            options = obj.get(key)
            if not isinstance(options, list):
                continue
            texts: list[str] = []
            for option in options:
                if isinstance(option, Mapping):
                    if option.get("type", "send") != "send":
                        continue
                    raw = option.get("text")
                else:
                    raw = option
                if isinstance(raw, str):
                    clean = " ".join(raw.split())
                    if clean and len(clean) <= 160 and clean not in texts:
                        texts.append(clean)
            if 1 <= len(texts) <= 12:
                return tuple(texts)
    return ()


def choose_quick_reply(response: str, options: tuple[str, ...]) -> str | None:
    """Accept exact/fuzzy choice only with sufficient unambiguous confidence."""
    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip().casefold().strip(" .!•-"))

    query = normalize(response)
    if not query or not options:
        return None
    exact = [option for option in options if normalize(option) == query]
    if len(exact) == 1:
        return exact[0]
    scores = sorted(
        ((SequenceMatcher(None, query, normalize(option)).ratio(), option) for option in options),
        reverse=True,
    )
    if scores[0][0] < 0.85 or (len(scores) > 1 and scores[0][0] - scores[1][0] < 0.05):
        return None
    return scores[0][1]
