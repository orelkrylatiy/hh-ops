from __future__ import annotations

import logging
import re
from typing import Any

from ..ai.base import AIError
from ..utils import json as utils_json
from ..utils.string import strip_tags

logger = logging.getLogger(__package__)


class ApplyVacanciesAIMixin:
    def _get_full_vacancy(self, vacancy_id: str | int) -> dict[str, Any]:
        vacancy_key = str(vacancy_id)
        if vacancy_key in self._vacancy_context_cache:
            return self._vacancy_context_cache[vacancy_key]

        full_vacancy = self.api_client.get(f"/vacancies/{vacancy_id}")
        self._vacancy_context_cache[vacancy_key] = full_vacancy
        self._save_vacancy_links(full_vacancy)
        return full_vacancy

    def _get_full_resume(self, resume_id: str) -> dict[str, Any]:
        return self.api_client.get(f"/resumes/{resume_id}")

    def _build_cover_letter_context(
        self,
        vacancy: dict[str, Any],
        resume: dict[str, Any],
    ) -> str:
        parts: list[str] = []

        vacancy_id = vacancy.get("id")
        full_vacancy = self._get_full_vacancy(vacancy_id) if vacancy_id else vacancy
        employer = full_vacancy.get("employer") or vacancy.get("employer") or {}
        salary = full_vacancy.get("salary") or vacancy.get("salary") or {}

        if vacancy_name := full_vacancy.get("name") or vacancy.get("name"):
            parts.append(f"Вакансия: {vacancy_name}")
        if employer_name := employer.get("name"):
            parts.append(f"Компания: {employer_name}")
        if area := (full_vacancy.get("area") or {}).get("name"):
            parts.append(f"Локация: {area}")
        if employment := (full_vacancy.get("employment") or {}).get("name"):
            parts.append(f"Формат занятости: {employment}")
        if schedule := (full_vacancy.get("schedule") or {}).get("name"):
            parts.append(f"График: {schedule}")
        if experience := (full_vacancy.get("experience") or {}).get("name"):
            parts.append(f"Опыт: {experience}")
        if salary:
            parts.append(
                "Зарплата: "
                f"от {salary.get('from') or ''} "
                f"до {salary.get('to') or ''} "
                f"{salary.get('currency') or ''}".strip()
            )

        description = strip_tags(full_vacancy.get("description") or "")
        if description:
            parts.append(f"Описание вакансии:\n{description[:4000]}")

        key_skills = full_vacancy.get("key_skills") or []
        if key_skills:
            parts.append(
                "Ключевые навыки вакансии: "
                + ", ".join(
                    skill["name"] for skill in key_skills if skill.get("name")
                )
            )

        resume_id = resume.get("id")
        if resume_title := resume.get("title"):
            parts.append(f"Название моего резюме: {resume_title}")
        if resume_id:
            resume_summary = self._analyze_resume_heavy(resume)
            if resume_summary:
                parts.append(f"Контекст моего резюме:\n{resume_summary[:4000]}")

        return "\n\n".join(parts)

    def _analyze_resume_heavy(self, resume: dict[str, Any]) -> str:
        resume_id = resume.get("id")
        cache_key = (resume_id, "heavy")
        if cache_key in self._resume_analysis_cache:
            return self._resume_analysis_cache[cache_key]

        if resume_id:
            try:
                full_resume = self._get_full_resume(resume_id)

                parts: list[str] = []

                title = full_resume.get("title", "")
                if title:
                    parts.append(f"Должность: {title}")

                if "skills" in full_resume:
                    parts.append("\n---------- О СЕБЕ ----------")
                    parts.append(full_resume.get("skills", ""))

                if "skill_set" in full_resume and full_resume["skill_set"]:
                    parts.append("\n---------- НАВЫКИ ----------")
                    skills_row = ", ".join(full_resume["skill_set"])
                    parts.append(skills_row)

                if "experience" in full_resume:
                    parts.append("\n---------- ОПЫТ РАБОТЫ ----------")
                    for exp in full_resume.get("experience", []):
                        company = exp.get("company", "Не указано")
                        position = exp.get("position", "Не указано")
                        start = exp.get("start", "")
                        end = exp.get("end") or "по настоящее время"

                        parts.append(f"\n- {company}")
                        parts.append(f" Должность: {position}")
                        parts.append(f" Период: {start} - {end}")

                        description = exp.get("description")
                        if description:
                            parts.append(" Описание:")
                            parts.append(f" {description}")

                result = "\n".join(parts)
                self._resume_analysis_cache[cache_key] = result
                return result

            except Exception as ex:
                logger.warning("Не удалось получить полное резюме: %s", ex)

        return ""

    def _analyze_resume_light(self, resume: dict[str, Any]) -> str:
        resume_id = resume.get("id")
        cache_key = (resume_id, "light")
        if cache_key in self._resume_analysis_cache:
            return self._resume_analysis_cache[cache_key]

        parts: list[str] = []
        full_resume = self._get_full_resume(resume_id)

        title = full_resume.get("title", "")
        if title:
            parts.append(f"Должность: {title}")

        if "skill_set" in full_resume and full_resume["skill_set"]:
            parts.append("Навыки: ")
            skills_row = ", ".join(full_resume["skill_set"])
            parts.append(skills_row)

        result = "\n".join(parts)
        self._resume_analysis_cache[cache_key] = result
        return result

    def _get_vacancy_key_skills(self, vacancy_id: str | int) -> str:
        try:
            full_vacancy = self.api_client.get(f"/vacancies/{vacancy_id}")
            key_skills_data = full_vacancy.get("key_skills") or []
            return ", ".join(
                s["name"] for s in key_skills_data if s.get("name")
            )
        except Exception as ex:
            logger.warning(
                "Не удалось получить key_skills вакансии %s: %s",
                vacancy_id,
                ex,
            )
            return ""

    def _build_vacancy_context(
        self,
        vacancy: dict[str, Any],
        full_vacancy: dict[str, Any] | None = None,
        include_full: bool = False,
    ) -> str:
        _ = include_full
        parts: list[str] = []

        name = vacancy.get("name")
        if name:
            parts.append(f"Вакансия: {name}")

        if full_vacancy:
            description = full_vacancy.get("description")
            if description:
                parts.append(f"Описание: {strip_tags(description)}")
        elif vacancy.get("id"):
            key_skills = self._get_vacancy_key_skills(vacancy["id"])
            if key_skills:
                parts.append(f"Ключевые навыки: {key_skills}")

        return "\n".join(parts)

    def _ask_ai_suitability(
        self,
        prompt: str,
        vacancy_name: str,
        log_suffix: str = "",
    ) -> bool:
        max_retries = 3

        if not self.vacancy_filter_ai:
            return True

        for attempt in range(max_retries):
            try:
                response = self.vacancy_filter_ai.complete(prompt).strip()

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "AI %s ответ (попытка %d): %s",
                        log_suffix,
                        attempt + 1,
                        response,
                    )

                result = self._parse_ai_json_response(response)
                if result is not None:
                    if result:
                        return True
                    logger.info(
                        "Вакансия %s отклонена AI %s",
                        vacancy_name,
                        log_suffix,
                    )
                    return False

                logger.warning(
                    "AI %s не дал валидный JSON для вакансии %s (попытка %d/%d)",
                    log_suffix,
                    vacancy_name,
                    attempt + 1,
                    max_retries,
                )
                continue

            except AIError as ex:
                logger.error("Ошибка AI %s: %s", log_suffix, ex)
                return True

        logger.warning(
            "AI %s не дал валидный JSON после %d попыток для вакансии %s",
            log_suffix,
            max_retries,
            vacancy_name,
        )
        return True

    def _parse_ai_json_response(self, response: str) -> bool | None:
        response = response.strip().lower()

        if response in ("да", "yes", "true"):
            return True
        if response in ("нет", "no", "false"):
            return False

        if response.startswith("```"):
            response = re.sub(r"^```(?:json)?\s*", "", response)
            response = re.sub(r"\s*```$", "", response)

        json_match = re.search(
            r'\{[^{}]*"suitable"\s*:\s*(true|false)[^{}]*\}',
            response,
            re.IGNORECASE,
        )
        if json_match:
            try:
                data = utils_json.loads(json_match.group(0))
                return data.get("suitable")
            except Exception:
                return None

        return None

    def _is_vacancy_suitable_heavy(self, vacancy: dict[str, Any]) -> bool:
        full_vacancy = None
        if vacancy.get("id"):
            full_vacancy = self._get_full_vacancy(vacancy["id"])

        vacancy_info = self._build_vacancy_context(
            vacancy,
            full_vacancy=full_vacancy,
            include_full=True,
        )
        prompt = f"Вакансия: {vacancy_info}"
        return self._ask_ai_suitability(
            prompt,
            vacancy.get("name", ""),
            "(heavy)",
        )

    def _is_vacancy_suitable_light(self, vacancy: dict[str, Any]) -> bool:
        vacancy_info = self._build_vacancy_context(vacancy, include_full=False)
        prompt = f"Вакансия: {vacancy_info}"
        return self._ask_ai_suitability(
            prompt,
            vacancy.get("name", ""),
            "(light)",
        )

    def _build_filter_system_prompt_heavy(self, resume_analysis: str) -> str:
        return f"""
Определи, подходит ли вакансия кандидату.

Смотри в первую очередь на тип работы (роль), а не на технологии.

Правила:

1. Если работа по сути другая -> suitable = false

2. Если роль совпадает или очень близкая:
   - есть пересечения по задачам или навыкам -> suitable = true
   - даже частичное совпадение допустимо

3. Общие технологии сами по себе ничего не значат.
   Если работа разная, это не делает вакансию подходящей.

4. Если данных мало:
   - ориентируйся на название роли

Не пиши объяснения.
Ответ строго JSON:
{{"suitable": true}} или {{"suitable": false}}

Кандидат:
{resume_analysis}
"""

    def _build_filter_system_prompt_light(self, resume_analysis: str) -> str:
        return f"""
Ты делаешь очень грубую проверку: подходит вакансия или нет.

Используй только:
- название резюме
- список навыков резюме
- название вакансии
- явно указанные ключевые навыки вакансии

Не анализируй описание, обязанности, контекст, домен, уровень, карьерный рост и прочую воду.
Не додумывай ничего, чего нет в тексте.

Правила:
- если название вакансии и резюме в одной профессии или близких ролях, и есть хотя бы частичное совпадение по ключевым навыкам -> suitable = true
- если роли явно разные или совпадений по навыкам почти нет -> suitable = false
- если данных мало -> ориентируйся только на явные совпадения, без фантазий

Ответ только JSON:
{{"suitable": true}} или {{"suitable": false}}

Кандидат:
{resume_analysis}
"""
