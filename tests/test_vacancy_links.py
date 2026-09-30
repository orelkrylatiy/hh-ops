"""Тесты извлечения ссылок из описаний вакансий и их хранения."""

from __future__ import annotations

import argparse
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hh_applicant_tool.operations.apply_safe import Operation
from hh_applicant_tool.storage.models.vacancy_link import VacancyLinkModel
from hh_applicant_tool.storage.repositories.errors import RepositoryError
from hh_applicant_tool.utils.description_links import (
    KIND_EMAIL,
    KIND_EXTERNAL,
    KIND_FORM,
    KIND_PHONE,
    KIND_TELEGRAM,
    extract_vacancy_links,
    vacancy_response_link,
)


def kinds_of(values: list[dict[str, str]]) -> list[str]:
    return [item["kind"] for item in values]


class TestExtractVacancyLinks:
    def test_google_form_href_is_form(self):
        html = '<p>Заполните анкету: <a href="https://forms.gle/abc123">форма</a></p>'
        links = extract_vacancy_links(html)
        assert {"kind": KIND_FORM, "value": "https://forms.gle/abc123"} in links

    def test_docs_google_form_href_is_form(self):
        html = '<a href="https://docs.google.com/forms/d/e/XYZ/viewform">анкета</a>'
        links = extract_vacancy_links(html)
        assert kinds_of(links) == [KIND_FORM]

    def test_telegram_href(self):
        html = '<a href="https://t.me/joinchat/AbCdEf">наш чат</a>'
        assert kinds_of(extract_vacancy_links(html)) == [KIND_TELEGRAM]

    def test_tg_handle_in_text(self):
        links = extract_vacancy_links("<p>Пишите: @hr_company</p>")
        assert {"kind": KIND_TELEGRAM, "value": "https://t.me/hr_company"} in links

    def test_email_in_text(self):
        links = extract_vacancy_links("<p>Резюме: jobs@company.ru</p>")
        assert {"kind": KIND_EMAIL, "value": "jobs@company.ru"} in links

    def test_mailto_href(self):
        html = '<a href="mailto:hr@company.ru?subject=Отклик">почта</a>'
        links = extract_vacancy_links(html)
        assert {"kind": KIND_EMAIL, "value": "hr@company.ru?subject=Отклик"} in links

    def test_tel_href(self):
        html = '<a href="tel:+79991234567">звоните</a>'
        links = extract_vacancy_links(html)
        assert {"kind": KIND_PHONE, "value": "+79991234567"} in links

    def test_phone_in_text(self):
        links = extract_vacancy_links("<p>Телефон HR: +7 (999) 123-45-67</p>")
        assert {"kind": KIND_PHONE, "value": "+7 (999) 123-45-67"} in links

    def test_bare_url_is_external(self):
        links = extract_vacancy_links("<p>Форум: forum.example.org/threads/123</p>")
        assert {
            "kind": KIND_EXTERNAL,
            "value": "https://forum.example.org/threads/123",
        } in links

    def test_plain_href_is_external(self):
        html = '<a href="https://example.org/careers">о нас</a>'
        assert kinds_of(extract_vacancy_links(html)) == [KIND_EXTERNAL]

    def test_hh_links_are_skipped(self):
        html = (
            '<a href="https://hh.ru/vacancy/123">вакансия</a>'
            '<a href="https://employer.hh.ru">работодатель</a>'
        )
        assert extract_vacancy_links(html) == []

    def test_html_entities_in_href_are_unescaped(self):
        html = '<a href="https://docs.google.com/forms?x=1&amp;y=2">форма</a>'
        links = extract_vacancy_links(html)
        assert links[0]["value"] == "https://docs.google.com/forms?x=1&y=2"

    def test_duplicates_are_removed(self):
        html = (
            '<a href="https://t.me/hr_chat">чат</a> '
            "<p>чат: t.me/hr_chat, почта jobs@company.ru, jobs@company.ru</p>"
        )
        links = extract_vacancy_links(html)
        telegram = [i for i in links if i["kind"] == KIND_TELEGRAM]
        emails = [i for i in links if i["kind"] == KIND_EMAIL]
        assert len(telegram) == 1
        assert len(emails) == 1

    def test_email_is_not_parsed_as_tg_handle(self):
        links = extract_vacancy_links("<p>jobs@company.ru</p>")
        assert kinds_of(links) == [KIND_EMAIL]

    def test_empty_description(self):
        assert extract_vacancy_links("") == []
        assert extract_vacancy_links(None) == []


class TestHhRedirectUnwrap:
    def test_query_param_target_is_unwrapped(self):
        html = '<a href="https://hh.ru/redirect?url=https%3A%2F%2Fforms.gle%2Fxyz">форма</a>'
        assert kinds_of(extract_vacancy_links(html)) == [KIND_FORM]

    def test_opaque_redirect_is_dropped(self):
        html = '<a href="https://hh.ru/redirect/click?data=eJyTjQEAAtAAvQ==">клик</a>'
        assert extract_vacancy_links(html) == []

    def test_plain_links_are_untouched(self):
        from hh_applicant_tool.utils.description_links import unwrap_hh_redirect

        assert unwrap_hh_redirect("https://example.org/a") == "https://example.org/a"


class TestVacancyResponseLink:
    def test_response_url_wins(self):
        vacancy = {
            "response_url": "https://forms.example.com/apply",
            "adv_response_url": "https://other.example.com",
        }
        assert vacancy_response_link(vacancy) == "https://forms.example.com/apply"

    def test_adv_response_url_fallback(self):
        assert (
            vacancy_response_link({"adv_response_url": "https://x.example.com"})
            == "https://x.example.com"
        )

    def test_none_when_absent(self):
        assert vacancy_response_link({}) is None


