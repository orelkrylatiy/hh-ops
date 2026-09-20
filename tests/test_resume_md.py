from __future__ import annotations

from pathlib import Path

import pytest

from hh_applicant_tool.utils.resume_md import parse_resume_md


def test_repository_resume_template_parses_expected_core_fields() -> None:
    template = Path("docs/resume_template.md").read_text(encoding="utf-8")

    payload = parse_resume_md(template)

    assert payload["first_name"] == "Иван"
    assert payload["last_name"] == "Иванов"
    assert payload["birth_date"] == "1990-01-15"
    assert payload["title"] == "Python-разработчик"
    assert payload["salary"] == {"amount": 200000, "currency": "RUR"}
    assert payload["area"] == {
        "_suggest": "/suggests/area_leaves",
        "text": "Москва",
    }
    assert payload["professional_roles"][0]["text"] == "Программист, разработчик"
    assert payload["contact"][0] == {
        "type": {"id": "email"},
        "value": "ivan@example.com",
    }
    assert payload["contact"][1]["value"] == {
        "country": "7",
        "city": "916",
        "number": "1234567",
    }
    assert payload["contact"][1]["comment"] == "звонить после 10:00"
    assert payload["experience"][0]["start"] == "2021-03-01"
    assert payload["experience"][1]["end"] == "2021-02-01"


@pytest.mark.parametrize(
    ("field_text", "match"),
    [
        (
            "## Личные данные\n\n- Имя: Иван\n- Фамилия: Иванов\n- Дата рождения: 31.02.2020",
            "Некорректная дата рождения",
        ),
        (
            "## Контакты\n\n- Email: not-an-email",
            "Некорректный email",
        ),
        (
            "## Контакты\n\n- Мобильный: 12345",
            "Некорректный телефон",
        ),
        (
            "## Контакты\n\n- Telegram: @ivan",
            "Неизвестный тип контакта",
        ),
        (
            "## Зарплата\n\n100000 BTC",
            "Неизвестная валюта",
        ),
        (
            "## Занятость\n\n- Космическая занятость",
            "Неизвестное значение для занятость",
        ),
        (
            "## Языки\n\n- Клингонский: C2",
            "Неизвестный язык",
        ),
        (
            "## Языки\n\n- Английский: Z9",
            "Неизвестный уровень языка",
        ),
    ],
)
def test_parser_rejects_malformed_or_unknown_values(field_text: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_resume_md(f"# Резюме\n\n{field_text}\n")


def test_company_id_is_kept_as_scalar_not_suggestion_object() -> None:
    payload = parse_resume_md(
        """
# Резюме

## Опыт работы

### Acme

- Должность: Developer
- Начало: 03.2021
- Компания ID: 123456
""".strip()
    )

    assert payload["experience"][0]["company_id"] == "123456"


def test_relocation_ignores_empty_items_after_trailing_comma() -> None:
    payload = parse_resume_md(
        """
# Резюме

## Переезд

- Тип: возможен
- Города: Москва, Санкт-Петербург,
""".strip()
    )

    assert [item["text"] for item in payload["relocation"]["area"]] == [
        "Москва",
        "Санкт-Петербург",
    ]


def test_invalid_experience_month_is_rejected() -> None:
    with pytest.raises(ValueError, match="Некорректная дата"):
        parse_resume_md(
            """
# Резюме

## Опыт работы

### Acme

- Должность: Developer
- Начало: 13.2021
""".strip()
        )
