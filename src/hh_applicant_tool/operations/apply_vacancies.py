from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..api import datatypes
from ..main import BaseNamespace, BaseOperation
from ..storage.repositories.errors import RepositoryError
from ..utils.misc import expand_env_placeholders, load_prompt
from ..utils.resume_aliases import resolve_resume_alias
from ._apply_vacancies_ai import ApplyVacanciesAIMixin
from ._apply_vacancies_apply_flow import ApplyVacanciesApplyFlowMixin
from ._apply_vacancies_helpers import ApplyVacanciesHelpersMixin

if TYPE_CHECKING:
    from ..main import HHApplicantTool


logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    resume_id: str | None
    resume_alias: str | None
    letter_file: Path | None
    ignore_employers: Path | None
    force_message: bool
    use_ai: bool
    ai_filter: Literal["heavy", "light"] | None
    ai_rate_limit: int
    system_prompt: str
    message_prompt: str
    response_delay: str
    order_by: str
    search: str
    search_field: list[str] | None
    schedule: str
    work_format: list[str] | None
    dry_run: bool
    response_delay_min: float
    response_delay_max: float
    # Пошли доп фильтры, которых не было
    experience: str
    employment: list[str] | None
    area: list[str] | None
    metro: list[str] | None
    professional_role: list[str] | None
    industry: list[str] | None
    employer_id: list[str] | None
    excluded_employer_id: list[str] | None
    currency: str | None
    salary: int | None
    only_with_salary: bool
    label: list[str] | None
    period: int | None
    date_from: str | None
    date_to: str | None
    top_lat: float | None
    bottom_lat: float | None
    left_lng: float | None
    right_lng: float | None
    sort_point_lat: float | None
    sort_point_lng: float | None
    no_magic: bool
    premium: bool
    per_page: int
    total_pages: int
    excluded_filter: str | None
    max_responses: int | None
    send_email: bool
    skip_tests: bool


