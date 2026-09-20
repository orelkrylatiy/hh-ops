from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from hh_applicant_tool.operations.create_resume_variant import Operation


class ConfigStub(dict):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.save_calls: list[dict] = []

    def save(self, **kwargs) -> None:
        self.update(kwargs)
        self.save_calls.append(kwargs)


class ApiStub:
    def __init__(self) -> None:
        self.posts: list[tuple[str, object, bool]] = []
        self.full_resume = {
            "id": "source-1",
            "first_name": "Max",
            "last_name": "Example",
            "title": "Frontend developer",
            "area": {"id": "3", "name": "Екатеринбург"},
            "professional_roles": [{"id": "96", "name": "Программист, разработчик"}],
            "contact": [
                {
                    "type": {"id": "cell", "name": "Мобильный"},
                    "value": {
                        "country": "7",
                        "city": "900",
                        "number": "1234567",
                        "formatted": "+7 900 123-45-67",
                    },
                }
            ],
            "education": {"level": {"id": "higher", "name": "Высшее"}},
            "experience": [],
            "status": {"id": "published"},
            "alternate_url": "https://example.test/source-1",
        }

    def get(self, endpoint: str, **kwargs):
        if endpoint == "/resumes/source-1":
            return self.full_resume
        if endpoint == "/suggests/professional_roles":
            return {
                "items": [
                    {"id": "96", "text": kwargs.get("text")},
                ]
            }
        if endpoint == "/industries":
            return []
        raise AssertionError(f"unexpected GET {endpoint}")

    def post(self, endpoint: str, payload=None, as_json: bool = False):
        self.posts.append((endpoint, payload, as_json))
        if endpoint == "/resumes":
            return {"id": "resume-ai"}
        if endpoint == "/resumes/resume-ai/publish":
            return {}
        raise AssertionError(f"unexpected POST {endpoint}")


def _variant(tmp_path: Path) -> Path:
    path = tmp_path / "ai.toml"
    path.write_text(
        """
title = "AI Engineer"

skills = "LLM automation and agent workflows"

skill_set = ["Python", "LLM", "OpenAI API"]

[[professional_roles]]
_suggest = "/suggests/professional_roles"
text = "Программист, разработчик"

[[experience]]
company = "Собственные AI/automation проекты"
position = "AI Engineer"
start = "2025-01-01"
description = "Built safe LLM automation."
""".strip(),
        encoding="utf-8",
    )
    return path


def _args(
    variant: Path,
    *,
    dry_run: bool = False,
    publish: bool = False,
    replace_alias: bool = False,
    source_resume_id: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        variant=variant,
        alias="ai-engineer",
        source_resume_id=source_resume_id,
        publish=publish,
        dry_run=dry_run,
        replace_alias=replace_alias,
    )


def _tool(config: ConfigStub | None = None):
    api = ApiStub()
    cfg = config or ConfigStub()
    tool = SimpleNamespace(
        api_client=api,
        config=cfg,
        get_resumes=lambda: [
            {
                "id": "source-1",
                "title": "Frontend",
                "status": {"id": "published"},
            }
        ],
    )
    return tool, api, cfg


def test_variant_dry_run_inherits_profile_data_without_writes(tmp_path, capsys) -> None:
    tool, api, config = _tool()

    result = Operation().run(tool, _args(_variant(tmp_path), dry_run=True, publish=True))

    assert result is None
    assert api.posts == []
    assert config.save_calls == []

    output = json.loads(capsys.readouterr().out)
    assert output["source_resume_id"] == "source-1"
    assert output["alias"] == "ai-engineer"
    assert output["payload"]["first_name"] == "Max"
    assert output["payload"]["area"] == {"id": "3"}
    assert output["payload"]["professional_roles"] == [{"id": "96"}]
    assert "formatted" not in output["payload"]["contact"][0]["value"]
    assert output["payload"]["experience"][0]["company"] == "Собственные AI/automation проекты"


def test_variant_live_creates_publishes_and_registers_aliases(tmp_path) -> None:
    tool, api, config = _tool()

    result = Operation().run(tool, _args(_variant(tmp_path), publish=True))

    assert result is None
    assert [call[0] for call in api.posts] == [
        "/resumes",
        "/resumes/resume-ai/publish",
    ]
    create_payload = api.posts[0][1]
    assert isinstance(create_payload, dict)
    assert create_payload["title"] == "AI Engineer"
    assert create_payload["first_name"] == "Max"
    assert config["resume_aliases"] == {
        "primary": "source-1",
        "ai-engineer": "resume-ai",
    }


def test_existing_alias_refuses_duplicate_creation_without_replace(tmp_path) -> None:
    config = ConfigStub(
        {
            "resume_aliases": {
                "primary": "source-1",
                "ai-engineer": "existing-ai",
            }
        }
    )
    tool, api, _ = _tool(config)

    result = Operation().run(tool, _args(_variant(tmp_path)))

    assert result == 1
    assert api.posts == []


def test_replace_alias_updates_target_but_keeps_primary(tmp_path) -> None:
    config = ConfigStub(
        {
            "resume_aliases": {
                "primary": "source-1",
                "ai-engineer": "old-ai",
            }
        }
    )
    tool, _, _ = _tool(config)

    result = Operation().run(
        tool,
        _args(_variant(tmp_path), replace_alias=True),
    )

    assert result is None
    assert config["resume_aliases"] == {
        "primary": "source-1",
        "ai-engineer": "resume-ai",
    }


def test_multiple_resumes_without_primary_alias_fail_closed(tmp_path) -> None:
    tool, api, config = _tool()
    tool.get_resumes = lambda: [
        {"id": "resume-a", "status": {"id": "published"}},
        {"id": "resume-b", "status": {"id": "published"}},
    ]

    result = Operation().run(tool, _args(_variant(tmp_path)))

    assert result == 1
    assert api.posts == []
    assert config.save_calls == []


def test_explicit_source_resume_must_exist(tmp_path) -> None:
    tool, api, _ = _tool()

    result = Operation().run(
        tool,
        _args(_variant(tmp_path), source_resume_id="missing"),
    )

    assert result == 1
    assert api.posts == []


def test_tracked_0555_ai_variant_is_project_based_and_single_experience() -> None:
    root = Path(__file__).resolve().parents[1]
    variant_path = root / "resumes" / "variants" / "0555-ai-engineer.toml"
    raw = variant_path.read_text(encoding="utf-8")

    import tomllib

    payload = tomllib.loads(raw)

    assert payload["title"] == "AI Engineer / LLM Automation Engineer"
    assert len(payload["experience"]) == 1
    assert payload["experience"][0]["company"] == "Собственные AI/automation проекты"
    assert "hh-ops" in payload["experience"][0]["description"]
    assert "BrainWave" in payload["experience"][0]["description"]
    assert "SDD" not in raw
