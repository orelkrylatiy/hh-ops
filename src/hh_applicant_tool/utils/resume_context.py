from __future__ import annotations

from pathlib import Path
from typing import Any

from .resume_aliases import get_resume_aliases


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resume_alias_for_id(config: dict[str, Any], resume_id: str) -> str | None:
    target = str(resume_id or "").strip()
    if not target:
        return None
    return next(
        (
            alias
            for alias, configured_id in get_resume_aliases(config).items()
            if configured_id == target
        ),
        None,
    )


def load_resume_context(
    *,
    config: dict[str, Any] | None = None,
    resume_id: str = "",
    alias: str | None = None,
    profile_id: str = "",
    root: Path | None = None,
) -> str:
    """Load optional context for the selected resume; missing means no override."""
    resolved_alias = (alias or "").strip().lower()
    if not resolved_alias and config is not None:
        resolved_alias = resume_alias_for_id(config, resume_id) or ""
    if not resolved_alias:
        return ""

    base = root or project_root()
    candidates = []
    if profile_id.strip():
        candidates.append(
            base / "resumes" / "context" / profile_id.strip() / f"{resolved_alias}.md"
        )
    candidates.append(base / "resumes" / "context" / "default" / f"{resolved_alias}.md")
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return ""