class Operation(
    ApplyVacanciesApplyFlowMixin,
    ApplyVacanciesAIMixin,
    ApplyVacanciesHelpersMixin,
    BaseOperation,
):
    """Откликнуться на все подходящие вакансии."""

    __aliases__ = ("apply", "apply-similar")
    _ARG_ATTRS = (
        "area",
        "bottom_lat",
        "currency",
        "date_from",
        "date_to",
        "dry_run",
        "employer_id",
        "employment",
        "excluded_employer_id",
        "excluded_filter",
        "experience",
        "force_message",
        "industry",
        "label",
        "left_lng",
        "max_responses",
        "metro",
        "no_magic",
        "only_with_salary",
        "order_by",
        "per_page",
        "period",
        "message_prompt",
        "premium",
        "professional_role",
        "resume_alias",
        "resume_id",
        "right_lng",
        "salary",
        "schedule",
        "work_format",
        "search",
        "search_field",
        "sort_point_lat",
        "sort_point_lng",
        "top_lat",
        "total_pages",
    )

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        resume_group = parser.add_mutually_exclusive_group()
        resume_group.add_argument("--resume-id", help="Идентификатор резюме")
        resume_group.add_argument(
            "--resume-alias",
            help="Профильный alias резюме из config.resume_aliases",
        )
        parser.add_argument(
            "--search",
            help="Строка поиска для фильтрации вакансий. Если указана, то поиск будет производиться по вакансиям. В остальных случаях отклики будут производиться по списку рекомендованных вакансий.",  # noqa: E501
            type=str,
        )
        parser.add_argument(
            "-L",
            "--letter-file",
            "--letter",
            help="Путь до файла с текстом сопроводительного письма.",
            type=Path,
        )
        parser.add_argument(
            "-f",
            "--force-message",
            "--force",
            help="Всегда отправлять сообщение при отклике",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--use-ai",
            "--ai",
            help="Использовать AI для генерации сообщений",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--ai-filter",
            help="Использовать AI для фильтрации вакансий. Режимы: heavy - полный анализ вакансии и резюме, light - быстрый анализ по названию и навыкам",
            choices=["heavy", "light"],
            default=None,
        )
        parser.add_argument(
            "--ai-rate-limit",
            help="Лимит запросов к AI в минуту для фильтрации",
            type=int,
            default=40,
        )
        parser.add_argument(
            "--system-prompt",
            "--ai-system",
            help="Системный промпт для AI генерации сопроводительных писем",
            default="Напиши сопроводительное письмо для отклика на эту вакансию. Не используй placeholder'ы, твой ответ будет отправлен без обработки.",  # noqa: E501
        )
        parser.add_argument(
            "--message-prompt",
            "--prompt",
            help="Промпт для генерации сопроводительного письма",
            default="Сгенерируй сопроводительное письмо не более 5-7 предложений от моего имени для вакансии",  # noqa: E501
        )
        parser.add_argument(
            "--total-pages",
            "--pages",
            help="Количество обрабатываемых страниц поиска",  # noqa: E501
            default=20,
            type=int,
        )
        parser.add_argument(
            "--per-page",
            help="Сколько должно быть результатов на странице",  # noqa: E501
            default=100,
            type=int,
        )
        parser.add_argument(
            "--send-email",
            help="Отправлять письмо на email компании или рекрутера с просьбой рассмотреть резюме",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--skip-tests",
            help="Пропускать тесты при откликах вместо",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--excluded-filter",
            type=str,
            help=r"Исключить вакансии, если название или описание не соответствует шаблону. Например, `--excluded-filter 'junior|стажир|bitrix|дружн\w+ коллектив|полиграф|open\s*space|опенспейс|хакатон|конкурс|тестов\w+ задан'`",
        )
        parser.add_argument(
            "--max-responses",
            type=self._non_negative_int,
            help="Максимальное число откликов за один запуск (0 — не отправлять)",
        )
        parser.add_argument(
            "--dry-run",
            help="Не отправлять отклики, а только выводить информацию",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--response-delay",
            "--delay",
            help="Задержка между откликами в секундах (минимум-максимум, напр. '1-3'). По умолчанию 1-3 сек",
            type=str,
            default="1-3",
        )

        # Дальше идут параметры в точности соответствующие параметрам запроса
        # при поиске подходящих вакансий
        api_search_filters = parser.add_argument_group(
            "Фильтры для поиска вакансий",
            "Эти параметры напрямую соответствуют фильтрам поиска HeadHunter API",
        )

        api_search_filters.add_argument(
            "--order-by",
            help="Сортировка вакансий",
            choices=[
                "publication_time",
                "salary_desc",
                "salary_asc",
                "relevance",
                "distance",
            ],
            # default="relevance",
        )
        api_search_filters.add_argument(
            "--experience",
            help="Уровень опыта работы (noExperience, between1And3, between3And6, moreThan6)",
            type=str,
            default=None,
        )
        api_search_filters.add_argument(
            "--schedule",
            help="Тип графика (fullDay, shift, flexible, remote, flyInFlyOut)",
            type=str,
        )
        api_search_filters.add_argument(
            "--work-format",
            nargs="+",
            help="Формат работы (REMOTE, HYBRID, ON_SITE, FIELD_WORK)",
        )
        api_search_filters.add_argument(
            "--employment", nargs="+", help="Тип занятости"
        )
        api_search_filters.add_argument(
            "--area", nargs="+", help="Регион (area id)"
        )
        api_search_filters.add_argument(
            "--metro", nargs="+", help="Станции метро (metro id)"
        )
        api_search_filters.add_argument(
            "--professional-role", nargs="+", help="Проф. роль (id)"
        )
        api_search_filters.add_argument(
            "--industry", nargs="+", help="Индустрия (industry id)"
        )
        api_search_filters.add_argument(
            "--employer-id", nargs="+", help="ID работодателей"
        )
        api_search_filters.add_argument(
            "--excluded-employer-id", nargs="+", help="Исключить работодателей"
        )
        api_search_filters.add_argument(
            "--currency", help="Код валюты (RUR, USD, EUR)"
        )
        api_search_filters.add_argument(
            "--salary", type=int, help="Минимальная зарплата"
        )
        api_search_filters.add_argument(
            "--only-with-salary",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        api_search_filters.add_argument(
            "--label", nargs="+", help="Метки вакансий (label)"
        )
        api_search_filters.add_argument(
            "--period", type=int, help="Искать вакансии за N дней"
        )
        api_search_filters.add_argument(
            "--date-from", help="Дата публикации с (YYYY-MM-DD)"
        )
        api_search_filters.add_argument(
            "--date-to", help="Дата публикации по (YYYY-MM-DD)"
        )
        api_search_filters.add_argument(
            "--top-lat", type=float, help="Гео: верхняя широта"
        )
        api_search_filters.add_argument(
            "--bottom-lat", type=float, help="Гео: нижняя широта"
        )
        api_search_filters.add_argument(
            "--left-lng", type=float, help="Гео: левая долгота"
        )
        api_search_filters.add_argument(
            "--right-lng", type=float, help="Гео: правая долгота"
        )
        api_search_filters.add_argument(
            "--sort-point-lat",
            type=float,
            help="Координата lat для сортировки по расстоянию",
        )
        api_search_filters.add_argument(
            "--sort-point-lng",
            type=float,
            help="Координата lng для сортировки по расстоянию",
        )
        api_search_filters.add_argument(
            "--no-magic",
            action="store_true",
            help="Отключить авторазбор текста запроса",
        )
        api_search_filters.add_argument(
            "--premium",
            default=False,
            action=argparse.BooleanOptionalAction,
            help="Только премиум вакансии",
        )
        api_search_filters.add_argument(
            "--search-field",
            nargs="+",
            help="Поля поиска (name, company_name и т.п.)",
        )

    cover_letter: str = "{Здравствуйте|Добрый день}, меня зовут %(first_name)s. {Прошу|Предлагаю} рассмотреть {мою кандидатуру|мое резюме «%(resume_title)s»} на вакансию «%(vacancy_name)s». С уважением, %(first_name)s."

    @property
    def api_client(self):
        return self.tool.api_client

    @property
    def args(self) -> Namespace:
        return self._args

    @staticmethod
    def _parse_response_delay(response_delay: str) -> tuple[float, float]:
        delay_parts = response_delay.split("-")
        try:
            delay_min = float(delay_parts[0])
            delay_max = (
                float(delay_parts[1])
                if len(delay_parts) > 1
                else delay_min
            )
        except (ValueError, IndexError):
            logger.warning(
                "Неверный формат задержки %r, используем по умолчанию 1-3 сек",
                response_delay,
            )
            return 1.0, 3.0

        return delay_min, delay_max

    @staticmethod
    def _non_negative_int(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as ex:
            raise argparse.ArgumentTypeError(
                "must be a non-negative integer"
            ) from ex
        if parsed < 0:
            raise argparse.ArgumentTypeError(
                "must be a non-negative integer"
            )
        return parsed

    def _response_quota_reached(self) -> bool:
        return (
            self.max_responses is not None
            and self.responses_sent >= self.max_responses
        )

    def _assign_args(self, args: Namespace) -> None:
        for attr_name in self._ARG_ATTRS:
            setattr(self, attr_name, getattr(args, attr_name, None))

    def run(
        self,
        tool: HHApplicantTool,
        args: Namespace,
    ) -> int | None:
        self.tool = tool
        self._args = args
        # Промпты можно задать как инлайн-строкой, так и путём к файлу (@file или путь)
        args.system_prompt = load_prompt(args.system_prompt)
        args.message_prompt = load_prompt(args.message_prompt)
        self.cover_letter = (
            expand_env_placeholders(
                args.letter_file.read_text(encoding="utf-8", errors="ignore")
            )
            if args.letter_file
            else self.cover_letter
        )
        self._assign_args(args)
        if self.resume_alias:
            self.resume_id = resolve_resume_alias(tool.config, self.resume_alias)
            logger.info(
                "Resolved resume alias %s -> %s",
                self.resume_alias,
                self.resume_id,
            )
        if self.max_responses is not None and self.max_responses < 0:
            raise ValueError("max_responses must be a non-negative integer")
        self.responses_sent = 0
        (
            self.response_delay_min,
            self.response_delay_max,
        ) = self._parse_response_delay(args.response_delay)

        logger.info(
            "Задежка между откликами: %.1f-%.1f сек",
            self.response_delay_min,
            self.response_delay_max,
        )

        self.cover_letter_ai = (
            tool.get_cover_letter_ai(args.system_prompt)
            if args.use_ai
            else None
        )
        self.ai_filter = args.ai_filter
        self.vacancy_filter_ai = None
        self._resume_analysis_cache: dict[tuple[str | None, str], str] = {}
        self._vacancy_context_cache: dict[str, dict[str, Any]] = {}
        self.ai_error_count = 0

        self._apply_vacancies()
        if self.ai_error_count:
            logger.error(
                "AI не сгенерировал письмо для %d вакансий; запуск помечен как неуспешный",
                self.ai_error_count,
            )
            return 1
        return None

    SEL_CAPTCHA_IMAGE = 'img[data-qa="account-captcha-picture"]'
    SEL_CAPTCHA_INPUT = 'input[data-qa="account-captcha-input"]'

    # Даже куки не грузятся, исправь
    async def _solve_captcha_async(self, captcha_url: str) -> bool:
        from playwright.async_api import async_playwright

        captcha_ai = self.tool.get_captcha_ai()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()

                await page.goto(captcha_url, timeout=30000)

                captcha_element = await page.wait_for_selector(
                    self.SEL_CAPTCHA_IMAGE, timeout=10000, state="visible"
                )

                img_bytes = await captcha_element.screenshot()

                captcha_text = await asyncio.to_thread(
                    captcha_ai.solve_captcha, img_bytes
                )

                if not captcha_text:
                    logger.error("AI не смог распознать капчу")
                    return False

                logger.info(f"Распознанный текст капчи: {captcha_text}")

                await page.fill(self.SEL_CAPTCHA_INPUT, captcha_text)
                await page.press(self.SEL_CAPTCHA_INPUT, "Enter")

                await page.wait_for_load_state("networkidle", timeout=15000)

                cookies = await context.cookies()
                for c in cookies:
                    self.tool.session.cookies.set(
                        c["name"],
                        c["value"],
                        domain=c.get("domain", ""),
                        path=c.get("path", "/"),
                    )

                return True
            finally:
                await browser.close()

        return False

    def _apply_vacancies(self) -> None:
        resumes: list[datatypes.Resume] = self.tool.get_resumes()
        if not self.dry_run:
            try:
                self.tool.storage.resumes.save_batch(resumes)
            except RepositoryError as ex:
                logger.exception(ex)
        resumes = (
            list(filter(lambda x: x["id"] == self.resume_id, resumes))
            if self.resume_id
            else resumes
        )
        # Выбираем только опубликованные
        resumes = list(
            filter(lambda x: x["status"]["id"] == "published", resumes)
        )
        if not resumes:
            logger.warning("У вас нет опубликованных резюме")
            return

        me: datatypes.User = self.tool.get_me()
        seen_employers = set()

        for resume in resumes:
            if self._response_quota_reached():
                logger.info(
                    "Reached application quota (%d); no more resumes will be processed",
                    self.max_responses,
                )
                break
            self._apply_resume(
                resume=resume,
                user=me,
                seen_employers=seen_employers,
            )

        # Синхронизация откликов
        if self.dry_run:
            logger.info("dry-run: negotiations were not synchronized to storage")
        else:
            for neg in self.tool.get_negotiations():
                try:
                    # Пропускаем отклики с удаленными работодателями (employer_id = null)
                    vacancy = neg.get("vacancy")
                    if (
                        not vacancy
                        or not vacancy.get("employer")
                        or not vacancy["employer"].get("id")
                    ):
                        logger.debug(
                            "Пропуск отклика: вакансия или работодатель удален"
                        )
                        continue
                    self.tool.storage.negotiations.save(neg)
                except RepositoryError as e:
                    logger.warning(e)

        print("📝 Отклики на вакансии разосланы!")
