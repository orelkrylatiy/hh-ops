"""Shadow integration of humanizer-framework into the reply pipeline.

Stage A of the migration plan: the framework generates a parallel draft with
the same context, its text is never sent, and the comparison is logged. The
primary reply path, including send safety and the static fallback, stays
exactly as before.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from humanizer_framework import CommunicationFramework
from humanizer_framework.providers.base import Provider

from .adapter import ChatCompleteProvider, build_chat_reply_request

logger = logging.getLogger(__name__)

SHADOW_ENV_FLAG = "HH_FRAMEWORK_SHADOW"

_TRUE_VALUES = {"1", "true", "yes", "on"}


class ReplyDecisionLike(Protocol):
    """Structural subset of automation.reply_worker.ReplyDecision used for shadow drafts."""

    @property
    def chat_id(self) -> str: ...

    @property
    def context(self) -> list[str]: ...

    @property
    def initiated_by_us(self) -> bool: ...

    @property
    def vacancy_name(self) -> str: ...

    @property
    def employer_name(self) -> str: ...

    @property
    def latest_message_text(self) -> str: ...


@dataclass(frozen=True)
class ShadowComparison:
    chat_id: str
    primary_reply: str
    framework_text: str
    plan_action: str
    target_length: str
    max_chars: int
    ask_question: bool
    allow_cta: bool
    issue_codes: tuple[str, ...]
    rewritten: bool


def framework_shadow_enabled(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(SHADOW_ENV_FLAG, "").strip().lower() in _TRUE_VALUES


class FrameworkShadowReplier:
    """Generate a parallel framework draft for REPLY_TEXT chats; never raises, never sends."""

    def __init__(
        self,
        complete: Callable[[str], str],
        *,
        provider: Provider | None = None,
    ) -> None:
        self._provider: Provider = provider or ChatCompleteProvider(complete)
        self._framework = CommunicationFramework(provider=self._provider)
        self.comparisons = 0

    def compare(self, decision: ReplyDecisionLike, primary_reply: str) -> ShadowComparison | None:
        try:
            request = build_chat_reply_request(
                context=decision.context,
                initiated_by_us=decision.initiated_by_us,
                vacancy_name=decision.vacancy_name,
                employer_name=decision.employer_name,
                latest_message_text=decision.latest_message_text,
            )
            result = self._framework.generate(request)
        except Exception as exc:  # shadow must never break the send path
            logger.warning(
                "Framework shadow generation failed for chat %s: %s",
                decision.chat_id,
                exc,
            )
            return None

        comparison = ShadowComparison(
            chat_id=decision.chat_id,
            primary_reply=primary_reply,
            framework_text=result.text,
            plan_action=result.plan.action.value,
            target_length=result.plan.target_length.value,
            max_chars=result.plan.max_chars,
            ask_question=result.plan.ask_question,
            allow_cta=result.plan.allow_cta,
            issue_codes=tuple(
                f"{'H' if issue.hard else 's'}:{issue.code}" for issue in result.issues
            ),
            rewritten=result.rewritten,
        )
        self.comparisons += 1
        logger.info(
            "FRAMEWORK_SHADOW chat=%s plan=%s/%s/max=%s issues=%s rewritten=%s "
            "primary_chars=%d framework_chars=%d\nPRIMARY: %s\nFRAMEWORK: %s",
            comparison.chat_id,
            comparison.plan_action,
            comparison.target_length,
            comparison.max_chars,
            ",".join(comparison.issue_codes) or "-",
            comparison.rewritten,
            len(comparison.primary_reply),
            len(comparison.framework_text),
            comparison.primary_reply,
            comparison.framework_text,
        )
        return comparison
