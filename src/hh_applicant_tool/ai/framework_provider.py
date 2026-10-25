"""Humanizer Framework provider used for all non-CAPTCHA text generation."""

from __future__ import annotations

import os

from humanizer_framework.providers import HarnessProvider, OpenCodeCLIProvider
from humanizer_framework.providers.base import Provider

DEFAULT_OPENCODE_MODEL = "openai/gpt-5.6-luna"


def opencode_provider() -> Provider:
    """Return the configured OpenCode backend without API-key fallbacks."""
    model = os.environ.get("HUMANIZER_OPENCODE_MODEL", DEFAULT_OPENCODE_MODEL).strip()
    if not model:
        model = DEFAULT_OPENCODE_MODEL
    return HarnessProvider([OpenCodeCLIProvider(model=model)])
