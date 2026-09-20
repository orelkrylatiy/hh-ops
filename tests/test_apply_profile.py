from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _load_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "apply_profile.py"
    spec = importlib.util.spec_from_file_location("apply_profile_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_lanes(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "lanes": [
                    {
                        "name": "frontend-primary",
                        "resume_alias": "primary",
                        "fallback_unfiltered_if_alias_missing": True,
                    },
                    {
                        "name": "ai-engineer",
                        "resume_alias": "ai-engineer",
                        "skip_if_alias_missing": True,
                        "search": "AI Engineer",
                        "limit": 25,
                        "pages": 12,
                        "ai_filter": "light",
                        "system_prompt": "prompts/cover_letter_ai_engineer.txt",
                        "hard_filter_file": "rules/apply-ai-hard-filter.regex",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_lane_args_target_explicit_resume_alias() -> None:
    module = _load_module()

    args = module._lane_args(
        {
            "name": "ai",
            "resume_alias": "ai-engineer",
            "search": "LLM Engineer",
            "limit": 20,
            "ai_filter": "light",
        },
        aliases={"ai-engineer": "resume-ai"},
        project_root=Path("/repo"),
    )

    assert args == [
        "--resume-alias",
        "ai-engineer",
        "--search",
        "LLM Engineer",
        "--ai-filter",
        "light",
        "--limit",
        "20",
    ]


def test_lane_skips_missing_optional_ai_alias(capsys) -> None:
    module = _load_module()

    args = module._lane_args(
        {
            "name": "ai",
            "resume_alias": "ai-engineer",
            "skip_if_alias_missing": True,
        },
        aliases={"primary": "resume-1"},
        project_root=Path("/repo"),
    )

    assert args is None
    assert "skip" in capsys.readouterr().out


def test_primary_lane_can_fallback_before_alias_registry_exists() -> None:
    module = _load_module()

    args = module._lane_args(
        {
            "name": "frontend-primary",
            "resume_alias": "primary",
            "fallback_unfiltered_if_alias_missing": True,
        },
        aliases={},
        project_root=Path("/repo"),
    )

    assert args == []


def test_main_runs_profile_lanes_with_alias_isolation(tmp_path, monkeypatch) -> None:
    module = _load_module()
    lane_dir = tmp_path / "lanes"
    lane_dir.mkdir()
    _write_lanes(lane_dir / "0555.json")

    calls: list[list[str]] = []
    monkeypatch.setenv("APPLY_LANES_DIR", str(lane_dir))
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        module,
        "_profile_aliases",
        lambda profile: {
            "primary": "resume-front",
            "ai-engineer": "resume-ai",
        },
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda command: calls.append(command) or 0,
    )

    result = module.main(
        [
            "--profile",
            "0555",
            "--dry-run",
            "--limit",
            "100",
        ]
    )

    assert result == 0
    assert len(calls) == 2

    frontend = calls[0]
    ai = calls[1]
    assert frontend[-2:] == ["--resume-alias", "primary"]
    assert "--dry-run" in frontend
    alias_index = ai.index("--resume-alias")
    assert ai[alias_index + 1] == "ai-engineer"
    search_index = ai.index("--search")
    assert ai[search_index + 1] == "AI Engineer"
    assert ai.count("--resume-alias") == 1


def test_main_skips_ai_lane_until_alias_exists(tmp_path, monkeypatch) -> None:
    module = _load_module()
    lane_dir = tmp_path / "lanes"
    lane_dir.mkdir()
    _write_lanes(lane_dir / "0555.json")

    calls: list[list[str]] = []
    monkeypatch.setenv("APPLY_LANES_DIR", str(lane_dir))
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        module,
        "_profile_aliases",
        lambda profile: {},
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda command: calls.append(command) or 0,
    )

    result = module.main(["--profile", "0555", "--dry-run"])

    assert result == 0
    assert len(calls) == 1
    assert "--resume-alias" not in calls[0]


def test_main_preserves_legacy_single_run_without_lane_file(tmp_path, monkeypatch) -> None:
    module = _load_module()
    calls: list[list[str]] = []

    monkeypatch.setenv("APPLY_LANES_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(
        module,
        "_run",
        lambda command: calls.append(command) or 0,
    )

    result = module.main(["--profile", "account2", "--dry-run", "--limit", "7"])

    assert result == 0
    assert len(calls) == 1
    assert calls[0][-5:] == [
        "--profile",
        "account2",
        "--dry-run",
        "--limit",
        "7",
    ]


def test_lane_config_rejects_duplicate_names(tmp_path) -> None:
    module = _load_module()
    path = tmp_path / "lanes.json"
    path.write_text(
        json.dumps(
            {
                "lanes": [
                    {"name": "same"},
                    {"name": "same"},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate lane name"):
        module._load_lanes(path)


def test_tracked_0555_lane_config_is_valid() -> None:
    module = _load_module()
    root = Path(__file__).resolve().parents[1]
    lanes = module._load_lanes(root / "rules" / "apply-lanes" / "0555.json")

    assert [lane["name"] for lane in lanes] == [
        "frontend-primary",
        "ai-engineer",
        "llm-engineer",
        "ai-automation",
    ]
    assert lanes[0]["resume_alias"] == "primary"
    assert all(
        lane.get("resume_alias") == "ai-engineer"
        for lane in lanes[1:]
    )
    assert all(lane.get("ai_filter") == "light" for lane in lanes[1:])
