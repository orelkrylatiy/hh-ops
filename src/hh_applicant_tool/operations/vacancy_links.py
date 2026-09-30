from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from typing import TYPE_CHECKING

from ..main import BaseNamespace, BaseOperation
from ..storage.utils import init_db

if TYPE_CHECKING:
    from ..main import HHApplicantTool

LINK_KINDS = ("form", "telegram", "email", "phone", "external")


class Namespace(BaseNamespace):
    limit: int
    kind: str | None
    csv_output: bool


class Operation(BaseOperation):
    """Ссылки и контакты из вакансий для ручного прохода: внешние анкеты,
    телеграм, почта и телефоны из описаний, куда автопилот не мог ответить."""

    __aliases__ = ["links"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument(
            "--kind",
            choices=LINK_KINDS,
            help="Фильтр по типу: form, telegram, email, phone, external",
        )
        parser.add_argument(
            "--csv",
            dest="csv_output",
            action="store_true",
            help="Вывести плоский CSV вместо сгруппированного текста",
        )

    def _fetch(self, tool: HHApplicantTool, args: Namespace) -> list[dict]:
        tool.db.row_factory = sqlite3.Row
        conditions = []
        params: list = []
        if args.kind:
            conditions.append("l.kind = ?")
            params.append(args.kind)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = tool.db.execute(
            f"""
            SELECT l.vacancy_id, l.kind, l.value, l.source, l.created_at,
                   v.name AS vacancy_name, v.alternate_url,
                   e.name AS employer_name
            FROM vacancy_links l
            LEFT JOIN vacancies v ON v.id = l.vacancy_id
            LEFT JOIN employers e ON e.id = v.employer_id
            {where}
            ORDER BY l.vacancy_id DESC, l.kind, l.value
            LIMIT ?
            """,
            (*params, max(args.limit, 1)),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _print_grouped(rows: list[dict]) -> None:
        if not rows:
            print("No manual follow-up links stored.")
            return

        by_vacancy: dict[int, list[dict]] = {}
        for row in rows:
            by_vacancy.setdefault(row["vacancy_id"], []).append(row)

        for vacancy_id, links in by_vacancy.items():
            first = links[0]
            name = first["vacancy_name"] or "неизвестная вакансия"
            employer = first["employer_name"] or "неизвестный работодатель"
            print(f"[{vacancy_id}] {name} — {employer}")
            if url := first["alternate_url"]:
                print(f"  {url}")
            for link in links:
                source = f" ({link['source']})" if link["source"] else ""
                print(f"  {link['kind']}: {link['value']}{source}")
            print()

    @staticmethod
    def _print_csv(rows: list[dict]) -> None:
        writer = csv.writer(sys.stdout)
        writer.writerow(
            (
                "vacancy_id",
                "vacancy_name",
                "employer_name",
                "alternate_url",
                "kind",
                "value",
                "source",
            )
        )
        for row in rows:
            writer.writerow(
                (
                    row["vacancy_id"],
                    row["vacancy_name"],
                    row["employer_name"],
                    row["alternate_url"],
                    row["kind"],
                    row["value"],
                    row["source"],
                )
            )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        # Схема идемпотентна: на старой базе без vacancy_links создаст таблицу
        init_db(tool.db)
        rows = self._fetch(tool, args)

        if getattr(args, "json", False):
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return

        if args.csv_output:
            self._print_csv(rows)
            return

        self._print_grouped(rows)
