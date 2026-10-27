from __future__ import annotations

from hh_applicant_tool.automation.contact_collector import extract_contacts
from hh_applicant_tool.automation.reply_state import EmployerContactStore


def test_extract_contacts_finds_telegram_links_handles_and_phone() -> None:
    contacts = extract_contacts(
        "Напишите в Telegram https://t.me/Recruiter_Max или @recruiter_max, "
        "телефон +7 (912) 345-67-89. Почта hr@example.com"
    )

    assert [(item.contact_type, item.normalized_value) for item in contacts] == [
        ("telegram", "@recruiter_max"),
        ("phone", "+79123456789"),
    ]


def test_extract_contacts_rejects_short_handles_and_non_phone_numbers() -> None:
    assert extract_contacts("Версия @bot и номер заказа 12345") == []


def test_contact_store_deduplicates_same_message_contact(tmp_path) -> None:
    store = EmployerContactStore(tmp_path / "profile.sqlite")
    fields = {
        "profile_id": "5457",
        "chat_id": "chat-1",
        "message_id": "message-1",
        "message_created_at": "2026-10-26T10:00:00+0500",
        "vacancy_name": "Python developer",
        "employer_name": "Example",
        "contact_type": "telegram",
        "contact_value": "@Recruiter_Max",
        "normalized_value": "@recruiter_max",
        "message_text": "Пишите @Recruiter_Max",
    }

    assert store.save(**fields) is True
    assert store.save(**fields) is False
    assert store.recent() == [{**fields, "created_at": store.recent()[0]["created_at"]}]
