from __future__ import annotations

import re
from typing import Any

RESUME_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def normalize_resume_alias(alias: str) -> str:
    value = alias.strip().lower()
    if not RESUME_ALIAS_RE.fullmatch(value):
        raise ValueError(
            "Invalid resume alias. Use lowercase letters, numbers, dot, dash or underscore."
        )
    return value


def get_resume_aliases(config: dict[str, Any]) -> dict[str, str]:
    raw = config.get("resume_aliases", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("'resume_aliases' must be a JSON object")

    aliases: dict[str, str] = {}
    for raw_alias, raw_resume_id in raw.items():
        if not isinstance(raw_alias, str):
            raise ValueError("resume alias names must be strings")
        alias = normalize_resume_alias(raw_alias)
        resume_id = str(raw_resume_id or "").strip()
        if not resume_id:
            raise ValueError(f"resume alias {alias!r} has an empty resume id")
        aliases[alias] = resume_id
    return aliases


def resolve_resume_alias(config: dict[str, Any], alias: str) -> str:
    normalized = normalize_resume_alias(alias)
    aliases = get_resume_aliases(config)
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown resume alias: {normalized}") from exc


def save_resume_aliases(
    config: Any,
    updates: dict[str, str],
) -> dict[str, str]:
    aliases = get_resume_aliases(config)
    for alias, resume_id in updates.items():
        normalized = normalize_resume_alias(alias)
        value = str(resume_id or "").strip()
        if not value:
            raise ValueError(f"resume alias {normalized!r} has an empty resume id")
        aliases[normalized] = value

    config.save(resume_aliases=aliases)
    return aliases
