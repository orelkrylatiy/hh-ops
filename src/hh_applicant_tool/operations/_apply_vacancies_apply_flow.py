from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any

import requests

from ..ai.base import AIError
from ..api import BadResponse, Redirect, datatypes
from ..api.errors import ApiError, CaptchaRequired, LimitExceeded
from ..storage.repositories.errors import RepositoryError
from ..utils.string import rand_text, unescape_string

logger = logging.getLogger(__package__)


@dataclass(frozen=True)
class VacancyResponseResult:
    """Outcome of one vacancy response attempt.

    ``accepted`` is also true in dry-run mode: the response was not sent, but it
    represents a response that would have been sent and therefore consumes the
    same per-run quota as a live response.
    """

    should_continue: bool
    accepted: bool


class ApplyVacanciesApplyFlowMixin:
    def _init_ai_filter_for_resume(self, resume: datatypes.Resume) -> None:
        if not self.ai_filter:
            return

        if self.ai_filter == "heavy":
            system_prompt = self._build_filter_system_prompt_heavy(
                self._analyze_resume_heavy(resume)
            )
        elif self.ai_filter == "light":
            system_prompt = self._build_filter_system_prompt_light(
                self._analyze_resume_light(resume)
            )
        else:
            raise ValueError(f"Неизвестный режим AI фильтра: {self.ai_filter}")

        logger.debug("AI системный промпт (%s): %s", self.ai_filter, system_prompt)
        self.vacancy_filter_ai = self.tool.get_vacancy_filter_ai(system_prompt)
        if self.args.ai_rate_limit:
            self.vacancy_filter_ai.rate_limit = self.args.ai_rate_limit

    def _save_vacancy_data(self, vacancy: dict[str, Any]) -> None:
        if self.dry_run:
            logger.debug(
                "dry-run: vacancy data was not persisted: %s",
                vacancy.get("alternate_url", vacancy.get("id")),
            )
            return

        storage = self.tool.storage
        try:
            storage.vacancies.save(vacancy)
        except RepositoryError as ex:
            logger.debug(ex)

        if vacancy.get("contacts"):
            logger.debug("Найдены контакты в вакансии: %s", vacancy["alternate_url"])
            try:
                storage.vacancy_contacts.save(vacancy)
            except RepositoryError as ex:
                logger.exception(ex)

    def _should_skip_vacancy_basic(
        self,
        vacancy: dict[str, Any],
        resume_id: str,
    ) -> bool:
        relations = vacancy.get("relations", [])
        if relations:
            logger.debug(
                "Пропускаем вакансию с откликом: %s",
                vacancy["alternate_url"],
            )
            if "got_rejection" in relations:
                logger.debug("Вы получили отказ от %s", vacancy["alternate_url"])
                print("⛔ Пришел отказ от", vacancy["alternate_url"])
            return True

        if vacancy.get("archived"):
            logger.debug("Пропускаем вакансию в архиве: %s", vacancy["alternate_url"])
            return True

        if vacancy.get("has_test") and self.args.skip_tests:
            logger.debug("Пропускаю вакансию с тестом %s", vacancy["alternate_url"])
            return True

        if redirect_url := vacancy.get("response_url"):
            logger.debug(
                "Пропускаем вакансию %s с перенаправлением: %s",
                vacancy["alternate_url"],
                redirect_url,
            )
            return True

        if self._is_excluded(vacancy):
            logger.info("Вакансия попала под фильтр: %s", vacancy["alternate_url"])
            if not self.dry_run:
                self._save_skipped_vacancy(vacancy, "excluded_filter", resume_id)
                self.api_client.put(f"/vacancies/blacklisted/{vacancy['id']}")
                logger.info(
                    "Вакансия добавлена в черный список: %s",
                    vacancy["alternate_url"],
                )
            else:
                logger.debug(
                    "dry-run: вакансия не добавлена в черный список: %s",
                    vacancy["alternate_url"],
                )
            return True
        return False

    def _should_skip_by_ai(
        self,
        vacancy: dict[str, Any],
        resume_id: str,
    ) -> bool:
        if not (self.ai_filter and self.vacancy_filter_ai):
            return False

        if self._is_vacancy_already_skipped(vacancy, resume_id):
            logger.debug("Вакансия уже была отклонена ранее: %s", vacancy["alternate_url"])
            print("⏩ Вакансия уже отклонена ранее", vacancy["alternate_url"])
            return True

        if self.ai_filter == "heavy":
            is_suitable = self._is_vacancy_suitable_heavy(vacancy)
        elif self.ai_filter == "light":
            is_suitable = self._is_vacancy_suitable_light(vacancy)
        else:
            raise ValueError(f"Неизвестный режим AI фильтра: {self.ai_filter}")

        if is_suitable:
            return False

        logger.info(
            "Вакансия отклонена AI фильтром (%s): %s",
            self.ai_filter,
            vacancy["alternate_url"],
        )
        print(
            f"🧠 AI ({self.ai_filter}) посчитал неподходящей",
            vacancy["alternate_url"],
        )
        if not self.dry_run:
            self._save_skipped_vacancy(vacancy, "ai_rejected", resume_id)
        return True

    def _load_employer_contacts(
        self,
        vacancy: dict[str, Any],
        seen_employers: set[str],
        site_emails: dict[str, list[str]],
    ) -> str | None:
        employer = vacancy.get("employer", {})
        employer_id = employer.get("id")
        if not employer_id or employer_id in seen_employers:
            return employer_id

        employer_profile: datatypes.Employer = self.api_client.get(
            f"/employers/{employer_id}"
        )
        if not self.dry_run:
            try:
                self.tool.storage.employers.save(employer_profile)
            except RepositoryError as ex:
                logger.exception(ex)

        if not self.dry_run and self.args.send_email and (
            site_url := (employer_profile.get("site_url") or "").strip()
        ):
            site_url = site_url if "://" in site_url else "https://" + site_url
            logger.debug("visit site: %s", site_url)
            try:
                site_info = self._parse_site(site_url)
                site_emails[employer_id] = site_info["emails"]
            except requests.RequestException as ex:
                site_info = None
                logger.error(ex)

            if site_info:
                logger.debug("site info: %r", site_info)
                try:
                    self.tool.storage.employer_sites.save(
                        {
                            "site_url": site_url,
                            "employer_id": employer_id,
                            "subdomains": [],
                            **site_info,
                        }
                    )
                except RepositoryError as ex:
                    logger.exception(ex)

        seen_employers.add(employer_id)
        return employer_id

    def _build_cover_letter(
        self,
        vacancy: dict[str, Any],
        resume: datatypes.Resume,
        message_placeholders: dict[str, str],
    ) -> str:
        if not (self.force_message or vacancy.get("response_letter_required")):
            return ""

        if self.cover_letter_ai:
            context = self._build_cover_letter_context(vacancy, resume)
            msg = self.message_prompt + "\n\n"
            msg += (
                "Напиши уникальное сопроводительное письмо под эту конкретную вакансию. "
                "Не ограничивайся повторением названия вакансии. Используй только факты из контекста, "
                "не выдумывай опыт и не используй placeholder'ы.\n\n"
            )
            msg += context
            logger.debug("prompt: %s", msg)
            letter = self.cover_letter_ai.complete(msg)
        else:
            letter = rand_text(self.cover_letter) % message_placeholders

        logger.debug(letter)
        return letter

    def _send_vacancy_response(
        self,
        vacancy: dict[str, Any],
        resume_id: str,
        letter: str,
    ) -> VacancyResponseResult:
        logger.debug("Пробуем откликнуться на вакансию: %s", vacancy["alternate_url"])

        if vacancy.get("has_test"):
            logger.debug("Решаем тест: %s", vacancy["alternate_url"])
            if self.dry_run:
                logger.info(
                    "dry-run: would send a response with a test: %s",
                    vacancy["alternate_url"],
                )
                return VacancyResponseResult(should_continue=True, accepted=True)

            try:
                result = self._solve_vacancy_test(
                    vacancy_id=vacancy["id"],
                    resume_hash=resume_id,
                    letter=letter,
                )
            except ValueError as ex:
                logger.warning(
                    "Не удалось обработать тест (%s), пробую обычный отклик: %s",
                    ex,
                    vacancy["alternate_url"],
                )
            except Exception as ex:
                logger.error("Произошла непредвиденная ошибка: %s", ex)
                return VacancyResponseResult(should_continue=True, accepted=False)
            else:
                if str(result.get("success")).lower() == "true":
                    print(
                        "📨 Отправили отклик на вакансию с тестом",
                        vacancy["alternate_url"],
                    )
                    return VacancyResponseResult(
                        should_continue=True,
                        accepted=True,
                    )

                err = result.get("error")
                if err == "negotiations-limit-exceeded":
                    logger.warning("Достигли лимита на отклики")
                    return VacancyResponseResult(
                        should_continue=False,
                        accepted=False,
                    )
                logger.error(
                    "Произошла ошибка при отклике на вакансию с тестом: %s - %s",
                    vacancy["alternate_url"],
                    err,
                )
                return VacancyResponseResult(
                    should_continue=True,
                    accepted=False,
                )

        params = {
            "resume_id": resume_id,
            "vacancy_id": vacancy["id"],
            "message": letter,
        }
        if self.dry_run:
            logger.info("dry-run: would send a response: %s", vacancy["alternate_url"])
            return VacancyResponseResult(should_continue=True, accepted=True)

        try:
            res = self.api_client.post(
                "/negotiations",
                params,
                delay=random.uniform(self.response_delay_min, self.response_delay_max),
            )
            if res != {}:
                logger.warning(
                    "Unexpected non-empty response while applying to %s: %r",
                    vacancy["alternate_url"],
                    res,
                )
            print("📨 Отправили отклик на вакансию", vacancy["alternate_url"])
            return VacancyResponseResult(should_continue=True, accepted=True)
        except Redirect:
            logger.warning(
                "Игнорирую перенаправление на форму: %s",
                vacancy["alternate_url"],
            )
            return VacancyResponseResult(should_continue=True, accepted=False)
        except CaptchaRequired as ex:
            logger.warning("Требуется капча: %s", ex.captcha_url)
            try:
                success = asyncio.run(self._solve_captcha_async(ex.captcha_url))
                if not success:
                    logger.error("Не удалось решить капчу")
                    raise RuntimeError("captcha failed")

                res = self.api_client.post(
                    "/negotiations",
                    params,
                    delay=random.uniform(self.response_delay_min, self.response_delay_max),
                )
                if res != {}:
                    logger.warning(
                        "Unexpected non-empty response while applying after captcha to %s: %r",
                        vacancy["alternate_url"],
                        res,
                    )
                print(
                    "📨 Отправили отклик на вакансию после капчи",
                    vacancy["alternate_url"],
                )
                return VacancyResponseResult(should_continue=True, accepted=True)
            except Exception as err:
                logger.error("Ошибка при решении капчи: %s", err)
                raise

    def _send_vacancy_email_if_needed(
        self,
        vacancy: dict[str, Any],
        employer_id: str | None,
        site_emails: dict[str, list[str]],
        message_placeholders: dict[str, str],
    ) -> None:
        if not self.args.send_email:
            return
        if self.dry_run:
            logger.info(
                "dry-run: email was not sent for vacancy: %s",
                vacancy["alternate_url"],
            )
            return

        mail_to: str | list[str] | None = (vacancy.get("contacts") or {}).get("email")
        if not mail_to and employer_id:
            mail_to = site_emails.get(employer_id)
        if not mail_to:
            return

        if isinstance(mail_to, list):
            mail_to_value = ", ".join(mail_to)
        else:
            mail_to_value = mail_to

        mail_subject = (
            rand_text(
                self.tool.config.get("apply_mail_subject")
                or "{Отклик|Резюме} на вакансию %(vacancy_name)s"
            )
            % message_placeholders
        )
        mail_body = (
            unescape_string(
                rand_text(
                    self.tool.config.get("apply_mail_body")
                    or "{Здравствуйте|Добрый день}, {прошу рассмотреть|пожалуйста рассмотрите} мое резюме %(resume_url)s на вакансию %(vacancy_name)s."
                )
            )
            % message_placeholders
        )
        try:
            self._send_email(mail_to_value, mail_subject, mail_body)
            print("📧 Отправлено письмо на email по поводу вакансии", vacancy["alternate_url"])
        except Exception as ex:
            logger.error("Ошибка отправки письма: %s", ex)

    def _apply_resume(
        self,
        resume: datatypes.Resume,
        user: datatypes.User,
        seen_employers: set[str],
    ) -> None:
        logger.info(
            "Начинаю рассылку откликов для резюме: %s (%s)",
            resume["alternate_url"],
            resume["title"],
        )
        print("🚀 Начинаю рассылку откликов для резюме:", resume["title"])

        if self._response_quota_reached():
            logger.info(
                "Reached application quota (%d); resume will not be processed",
                self.max_responses,
            )
            return

        placeholders = {
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
            "email": user.get("email") or "",
            "phone": user.get("phone") or "",
            "resume_hash": resume.get("id") or "",
            "resume_title": resume.get("title") or "",
            "resume_url": resume.get("alternate_url") or "",
        }
        do_apply = True
        site_emails: dict[str, list[str]] = {}

        self._init_ai_filter_for_resume(resume)

        for vacancy in self._get_vacancies(resume_id=resume["id"]):
            try:
                if self._response_quota_reached():
                    logger.info(
                        "Reached application quota (%d); stopping this run",
                        self.max_responses,
                    )
                    return

                employer = vacancy.get("employer", {})
                message_placeholders = {
                    "vacancy_name": vacancy.get("name", ""),
                    "employer_name": employer.get("name", ""),
                    **placeholders,
                }

                self._save_vacancy_data(vacancy)
                if not do_apply:
                    continue

                if self._should_skip_vacancy_basic(vacancy, resume["id"]):
                    continue
                if self._should_skip_by_ai(vacancy, resume["id"]):
                    continue

                employer_id = self._load_employer_contacts(
                    vacancy,
                    seen_employers,
                    site_emails,
                )
                letter = self._build_cover_letter(
                    vacancy,
                    resume,
                    message_placeholders,
                )
                result = self._send_vacancy_response(vacancy, resume["id"], letter)
                if not isinstance(result, VacancyResponseResult):
                    # Preserve the old private-method contract for custom
                    # integrations which return a plain boolean.
                    result = VacancyResponseResult(
                        should_continue=bool(result),
                        accepted=bool(result),
                    )
                do_apply = result.should_continue
                if result.accepted:
                    self.responses_sent += 1
                    self._send_vacancy_email_if_needed(
                        vacancy,
                        employer_id,
                        site_emails,
                        message_placeholders,
                    )
            except LimitExceeded:
                do_apply = False
                logger.warning("Достигли лимита на отклики")
            except ApiError as ex:
                logger.warning(ex)
            except AIError as ex:
                self.ai_error_count += 1
                logger.error(ex)
            except BadResponse as ex:
                logger.error(ex)

        logger.info(
            "Закончили рассылку откликов для резюме: %s (%s)",
            resume["alternate_url"],
            resume["title"],
        )
        print("✅️ Закончили рассылку откликов для резюме:", resume["title"])
