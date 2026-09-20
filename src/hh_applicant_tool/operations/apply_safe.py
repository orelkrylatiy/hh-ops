from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..ai.base import AIError
from ..api import datatypes
from ..utils.misc import expand_env_placeholders, load_prompt
from ..utils.string import rand_text
from ._apply_vacancies_apply_flow import VacancyResponseResult
from .apply_vacancies import Namespace
from .apply_vacancies import Operation as BaseApplyOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool


logger = logging.getLogger(__package__)


class Operation(BaseApplyOperation):
    """Apply to vacancies with a fail-open static cover-letter fallback."""

    __aliases__ = ("apply-with-fallback",)

    def _configure_cover_letter_fallback(
        self,
        tool: HHApplicantTool,
        args: Namespace,
    ) -> None:
        raw_config = tool.config.get("cover_letter_fallback", {})
        if raw_config is None:
            raw_config = {}
        if not isinstance(raw_config, dict):
            raise ValueError("'cover_letter_fallback' must be a JSON object")

        self.cover_letter_fallback_enabled = bool(raw_config.get("enabled", True))
        configured_message = str(raw_config.get("message") or "").strip()

        if configured_message:
            self.cover_letter = expand_env_placeholders(configured_message)
        elif args.letter_file:
            self.cover_letter = expand_env_placeholders(
                args.letter_file.read_text(encoding="utf-8", errors="ignore")
            )
        else:
            self.cover_letter = BaseApplyOperation.cover_letter

        if self.cover_letter_fallback_enabled and not self.cover_letter.strip():
            raise ValueError("cover-letter fallback is enabled but its message is empty")

    def _render_cover_letter_fallback(
        self,
        message_placeholders: dict[str, str],
    ) -> str:
        letter = (rand_text(self.cover_letter) % message_placeholders).strip()
        if not letter:
            raise ValueError("cover-letter fallback rendered an empty message")
        return letter

    def _build_cover_letter(
        self,
        vacancy: dict[str, Any],
        resume: datatypes.Resume,
        message_placeholders: dict[str, str],
    ) -> str:
        if not (self.force_message or vacancy.get("response_letter_required")):
            return ""

        if self.cover_letter_ai:
            try:
                letter = (
                    super()
                    ._build_cover_letter(
                        vacancy,
                        resume,
                        message_placeholders,
                    )
                    .strip()
                )
                if letter:
                    return letter
                raise AIError("cover-letter AI returned an empty message")
            except (AIError, OSError, ValueError) as exc:
                if not self.cover_letter_fallback_enabled:
                    raise
                self.cover_fallback_count += 1
                logger.warning(
                    "HH_COVER_FALLBACK vacancy=%s reason=%s",
                    vacancy.get("id", ""),
                    type(exc).__name__,
                )

        return self._render_cover_letter_fallback(message_placeholders)

    def _send_vacancy_response(
        self,
        vacancy: dict[str, Any],
        resume_id: str,
        letter: str,
    ) -> VacancyResponseResult:
        if (self.force_message or vacancy.get("response_letter_required")) and not letter.strip():
            logger.error(
                "Refusing to apply without a required cover letter: %s",
                vacancy.get("alternate_url", vacancy.get("id")),
            )
            return VacancyResponseResult(should_continue=True, accepted=False)
        return super()._send_vacancy_response(vacancy, resume_id, letter)

    def run(
        self,
        tool: HHApplicantTool,
        args: Namespace,
    ) -> int | None:
        self.tool = tool
        self._args = args
        args.system_prompt = load_prompt(args.system_prompt)
        args.message_prompt = load_prompt(args.message_prompt)
        self._configure_cover_letter_fallback(tool, args)
        self._assign_args(args)
        self._resolve_resume_selector(tool)

        if self.max_responses is not None and self.max_responses < 0:
            raise ValueError("max_responses must be a non-negative integer")

        self.responses_sent = 0
        self.response_delay_min, self.response_delay_max = self._parse_response_delay(
            args.response_delay
        )
        logger.info(
            "Задержка между откликами: %.1f-%.1f сек",
            self.response_delay_min,
            self.response_delay_max,
        )

        self.cover_fallback_count = 0
        try:
            self.cover_letter_ai = (
                tool.get_cover_letter_ai(args.system_prompt) if args.use_ai else None
            )
        except (AIError, OSError, ValueError) as exc:
            if not self.cover_letter_fallback_enabled:
                raise
            logger.warning(
                "Cover-letter AI initialization failed (%s); static fallback is armed",
                type(exc).__name__,
            )
            self.cover_letter_ai = None

        self.ai_filter = args.ai_filter
        self.vacancy_filter_ai = None
        self._resume_analysis_cache: dict[tuple[str | None, str], str] = {}
        self._vacancy_context_cache: dict[str, dict[str, Any]] = {}
        self.ai_error_count = 0

        self._apply_vacancies()

        if self.cover_fallback_count:
            logger.warning(
                "Static cover-letter fallback used for %d vacancy(s)",
                self.cover_fallback_count,
            )
        if self.ai_error_count:
            logger.error(
                "AI processing failed for %d vacancy(s); run marked unsuccessful",
                self.ai_error_count,
            )
            return 1
        return None
