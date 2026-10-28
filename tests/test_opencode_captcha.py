from __future__ import annotations

import json

from hh_applicant_tool.utils import opencode


def test_opencode_captcha_preserves_cyrillic_phrase(tmp_path, monkeypatch) -> None:
    image_path = tmp_path / "captcha.png"
    metadata_path = tmp_path / "CURRENT.json"
    metadata_path.write_text(
        json.dumps({"image": str(image_path)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        opencode,
        "run_opencode",
        lambda *args, **kwargs: "Текст на изображении: «автов ожидание»",
    )

    assert opencode.solve_captcha_with_opencode(metadata_path) == "автов ожидание"


def test_opencode_captcha_uses_61_sol(tmp_path, monkeypatch) -> None:
    image_path = tmp_path / "captcha.png"
    metadata_path = tmp_path / "CURRENT.json"
    metadata_path.write_text(
        json.dumps({"image": str(image_path)}),
        encoding="utf-8",
    )

    def fake_run(*args, **kwargs):
        assert kwargs["models"] == (opencode.OPENCODE_FALLBACK_MODEL,)
        return "изъянов налущив"

    monkeypatch.setattr(opencode, "run_opencode", fake_run)

    assert opencode.solve_captcha_with_opencode(metadata_path) == "изъянов налущив"
