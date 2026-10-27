from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedContact:
    contact_type: str
    value: str
    normalized_value: str


_TELEGRAM_URL_RE = re.compile(
    r"(?i)\b(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([a-z0-9_]{5,32})\b"
)
_TELEGRAM_HANDLE_RE = re.compile(r"(?<![\w@])@([a-zA-Z0-9_]{5,32})\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")


def extract_contacts(text: str) -> list[ExtractedContact]:
    """Extract Telegram handles/links and plausible phone numbers from chat text."""
    found: list[ExtractedContact] = []
    seen: set[tuple[str, str]] = set()

    for match in _TELEGRAM_URL_RE.finditer(text or ""):
        username = match.group(1)
        normalized = f"@{username.lower()}"
        key = ("telegram", normalized)
        if key not in seen:
            seen.add(key)
            found.append(ExtractedContact("telegram", match.group(0), normalized))

    text_without_emails = re.sub(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", "", text or "")
    for match in _TELEGRAM_HANDLE_RE.finditer(text_without_emails):
        username = match.group(1)
        normalized = f"@{username.lower()}"
        key = ("telegram", normalized)
        if key not in seen:
            seen.add(key)
            found.append(ExtractedContact("telegram", match.group(0), normalized))

    for match in _PHONE_RE.finditer(text or ""):
        raw = match.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if not 10 <= len(digits) <= 15:
            continue
        normalized = f"+{digits}" if raw.startswith("+") else digits
        key = ("phone", normalized)
        if key not in seen:
            seen.add(key)
            found.append(ExtractedContact("phone", raw, normalized))

    return found
