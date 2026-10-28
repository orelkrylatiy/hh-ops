from __future__ import annotations

import pytest
from humanizer_framework.providers import (
    OpenCodeCLIProvider,
    ZaiAnthropicProvider,
)

from hh_applicant_tool.ai import framework_provider
from hh_applicant_tool.ai.framework_provider import generation_provider


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Изолируем фабрику от реального .env и ключей хоста."""
    monkeypatch.setenv("HUMANIZER_ENV_FILE", str(tmp_path / "absent.env"))
    for name in framework_provider.ZAI_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("HUMANIZER_OPENCODE_MODEL", raising=False)
    # load_env() кешируется глобально — сбрасываем, чтобы тест прочитал tmp-файл.
    monkeypatch.setattr(
        "humanizer_framework.env._LOADED", False
    )


def test_generation_provider_glm_first_when_key_present(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setenv("HUMANIZER_ENV_FILE", str(env_file))
    monkeypatch.setattr("humanizer_framework.env._LOADED", False)

    provider = generation_provider()

    assert len(provider.providers) == 2
    assert isinstance(provider.providers[0], ZaiAnthropicProvider)
    assert isinstance(provider.providers[1], OpenCodeCLIProvider)


def test_generation_provider_opencode_only_without_key(monkeypatch) -> None:
    provider = generation_provider()

    assert len(provider.providers) == 1
    assert isinstance(provider.providers[0], OpenCodeCLIProvider)


def test_generation_provider_reads_key_from_env_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("HUMANIZER_ENV_FILE", str(env_file))
    monkeypatch.setattr("humanizer_framework.env._LOADED", False)

    provider = generation_provider()

    glm = provider.providers[0]
    assert isinstance(glm, ZaiAnthropicProvider)
    assert glm._resolve_key() == "from-file"


def test_generation_provider_model_from_llm_env(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=test-key\nLLM_MODEL=GLM-5.3-Flash\n", encoding="utf-8")
    monkeypatch.setenv("HUMANIZER_ENV_FILE", str(env_file))
    monkeypatch.setattr("humanizer_framework.env._LOADED", False)

    provider = generation_provider()

    glm = provider.providers[0]
    assert isinstance(glm, ZaiAnthropicProvider)
    assert glm.model == "glm-5.3-flash"
