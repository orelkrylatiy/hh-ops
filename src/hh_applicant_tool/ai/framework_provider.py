"""Humanizer Framework provider used for all non-CAPTCHA text generation."""

from __future__ import annotations

import os
from pathlib import Path

from humanizer_framework.env import load_env
from humanizer_framework.providers import (
    HarnessProvider,
    OpenCodeCLIProvider,
    ZaiAnthropicProvider,
)
from humanizer_framework.providers.base import Provider

DEFAULT_OPENCODE_MODEL = "openai/gpt-5.6-luna"
DEFAULT_ZAI_MODEL = "glm-5.3-flash"
# ZaiAnthropicProvider сам резолвит ключ из первой непустой переменной.
ZAI_KEY_ENV_VARS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ZAI_API_KEY")


def _ensure_env_file() -> None:
    # Ключ GLM лежит в репозиторном .env, но HUMANIZER_ENV_FILE выставляет
    # только FrameworkWriter, а фабрику зовут и мимо него (ChatOpenAI).
    # load_env() никогда не перезаписывает уже установленные переменные.
    os.environ.setdefault(
        "HUMANIZER_ENV_FILE",
        str(Path(__file__).resolve().parents[3] / ".env"),
    )


def generation_provider() -> Provider:
    """GLM первой ногой (если ключ z.ai есть), OpenCode — фолбэк.

    Капча сюда не заходит: она решается своим opencode-каналом
    (см. utils/opencode.py и OPENCODE_CAPTCHA_MODEL).
    """
    _ensure_env_file()
    load_env()

    legs: list[Provider] = []
    if any(os.environ.get(name, "").strip() for name in ZAI_KEY_ENV_VARS):
        model = os.environ.get("LLM_MODEL", DEFAULT_ZAI_MODEL).strip().lower()
        legs.append(ZaiAnthropicProvider(model=model or DEFAULT_ZAI_MODEL))

    opencode_model = os.environ.get(
        "HUMANIZER_OPENCODE_MODEL", DEFAULT_OPENCODE_MODEL
    ).strip()
    legs.append(OpenCodeCLIProvider(model=opencode_model or DEFAULT_OPENCODE_MODEL))

    return HarnessProvider(legs)
