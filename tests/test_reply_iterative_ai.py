from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "reply_iterative_ai.py"
    spec = importlib.util.spec_from_file_location("reply_iterative_ai_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hot_lead_runtime_defaults(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.delenv("HOT_LEADS_ENABLED", raising=False)
    monkeypatch.delenv("HOT_LEAD_MIN_CONFIDENCE", raising=False)
    monkeypatch.delenv("HOT_LEAD_TELEGRAM_TIMEOUT", raising=False)

    assert module.hot_lead_runtime_settings() == (True, 0.85, 10.0)


def test_invalid_hot_lead_enabled_flag_disables_side_channel(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("HOT_LEADS_ENABLED", "definitely")

    enabled, confidence, telegram_timeout = module.hot_lead_runtime_settings()

    assert enabled is False
    assert confidence == 0.85
    assert telegram_timeout is None


def test_invalid_hot_lead_confidence_disables_side_channel(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("HOT_LEADS_ENABLED", "1")
    monkeypatch.setenv("HOT_LEAD_MIN_CONFIDENCE", "4.2")

    enabled, confidence, telegram_timeout = module.hot_lead_runtime_settings()

    assert enabled is False
    assert confidence == 0.85
    assert telegram_timeout is None


def test_invalid_telegram_timeout_keeps_classifier_enabled(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("HOT_LEADS_ENABLED", "1")
    monkeypatch.setenv("HOT_LEAD_MIN_CONFIDENCE", "0.91")
    monkeypatch.setenv("HOT_LEAD_TELEGRAM_TIMEOUT", "-5")

    enabled, confidence, telegram_timeout = module.hot_lead_runtime_settings()

    assert enabled is True
    assert confidence == 0.91
    assert telegram_timeout is None


def test_explicit_hot_lead_disable_ignores_other_hot_settings(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("HOT_LEADS_ENABLED", "0")
    monkeypatch.setenv("HOT_LEAD_MIN_CONFIDENCE", "invalid")
    monkeypatch.setenv("HOT_LEAD_TELEGRAM_TIMEOUT", "invalid")

    assert module.hot_lead_runtime_settings() == (False, 0.85, None)
