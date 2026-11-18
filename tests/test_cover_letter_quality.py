from __future__ import annotations

from hh_applicant_tool.operations._apply_vacancies_apply_flow import (
    append_application_contact,
    letter_quality_issues,
    resolve_contact_url,
)

GOOD_LETTER = (
    "Здравствуйте! Заинтересовала вакансия «Frontend-разработчик». "
    "Последние несколько лет работаю фронтенд-разработчиком с React и TypeScript "
    "в финтехе, поэтому задачи вакансии мне близки. Готов ответить на вопросы "
    "в переписке или на созвоне. Быстрее всего на связи в Telegram."
)


def test_good_letter_passes():
    assert letter_quality_issues(GOOD_LETTER) == []


def test_empty_letter_rejected():
    assert letter_quality_issues("") == ["empty letter"]
    assert letter_quality_issues("   ") == ["empty letter"]


def test_too_short_rejected():
    assert "letter is too short" in letter_quality_issues("Здравствуйте! Готов обсудить.")


def test_too_long_rejected():
    assert "letter is too long" in letter_quality_issues(GOOD_LETTER + " очень длинное " * 60)


def test_long_dash_rejected():
    assert "contains a long dash" in letter_quality_issues(GOOD_LETTER.replace("в", "—в", 1))


def test_unrendered_placeholder_rejected():
    assert "contains an unrendered placeholder" in letter_quality_issues(
        GOOD_LETTER.replace("фронтенд", "%(vacancy_name)s фронтенд", 1)
    )
    assert "contains an unrendered placeholder" in letter_quality_issues(
        GOOD_LETTER.replace("фронтенд", "[название компании] фронтенд", 1)
    )


def test_ai_cliche_rejected():
    assert "contains an AI-style cliche" in letter_quality_issues(
        GOOD_LETTER.replace("Готов ответить", "Важно отметить, что готов ответить", 1)
    )


def test_truncated_letter_rejected():
    cut = GOOD_LETTER[: GOOD_LETTER.rfind(" ")].rstrip(".,!")
    assert "letter looks truncated" in letter_quality_issues(cut)


def test_bracket_ending_is_not_truncation():
    assert letter_quality_issues(GOOD_LETTER + " (опыт 5+ лет)") == []


def test_unconfigured_contact_never_injects_another_accounts_telegram(monkeypatch):
    monkeypatch.delenv("HH_TELEGRAM", raising=False)
    assert resolve_contact_url({}) == ""
    result = append_application_contact(GOOD_LETTER, "")
    assert "t.me/" not in result
    assert result.endswith("Telegram.")
    assert letter_quality_issues(result) == []


def test_account_telegram_appended_only_when_explicitly_configured(monkeypatch):
    monkeypatch.delenv("HH_TELEGRAM", raising=False)
    contact = resolve_contact_url({"contact": {"telegram": "@example_account"}})
    assert contact == "https://t.me/example_account"
    result = append_application_contact(GOOD_LETTER, contact)
    assert result.count("https://t.me/example_account") == 1
    assert letter_quality_issues(result, contact) == []
