from __future__ import annotations

import html
import logging
import random
import re
import time
from datetime import datetime
from email.message import EmailMessage
from itertools import chain
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

from ..api.datatypes import PaginatedItems, SearchVacancy
from ..utils.datatypes import VacancyTestsData
from ..utils.find import find_key
from ..utils.json import JSONDecoder
from ..utils.string import bool2str, rand_text, strip_tags

logger = logging.getLogger(__package__)


class ApplyVacanciesHelpersMixin:
    json_decoder = JSONDecoder()

    def _send_email(self, to: str, subject: str, body: str) -> None:
        if getattr(self, "dry_run", False):
            logger.info("dry-run: SMTP message was not sent to %s", to)
            return

        cfg = self.tool.config.get("smtp", {})
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.get("from") or cfg.get("user")
        msg["To"] = to
        msg.set_content(body)
        self.tool.smtp.send_message(msg)

    def _get_vacancy_tests(
        self,
        response_url: str,
    ) -> VacancyTestsData | None:
        """Read vacancy test data from HH's current embedded initial state."""
        config = self.tool.get_redirect_config(response_url)
        tests = find_key(config, "vacancyTests")
        return tests if isinstance(tests, dict) else None

    def _solve_vacancy_test(
        self,
        vacancy_id: str | int,
        resume_hash: str,
        letter: str = "",
    ) -> dict[str, Any]:
        response_url = (
            "https://hh.ru/applicant/vacancy_response?"
            f"vacancyId={vacancy_id}&startedWithQuestion=false&hhtmFrom=vacancy"
        )

        tests_data = self._get_vacancy_tests(response_url)

        try:
            test_data = tests_data[str(vacancy_id)]
        except KeyError as ex:
            raise ValueError("Отсутствуют данные теста для вакансии.") from ex

        logger.debug("%s", {"test_data": test_data})

        payload: dict[str, Any] = {
            "_xsrf": self.tool.xsrf_token,
            "uidPk": test_data["uidPk"],
            "guid": test_data["guid"],
            "startTime": test_data["startTime"],
            "testRequired": test_data["required"],
            "vacancy_id": vacancy_id,
            "resume_hash": resume_hash,
            "ignore_postponed": "true",
            "incomplete": "false",
            "mark_applicant_visible_in_vacancy_country": "false",
            "country_ids": "[]",
            "lux": "true",
            "withoutTest": "no",
            "letter": letter,
        }

        for task in test_data["tasks"]:
            field_name = f"task_{task['id']}"
            solutions = task.get("candidateSolutions") or []
            question = (task.get("description") or "").strip()

            if solutions:
                if self.cover_letter_ai:
                    options = "\n".join(
                        f"{s['id']}: {strip_tags(s['text'])}" for s in solutions
                    )
                    prompt = (
                        f"Вопрос: {question}\n"
                        f"Варианты:\n{options}\n"
                        f"Выбери ID правильного ответа. Пришли только ID."
                    )
                    ai_answer = self.cover_letter_ai.complete(prompt).strip()
                    match = re.search(r"\d+", ai_answer)
                    selected_id = (
                        match.group(0) if match else solutions[0]["id"]
                    )
                    payload[field_name] = selected_id
                else:
                    yes_solution = next(
                        filter(lambda x: x["text"].lower() == "да", solutions),
                        None,
                    )
                    payload[field_name] = (
                        yes_solution["id"]
                        if yes_solution
                        else solutions[len(solutions) // 2]["id"]
                    )
            else:
                if "://" in question:
                    answer = rand_text(
                        "{{Простите|Извините}, но я не перехожу по {внешним|сторонним} ссылкам, так как {опасаюсь взлома|не хочу {быть взломанным|подхватить вирус|чтобы у меня {со|с банковского} счета украли деньги}}.|У меня нет времени на заполнение анкет и гуглодоков}"
                    )
                elif self.cover_letter_ai:
                    prompt = (
                        "Дай краткий и профессиональный ответ на вопрос: "
                        f"{question}"
                    )
                    answer = self.cover_letter_ai.complete(prompt)
                else:
                    answer = "Да"

                payload[f"{field_name}_text"] = answer

        logger.debug("%s", {"payload": payload})

        time.sleep(random.uniform(self.response_delay_min, self.response_delay_max))

        response = self.tool.session.post(
            "https://hh.ru/applicant/vacancy_response/popup",
            data=payload,
            headers={
                "Referer": response_url,
                "X-Hhtmfrom": "vacancy",
                "X-Hhtmsource": "vacancy_response",
                "X-Requested-With": "XMLHttpRequest",
                "X-Xsrftoken": self.tool.xsrf_token,
            },
        )

        logger.debug(
            "%s %s %d",
            response.request.method,
            response.url,
            response.status_code,
        )

        return response.json()

    def _parse_site(self, url: str) -> dict[str, Any]:
        with self.tool.session.get(url, timeout=10) as r:
            val = lambda m: html.unescape(m.group(1)) if m else ""

            title = val(re.search(r"<title>(.*?)</title>", r.text, re.I | re.S))
            description = val(
                re.search(
                    r'<meta name="description" content="(.*?)"',
                    r.text,
                    re.I,
                )
            )
            generator = val(
                re.search(
                    r'<meta name="generator" content="(.*?)"',
                    r.text,
                    re.I,
                )
            )

            emails = set(
                m.group(0)
                for m in re.finditer(
                    r"\b[a-z][a-z0-9_.-]+@([a-z0-9][a-z0-9-]+)(?!\.(?:png|jpe?g|bmp|gif|ico|js|css)\b)(\.[a-z0-9][a-z0-9-]+)+\b",
                    r.text,
                )
            )

            ip_address = None
            if getattr(r.raw, "_connection", None):
                sock = getattr(r.raw._connection, "sock", None)
                if sock:
                    ip_address = sock.getpeername()[0]

            return {
                "title": title,
                "description": description,
                "generator": generator,
                "emails": list(emails),
                "server_name": r.headers.get("Server"),
                "powered_by": r.headers.get("X-Powered-By"),
                "ip_address": ip_address,
            }

    def _get_subdomains(self, url: str) -> set[str]:
        domain = urlparse(url).netloc
        r = self.tool.session.get(
            "https://crt.sh",
            params={"q": domain, "output": "json"},
            timeout=30,
        )

        r.raise_for_status()

        return set(
            item
            for item in chain.from_iterable(
                item["name_value"].split() for item in r.json()
            )
            if not item.startswith("*.")
        )

    def _get_search_params(self, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "page": page,
            "per_page": self.per_page,
        }
        if self.order_by:
            params |= {"order_by": self.order_by}
        if self.search:
            params["text"] = self.search
        if self.schedule:
            params["schedule"] = self.schedule
        if self.work_format:
            params["work_format"] = list(self.work_format)
        if self.experience:
            params["experience"] = self.experience
        if self.currency:
            params["currency"] = self.currency
        if self.salary:
            params["salary"] = self.salary
        if self.period:
            params["period"] = self.period
        if self.date_from:
            params["date_from"] = self.date_from
        if self.date_to:
            params["date_to"] = self.date_to
        if self.top_lat:
            params["top_lat"] = self.top_lat
        if self.bottom_lat:
            params["bottom_lat"] = self.bottom_lat
        if self.left_lng:
            params["left_lng"] = self.left_lng
        if self.right_lng:
            params["right_lng"] = self.right_lng
        if self.sort_point_lat:
            params["sort_point_lat"] = self.sort_point_lat
        if self.sort_point_lng:
            params["sort_point_lng"] = self.sort_point_lng
        if self.search_field:
            params["search_field"] = list(self.search_field)
        if self.employment:
            params["employment"] = list(self.employment)
        if self.area:
            params["area"] = list(self.area)
        if self.metro:
            params["metro"] = list(self.metro)
        if self.professional_role:
            params["professional_role"] = list(self.professional_role)
        if self.industry:
            params["industry"] = list(self.industry)
        if self.employer_id:
            params["employer_id"] = list(self.employer_id)
        if self.excluded_employer_id:
            params["excluded_employer_id"] = list(self.excluded_employer_id)
        if self.label:
            params["label"] = list(self.label)
        if self.only_with_salary:
            params["only_with_salary"] = bool2str(self.only_with_salary)
        if self.no_magic:
            params["no_magic"] = bool2str(self.no_magic)
        if self.premium:
            params["premium"] = bool2str(self.premium)
        return params

    def _get_vacancies(
        self,
        resume_id: str | None = None,
    ) -> Iterator[SearchVacancy]:
        for page in range(self.total_pages):
            logger.debug("Загружаем вакансии со страницы: %d", page + 1)
            params = self._get_search_params(page)

            if self.search:
                res: PaginatedItems[SearchVacancy] = self.api_client.get(
                    "/vacancies",
                    params,
                )
            else:
                res = self.api_client.get(
                    f"/resumes/{resume_id}/similar_vacancies",
                    params,
                )

            logger.debug("Количество вакансий: %s", res["found"])

            if not res["items"]:
                return

            yield from res["items"]

            if page >= res["pages"] - 1:
                return

    def _is_excluded(self, vacancy: SearchVacancy) -> bool:
        if not self.excluded_filter:
            return False

        snippet = vacancy.get("snippet", {})
        vacancy_summary = " ".join(
            filter(
                None,
                [
                    vacancy.get("name"),
                    snippet.get("requirement"),
                    snippet.get("responsibility"),
                ],
            )
        )

        logger.debug(vacancy_summary)

        excluded_pat: re.Pattern[str] = re.compile(
            self.excluded_filter,
            re.IGNORECASE,
        )
        if excluded_pat.search(vacancy_summary):
            return True

        r = self.tool.session.get("https://hh.ru/vacancy/" + vacancy["id"])
        r.raise_for_status()

        match = re.search(r'"description":\s*(.*)', r.text)
        if not match:
            return False
        description, _ = self.json_decoder.raw_decode(match.group(1))
        description = strip_tags(description)
        logger.debug(description[:2047])
        return bool(excluded_pat.search(description))

    def _is_vacancy_already_skipped(
        self,
        vacancy: SearchVacancy,
        resume_id: str | None = None,
    ) -> bool:
        try:
            vacancy_id = vacancy["id"]

            if resume_id and any(
                self.tool.storage.skipped_vacancies.find(
                    resume_id=resume_id,
                    vacancy_id=vacancy_id,
                )
            ):
                return True

            return any(
                self.tool.storage.skipped_vacancies.find(
                    resume_id="",
                    vacancy_id=vacancy_id,
                )
            )
        except Exception:
            return False

    def _save_skipped_vacancy(
        self,
        vacancy: SearchVacancy,
        reason: str,
        resume_id: str | None = None,
    ) -> None:
        try:
            employer = vacancy.get("employer", {})
            self.tool.storage.skipped_vacancies.save(
                {
                    "resume_id": resume_id or "",
                    "vacancy_id": vacancy["id"],
                    "reason": reason,
                    "alternate_url": vacancy.get("alternate_url"),
                    "name": vacancy.get("name"),
                    "employer_name": employer.get("name"),
                    "created_at": datetime.now(),
                }
            )
        except Exception as ex:
            logger.warning("Не удалось сохранить пропущенную вакансию: %s", ex)
