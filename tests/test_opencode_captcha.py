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
        assert kwargs["models"] == (opencode.OPENCODE_CAPTCHA_MODEL,)
        return "изъянов налущив"

    monkeypatch.setattr(opencode, "run_opencode", fake_run)

    assert opencode.solve_captcha_with_opencode(metadata_path) == "изъянов налущив"


def test_opencode_captcha_does_not_retry_same_model_on_timeout(tmp_path, monkeypatch) -> None:
    metadata = tmp_path / 'CURRENT.json'
    metadata.write_text(json.dumps({'image': str(tmp_path / 'image.png')}), encoding='utf-8')
    calls = []
    def fail(*args, **kwargs):
        calls.append(kwargs['models'])
        raise TimeoutError('probe timeout')
    monkeypatch.setattr(opencode, 'run_opencode', fail)
    assert opencode.solve_captcha_with_opencode(metadata) == ''
    assert calls == [(opencode.OPENCODE_CAPTCHA_MODEL,)]


def test_opencode_captcha_rejects_refusal_without_switching_model(tmp_path, monkeypatch) -> None:
    metadata = tmp_path / 'CURRENT.json'
    metadata.write_text(json.dumps({'image': str(tmp_path / 'image.png')}), encoding='utf-8')
    calls = []
    def refusal(*args, **kwargs):
        calls.append(kwargs['models'])
        return "Sorry, I can't help solve CAPTCHAs."
    monkeypatch.setattr(opencode, 'run_opencode', refusal)
    assert opencode.solve_captcha_with_opencode(metadata) == ''
    assert calls == [(opencode.OPENCODE_CAPTCHA_MODEL,)]
