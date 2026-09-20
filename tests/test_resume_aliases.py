from __future__ import annotations

from types import SimpleNamespace

import pytest

from hh_applicant_tool.utils.resume_aliases import (
    get_resume_aliases,
    normalize_resume_alias,
    resolve_resume_alias,
    save_resume_aliases,
)


class ConfigStub(dict):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.saved: dict | None = None

    def save(self, **kwargs) -> None:
        self.update(kwargs)
        self.saved = kwargs


def test_resume_aliases_round_trip() -> None:
    config = ConfigStub({"resume_aliases": {"primary": "resume-1"}})

    aliases = save_resume_aliases(config, {"ai-engineer": "resume-2"})

    assert aliases == {
        "primary": "resume-1",
        "ai-engineer": "resume-2",
    }
    assert config.saved == {"resume_aliases": aliases}
    assert resolve_resume_alias(config, "AI-ENGINEER") == "resume-2"


@pytest.mark.parametrize(
    "alias",
    ["", "AI Engineer", "ai/engineer", "_ai", " ai engineer "],
)
def test_resume_alias_rejects_unsafe_names(alias: str) -> None:
    with pytest.raises(ValueError):
        normalize_resume_alias(alias)


def test_resume_aliases_reject_invalid_config_shape() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        get_resume_aliases({"resume_aliases": ["resume-1"]})


def test_unknown_alias_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unknown resume alias"):
        resolve_resume_alias({"resume_aliases": {"primary": "resume-1"}}, "ai-engineer")
