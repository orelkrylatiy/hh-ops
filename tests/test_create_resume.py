from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hh_applicant_tool.operations.create_resume import (
    Operation,
    ResumeTemplateError,
    _load_template,
    _resolve_industries,
    _resolve_suggests,
    _validate_payload,
)


def _minimal_payload() -> dict:
    return {
        "first_name": "Иван",
        "last_name": "Иванов",
        "title": "Frontend developer",
        "area": {"id": "1"},
        "professional_roles": [{"id": "96"}],
    }


def _args(path: Path, *, dry_run: bool = False, publish: bool = False):
    return SimpleNamespace(template=path, dry_run=dry_run, publish=publish)


class FakeApi:
    def __init__(self) -> None:
        self.posts: list[tuple[str, object, bool]] = []
        self.gets: list[tuple[str, dict]] = []
        self.create_result: object = {"id": "new-resume"}
        self.suggestions: dict[str, list[dict]] = {}
        self.industries: object = []

    def get(self, endpoint: str, **kwargs):
        self.gets.append((endpoint, kwargs))
        if endpoint == "/industries":
            return self.industries
        text = str(kwargs.get("text") or "")
        return {"items": self.suggestions.get(text, [])}

    def post(self, endpoint: str, payload=None, as_json: bool = False):
        self.posts.append((endpoint, payload, as_json))
        if endpoint == "/resumes":
            return self.create_result
        return {}


def test_load_template_rejects_unknown_extension(tmp_path) -> None:
    path = tmp_path / "resume.txt"
    path.write_text("hello", encoding="utf-8")

    with pytest.raises(ResumeTemplateError, match="Неподдерживаемый формат"):
        _load_template(path)


def test_resolve_suggests_prefers_exact_match_over_first_result() -> None:
    api = FakeApi()
    api.suggestions["Москва"] = [
        {"id": "2019", "text": "Московская область"},
        {"id": "1", "text": "Москва"},
    ]

    result = _resolve_suggests(
        api,
        {"area": {"_suggest": "/suggests/area_leaves", "text": "Москва"}},
    )

    assert result == {"area": {"id": "1"}}


def test_resolve_suggests_fails_on_ambiguous_non_exact_results() -> None:
    api = FakeApi()
    api.suggestions["React"] = [
        {"id": "1", "text": "React developer"},
        {"id": "2", "text": "React Native developer"},
    ]

    with pytest.raises(ResumeTemplateError, match="Неоднозначное значение"):
        _resolve_suggests(
            api,
            {"professional_roles": [{"_suggest": "/suggests/professional_roles", "text": "React"}]},
        )


def test_resolve_suggests_fails_when_hh_returns_no_items() -> None:
    api = FakeApi()

    with pytest.raises(ResumeTemplateError, match="не нашёл"):
        _resolve_suggests(
            api,
            {"area": {"_suggest": "/suggests/area_leaves", "text": "НетТакогоГорода"}},
        )


def test_resolve_industries_requires_exact_unique_name() -> None:
    api = FakeApi()
    api.industries = [
        {
            "id": "7",
            "name": "IT",
            "industries": [
                {"id": "7.1", "name": "Разработка ПО"},
                {"id": "7.2", "name": "Интернет"},
            ],
        }
    ]
    experience = [{"industries": [{"name": "Разработка ПО"}]}]

    _resolve_industries(api, experience)

    assert experience == [{"industries": [{"id": "7.1"}]}]


def test_validate_payload_fails_closed_on_missing_core_fields() -> None:
    with pytest.raises(ResumeTemplateError, match="first_name"):
        _validate_payload({"title": "Developer"})


def test_dry_run_resolves_template_without_writes(tmp_path, capsys) -> None:
    template = tmp_path / "resume.toml"
    template.write_text(
        """
first_name = "Ivan"
last_name = "Ivanov"
title = "Frontend developer"

[area]
_suggest = "/suggests/area_leaves"
text = "Москва"

[[professional_roles]]
_suggest = "/suggests/professional_roles"
text = "Программист, разработчик"
""".strip(),
        encoding="utf-8",
    )
    api = FakeApi()
    api.suggestions = {
        "Москва": [{"id": "1", "text": "Москва"}],
        "Программист, разработчик": [{"id": "96", "text": "Программист, разработчик"}],
    }
    tool = SimpleNamespace(api_client=api, get_resumes=Mock())

    result = Operation().run(tool, _args(template, dry_run=True, publish=True))

    assert result is None
    assert api.posts == []
    tool.get_resumes.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert payload["area"] == {"id": "1"}
    assert payload["professional_roles"] == [{"id": "96"}]
    assert "_suggest" not in json.dumps(payload)


def test_create_and_publish_uses_id_returned_by_hh(tmp_path) -> None:
    template = tmp_path / "resume.toml"
    template.write_text(
        """
first_name = "Ivan"
last_name = "Ivanov"
title = "Frontend developer"

[area]
id = "1"

[[professional_roles]]
id = "96"
""".strip(),
        encoding="utf-8",
    )
    api = FakeApi()
    api.create_result = {"id": "created-123"}
    tool = SimpleNamespace(
        api_client=api,
        get_resumes=Mock(return_value=[{"id": "old"}]),
    )

    result = Operation().run(tool, _args(template, publish=True))

    assert result is None
    assert api.posts[0][0] == "/resumes"
    assert api.posts[0][2] is True
    assert api.posts[1][0] == "/resumes/created-123/publish"


def test_create_detects_unique_new_id_when_post_response_is_empty(tmp_path) -> None:
    template = tmp_path / "resume.toml"
    template.write_text(
        """
first_name = "Ivan"
last_name = "Ivanov"
title = "Frontend developer"

[area]
id = "1"

[[professional_roles]]
id = "96"
""".strip(),
        encoding="utf-8",
    )
    api = FakeApi()
    api.create_result = {}
    get_resumes = Mock(
        side_effect=[
            [{"id": "old"}],
            [{"id": "old"}, {"id": "new-only"}],
        ]
    )
    tool = SimpleNamespace(api_client=api, get_resumes=get_resumes)

    result = Operation().run(tool, _args(template, publish=True))

    assert result is None
    assert api.posts[-1][0] == "/resumes/new-only/publish"


def test_create_refuses_publish_when_multiple_new_ids_appear(tmp_path) -> None:
    template = tmp_path / "resume.toml"
    template.write_text(
        """
first_name = "Ivan"
last_name = "Ivanov"
title = "Frontend developer"

[area]
id = "1"

[[professional_roles]]
id = "96"
""".strip(),
        encoding="utf-8",
    )
    api = FakeApi()
    api.create_result = {}
    get_resumes = Mock(
        side_effect=[
            [{"id": "old"}],
            [{"id": "old"}, {"id": "new-a"}, {"id": "new-b"}],
        ]
    )
    tool = SimpleNamespace(api_client=api, get_resumes=get_resumes)

    result = Operation().run(tool, _args(template, publish=True))

    assert result == 1
    assert [call[0] for call in api.posts] == ["/resumes"]


def test_invalid_template_never_calls_create_endpoint(tmp_path) -> None:
    template = tmp_path / "resume.toml"
    template.write_text('title = "Only title"', encoding="utf-8")
    api = FakeApi()
    tool = SimpleNamespace(api_client=api, get_resumes=Mock())

    result = Operation().run(tool, _args(template))

    assert result == 1
    assert api.posts == []
    tool.get_resumes.assert_not_called()