class TestVacancyLinksStorage:
    def test_save_batch_and_find(self, storage):
        storage.vacancy_links.save_batch(
            [
                {
                    "vacancy_id": 1,
                    "kind": KIND_FORM,
                    "value": "https://f.gle/x",
                    "source": "response_url",
                },
                {"vacancy_id": 1, "kind": KIND_EMAIL, "value": "hr@x.ru", "source": "description"},
            ]
        )
        rows = list(storage.vacancy_links.find(vacancy_id=1))
        assert len(rows) == 2
        assert isinstance(rows[0], VacancyLinkModel)
        assert {row.value for row in rows} == {"https://f.gle/x", "hr@x.ru"}

    def test_upsert_does_not_duplicate(self, storage):
        item = {"vacancy_id": 2, "kind": KIND_FORM, "value": "https://f.gle/y"}
        storage.vacancy_links.save_batch([item])
        storage.vacancy_links.save_batch([item])
        assert len(list(storage.vacancy_links.find(vacancy_id=2))) == 1

    def test_table_created_for_existing_db(self, db_conn):
        # База старого профиля без vacancy_links должна обновляться на init
        from hh_applicant_tool.storage.utils import init_db

        init_db(db_conn)
        db_conn.execute(
            "INSERT INTO vacancy_links (vacancy_id, kind, value) VALUES (3, 'form', 'https://f.gle/z')"
        )
        row = db_conn.execute(
            "SELECT kind, value FROM vacancy_links WHERE vacancy_id = 3"
        ).fetchone()
        assert tuple(row) == ("form", "https://f.gle/z")


class TestSaveVacancyLinksHook:
    @staticmethod
    def _operation(dry_run: bool = False) -> tuple[Operation, Mock]:
        operation = Operation()
        operation.dry_run = dry_run
        save_batch = Mock()
        operation.tool = SimpleNamespace(
            storage=SimpleNamespace(vacancy_links=SimpleNamespace(save_batch=save_batch)),
        )
        return operation, save_batch

    def test_saves_response_url_and_description_links(self):
        operation, save_batch = self._operation()
        vacancy = {
            "id": 123,
            "response_url": "https://forms.example.com/apply",
            "description": (
                "<p>Анкета: <a href='https://docs.google.com/forms/d/e/X/viewform'>форма</a>. "
                "Вопросы: @hr_company или jobs@company.ru, +7 (999) 123-45-67. "
                "<a href='https://hh.ru/vacancy/123'>hh</a></p>"
            ),
        }

        operation._save_vacancy_links(vacancy)

        save_batch.assert_called_once()
        items = save_batch.call_args[0][0]
        assert {item["kind"] for item in items} == {
            "form",
            "telegram",
            "email",
            "phone",
        }
        assert all(item["vacancy_id"] == 123 for item in items)

    def test_skipped_in_dry_run(self):
        operation, save_batch = self._operation(dry_run=True)

        operation._save_vacancy_links({"id": 1, "response_url": "https://forms.example.com"})

        save_batch.assert_not_called()

    def test_no_links_no_save(self):
        operation, save_batch = self._operation()

        operation._save_vacancy_links({"id": 1, "description": "<p>Просто текст</p>"})

        save_batch.assert_not_called()

    def test_storage_error_does_not_raise(self):
        operation, save_batch = self._operation()
        save_batch.side_effect = RepositoryError("Database error in save_batch: locked")

        # Ошибка сохранения не должна ломать отклики
        operation._save_vacancy_links({"id": 1, "response_url": "https://x.example.com"})


@pytest.mark.parametrize(
    ("host", "expected_kind"),
    [
        ("forms.gle", KIND_FORM),
        ("typeform.com", KIND_FORM),
        ("t.me", KIND_TELEGRAM),
        ("example.org", KIND_EXTERNAL),
        ("hh.ru", None),
        ("career.hh.ru", None),
    ],
)
def test_classify_url_by_host(host: str, expected_kind: str | None):
    from hh_applicant_tool.utils.description_links import classify_url

    assert classify_url(f"https://{host}/path") == expected_kind


class TestVacancyLinksCliOperation:
    @staticmethod
    def _args(*extra: str) -> argparse.Namespace:
        from hh_applicant_tool.operations.vacancy_links import Operation as LinksOperation

        operation = LinksOperation()
        parser = argparse.ArgumentParser()
        operation.setup_parser(parser)
        namespace = parser.parse_args(list(extra))
        # --json добавляет глобальный парсер CLI, локально его нет
        namespace.json = False
        return namespace

    def test_run_creates_table_on_bare_db(self, capsys):
        # База старого профиля без vacancy_links не должна ронять команду
        from hh_applicant_tool.operations.vacancy_links import Operation as LinksOperation

        conn = sqlite3.connect(":memory:")
        LinksOperation().run(SimpleNamespace(db=conn), self._args())

        out = capsys.readouterr().out
        assert "No manual follow-up links stored." in out
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vacancy_links'"
        ).fetchone()
        assert table is not None

    def test_run_json_output(self, storage, capsys):
        from hh_applicant_tool.operations.vacancy_links import Operation as LinksOperation

        storage.vacancy_links.save_batch(
            [{"vacancy_id": 7, "kind": KIND_FORM, "value": "https://f.gle/json"}]
        )
        args = self._args()
        args.json = True
        LinksOperation().run(SimpleNamespace(db=storage.vacancy_links.conn), args)

        payload = json.loads(capsys.readouterr().out)
        assert payload == [
            {
                "vacancy_id": 7,
                "kind": "form",
                "value": "https://f.gle/json",
                "source": None,
                "created_at": payload[0]["created_at"],
                "vacancy_name": None,
                "alternate_url": None,
                "employer_name": None,
            }
        ]
