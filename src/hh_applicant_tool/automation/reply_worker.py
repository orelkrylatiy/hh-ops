from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hh_applicant_tool.ai.openai import ChatOpenAI, OpenAIError
from hh_applicant_tool.automation.hot_lead_state import HotLeadStore
from hh_applicant_tool.automation.hot_leads import (
    HotLeadDetectionError,
    HotLeadDetector,
    TelegramNotificationError,
    TelegramNotifier,
    format_hot_lead_alert,
    prefilter_hot_lead,
)
from hh_applicant_tool.automation.reply_state import ManualChatQueue

logger = logging.getLogger(__name__)

EMPLOYER_ROLE = "EMPLOYER"
APPLICANT_ROLE = "APPLICANT"
MAX_CONTEXT_MESSAGES = 30
MAX_REPLY_CHARS = 2000

ACTION_REPLY = "REPLY_TEXT"
ACTION_IGNORE = "IGNORE"
ACTION_MANUAL = "MANUAL"

AI_CLICHES = (
    "важно отметить",
    "таким образом",
    "в данном случае",
    "не просто",
    "дело не только",
)
PLACEHOLDER_TOKENS = ("[", "]", "{{", "}}", "<имя>", "<name>")

# HH шлёт служебные уведомления (удаление вакансии и т.п.) с author=employer,
# поэтому роль не помогает - распознаём по тексту. На такие сообщения отвечать нельзя.
SYSTEM_NOTIFICATION_RE = re.compile(
    r"^\s*(Вакансия\s+(не\s+прошла\s+проверку|была\s+удалена|закрыта|снята\s+с\s+публикации|архивирована)"
    r"|Резюме\s+(было\s+)?отклонено"
    r"|Отклик\s+(был\s+)?(отклон|сня|истёк)"
    r"|Вы\s+можете\s+откликаться\s+на\s+другие\s+вакансии)",
    re.IGNORECASE,
)

ACKNOWLEDGEMENT_RE = re.compile(
    r"(спасибо\s+за\s+(отклик|интерес)|ваш\s+отклик\s+(получен|принят)|"
    r"резюме\s+(получено|рассмотрим)|мы\s+(рассмотрим|ознакомимся)|"
    r"(верн[её]мся|свяжемся)\s+с\s+вами)",
    re.IGNORECASE,
)
QUESTIONNAIRE_COMPLETED_RE = re.compile(
    r"(ваши\s+ответы\s+(?:отправлены|переданы)\s+работодателю"
    r"(?:[\s\S]{0,240}?если\s+(?:ваш\s+)?отклик[\s\S]{0,160}?"
    r"(?:заинтересует|подойд[её]т)[\s\S]{0,160}?"
    r"(?:напишет|позвонит|свяжется))?"
    r"|ответы\s+(?:отправлены|переданы)\s+работодателю)",
    re.IGNORECASE,
)

UI_ACTION_RE = re.compile(
    r"(нажм(?:ите|и)\s+(?:на\s+)?кноп|кнопк\w*\s+(?:ниже|выше)|"
    r"выбер(?:ите|и)\s+(?:один\s+)?вариант|выберите\s+ответ)",
    re.IGNORECASE,
)

QUESTION_HINT_RE = re.compile(
    r"\b(сколько|когда|какой|какая|какие|где|почему|готовы|можете|"
    r"есть\s+ли|расскажите|укажите|ответьте|уточните|подскажите)\b",
    re.IGNORECASE,
)

FOLLOWUP_HINT_RE = re.compile(
    r"\b(приглаша\w*|интервью|собеседован\w*|созвон\w*|встреч\w*|"
    r"тестов\w*|задан\w*|зарплат\w*|вилк\w*|офис\w*|удал[её]н\w*|"
    r"гибрид\w*|формат\w*|телеграм\w*|telegram|whatsapp|контакт\w*|"
    r"ссылк\w*|врем\w*|дат\w*)\b",
    re.IGNORECASE,
)


def is_system_notification(text: str) -> bool:
    return bool(SYSTEM_NOTIFICATION_RE.match(text or ""))


class HHCLIError(RuntimeError):
    """Raised when the hh-applicant-tool subprocess fails."""


@dataclass(frozen=True)
class ReplyWorkerConfig:
    profile_id: str = ""
    dry_run: bool = True
    max_chats: int = 100
    ai_retries: int = 1
    send_retries: int = 2
    send_retry_delay: float = 1.0
    hot_leads_enabled: bool = False
    # Чаты, которые бот обязан игнорировать (живой диалог для ручного ответа).
    skip_chat_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplyDecision:
    chat_id: str
    expected_last_message_id: str
    context: list[str]
    initiated_by_us: bool
    vacancy_name: str
    employer_name: str
    action: str = ACTION_REPLY
    reason: str = "employer_message"
    latest_message_text: str = ""


class HHCLI:
    """Small JSON boundary around the existing CLI.

    Keeping network/auth logic in the main CLI means cron and the interactive
    commands use exactly the same token refresh and HTTP implementation.
    """

    def __init__(self, profile_id: str = "") -> None:
        self.profile_id = profile_id

    def _base_command(self) -> list[str]:
        command = ["hh-applicant-tool", "--no-auto-auth"]
        if self.profile_id:
            command.extend(["--profile-id", self.profile_id])
        return command

    def call_api(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        json_data: dict[str, Any] | None = None,
        form_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        command = [*self._base_command(), "call-api", endpoint]
        if method != "GET":
            command.extend(["--method", method])
        if form_params is not None:
            # call-api без --data шлёт PARAM=VALUE как form-encoded тело
            command.extend(f"{key}={value}" for key, value in form_params.items())
        if json_data is not None:
            command.extend(
                ["--data", json.dumps(json_data, ensure_ascii=False, separators=(",", ":"))]
            )

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown HH CLI error"
            raise HHCLIError(details)
        if not result.stdout.strip():
            return {}
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HHCLIError(f"invalid HH JSON: {result.stdout[:300]}") from exc
        if not isinstance(payload, dict):
            raise HHCLIError("HH API returned a non-object response")
        return payload


def select_ai_config(
    config: dict[str, Any],
    sections: tuple[str, ...] = ("openai_reply", "openai_cover_letter"),
) -> tuple[str, dict[str, Any]]:
    """Return the first configured OpenAI-compatible provider section."""
    for section in sections:
        value = config.get(section)
        if isinstance(value, dict) and value:
            return section, value
    raise ValueError("configure one of: " + ", ".join(sections))


def build_ai_client(
    config: dict[str, Any],
    system_prompt: str,
    *,
    sections: tuple[str, ...] = ("openai_reply", "openai_cover_letter"),
    temperature: float | None = None,
    max_completion_tokens: int | None = None,
) -> ChatOpenAI:
    section, provider = select_ai_config(config, sections)
    api_key = str(provider.get("api_key") or "").strip()
    base_url = str(provider.get("base_url") or "").strip()
    model = str(provider.get("model") or "").strip()
    if not api_key:
        raise ValueError(f"'{section}.api_key' is required")
    if not base_url:
        raise ValueError(f"'{section}.base_url' is required")
    if not model:
        raise ValueError(f"'{section}.model' is required")

    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system_prompt=system_prompt,
        temperature=(temperature if temperature is not None else float(provider.get("temperature", 0.35))),
        max_completion_tokens=(
            max_completion_tokens
            if max_completion_tokens is not None
            else int(provider.get("max_completion_tokens", 500))
        ),
        rate_limit=int(provider.get("rate_limit", 30)),
        timeout=float(provider.get("timeout", 45.0)),
        max_retries=int(provider.get("max_retries", 3)),
    )


def load_json_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("config.json must contain a JSON object")
    return payload


def message_role(message: dict[str, Any]) -> str:
    sender = message.get("sender_display_info")
    if not isinstance(sender, dict):
        author = message.get("author")
        if isinstance(author, dict):
            participant = str(author.get("participant_type") or "").upper()
            return participant if participant in (EMPLOYER_ROLE, APPLICANT_ROLE) else ""
        return ""
    return str(sender.get("role") or "").upper()


def message_text(message: dict[str, Any]) -> str:
    payload = message.get("payload")
    if isinstance(payload, dict):
        text = str(payload.get("text") or "").strip()
        if text:
            return text
    return str(message.get("text") or "").strip()


def message_id(message: dict[str, Any]) -> str:
    return str(message.get("id") or "")


def message_created_at(message: dict[str, Any]) -> str:
    """Timestamp for both current /negotiations and legacy /common/chats payloads."""
    return str(message.get("created_at") or message.get("creation_time") or "").strip()


def sorted_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return chat messages oldest-first, failing closed if order is unknowable."""
    if not messages:
        return []
    if any(not message_created_at(item) for item in messages):
        logger.warning(
            "HH chat history contains a message without created_at/creation_time; "
            "refusing to infer the latest sender"
        )
        return []
    return sorted(messages, key=message_created_at)


def normalize_message_text(text: str) -> str:
    """Normalize harmless formatting differences for repeated-bot detection."""
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return normalized.strip(" .,!?:;…-")


def repeated_employer_message_after_reply(
    messages: list[dict[str, Any]],
) -> bool:
    """Detect a bot repeating the same prompt after the applicant already replied."""
    ordered = sorted_messages(messages)
    if len(ordered) < 3 or message_role(ordered[-1]) != EMPLOYER_ROLE:
        return False

    latest_text = normalize_message_text(message_text(ordered[-1]))
    if not latest_text:
        return False

    for index in range(len(ordered) - 2, -1, -1):
        item = ordered[index]
        if message_role(item) != EMPLOYER_ROLE:
            continue
        if normalize_message_text(message_text(item)) != latest_text:
            continue
        if any(message_role(between) == APPLICANT_ROLE for between in ordered[index + 1 : -1]):
            return True
    return False


def classify_chat(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Classify the latest employer turn before spending an LLM call."""
    ordered = sorted_messages(messages)
    if not ordered:
        return ACTION_IGNORE, "empty_or_unordered_history"

    latest = ordered[-1]
    text = message_text(latest)
    if message_role(latest) != EMPLOYER_ROLE:
        return ACTION_IGNORE, "latest_not_employer"
    if not text:
        return ACTION_MANUAL, "employer_message_without_text"
    if is_system_notification(text):
        return ACTION_IGNORE, "hh_system_notification"
    if UI_ACTION_RE.search(text):
        return ACTION_MANUAL, "ui_action_hint"
    if (
        QUESTIONNAIRE_COMPLETED_RE.search(text)
        and "?" not in text
        and not QUESTION_HINT_RE.search(text)
        and not FOLLOWUP_HINT_RE.search(text)
    ):
        return ACTION_IGNORE, "questionnaire_completed"
    if repeated_employer_message_after_reply(ordered):
        return ACTION_MANUAL, "repeated_after_applicant_reply"
    if (
        "?" not in text
        and not QUESTION_HINT_RE.search(text)
        and not FOLLOWUP_HINT_RE.search(text)
        and ACKNOWLEDGEMENT_RE.search(text)
    ):
        return ACTION_IGNORE, "acknowledgement_without_question"
    return ACTION_REPLY, "employer_message"


def sanitize_reply_text(text: str) -> str:
    """Косметическая нормализация: LLM любит длинные тире, живой человек — дефис.

    Вызывается до проверки качества, чтобы тире не отправляло чат
    в fail-closed (два отклонения подряд = чат без ответа).
    """
    return (text or "").replace("—", "-").replace("–", "-")


def reply_quality_issues(text: str) -> list[str]:
    normalized = " ".join((text or "").split())
    if not normalized:
        return ["empty reply"]

    issues: list[str] = []
    lowered = normalized.lower()
    if len(normalized) > MAX_REPLY_CHARS:
        issues.append("reply is too long")
    if "—" in normalized or "–" in normalized:
        issues.append("contains a long dash")
    if any(token in normalized for token in PLACEHOLDER_TOKENS):
        issues.append("contains a placeholder")
    if any(phrase in lowered for phrase in AI_CLICHES):
        issues.append("contains an AI-style cliche")
    return issues


def safe_preview_reply(initiated_by_us: bool) -> str:
    if initiated_by_us:
        return "Здравствуйте! Спасибо за сообщение. Готов ответить на вопросы и обсудить детали."
    return "Здравствуйте! Спасибо за приглашение. Готов ответить на вопросы и обсудить детали."


def build_context(messages: list[dict[str, Any]]) -> tuple[list[str], bool]:
    ordered = sorted_messages(messages)
    if not ordered:
        return [], False
    first_role = message_role(ordered[0])
    context: list[str] = []
    for item in ordered[-MAX_CONTEXT_MESSAGES:]:
        text = message_text(item)
        if not text:
            continue
        role = message_role(item)
        if role == APPLICANT_ROLE:
            author = "Я"
        elif role == EMPLOYER_ROLE:
            author = "Работодатель"
        else:
            continue
        context.append(f"{author}: {text}")
    return context, first_role == APPLICANT_ROLE


class ReplyWorker:
    def __init__(
        self,
        config: ReplyWorkerConfig,
        *,
        hh: HHCLI,
        ai: ChatOpenAI | None,
        system_prompt: str,
        manual_queue: ManualChatQueue | None = None,
        hot_lead_detector: HotLeadDetector | None = None,
        hot_lead_store: HotLeadStore | None = None,
        hot_lead_notifier: TelegramNotifier | None = None,
    ) -> None:
        self.config = config
        self.hh = hh
        self.ai = ai
        self.system_prompt = system_prompt
        self.manual_queue = manual_queue
        self.hot_lead_detector = hot_lead_detector
        self.hot_lead_store = hot_lead_store
        self.hot_lead_notifier = hot_lead_notifier

    def _notify_hot_event(self, event: dict[str, Any], stats: dict[str, Any]) -> None:
        if self.config.dry_run or self.hot_lead_notifier is None or self.hot_lead_store is None:
            return
        try:
            self.hot_lead_notifier.send(format_hot_lead_alert(self.config.profile_id, event))
        except TelegramNotificationError as exc:
            self.hot_lead_store.mark_notification_failed(
                str(event["chat_id"]),
                str(event["message_id"]),
                str(exc),
            )
            stats["hot_notify_failed"] += 1
            logger.warning(
                "Hot lead Telegram notification failed for chat %s: %s",
                event["chat_id"],
                exc,
            )
            return
        self.hot_lead_store.mark_notified(
            str(event["chat_id"]),
            str(event["message_id"]),
        )
        stats["hot_notified"] += 1

    def _flush_pending_hot_notifications(self, stats: dict[str, Any]) -> None:
        if (
            not self.config.hot_leads_enabled
            or self.config.dry_run
            or self.hot_lead_store is None
            or self.hot_lead_notifier is None
        ):
            return
        for event in self.hot_lead_store.pending_notifications(limit=100):
            self._notify_hot_event(event, stats)

    def _process_hot_lead(self, decision: ReplyDecision, stats: dict[str, Any]) -> None:
        if not self.config.hot_leads_enabled or decision.action != ACTION_REPLY:
            return
        prefilter = prefilter_hot_lead(decision.latest_message_text)
        if not prefilter.candidate:
            return

        stats["hot_candidates"] += 1
        if self.config.dry_run:
            logger.info(
                "DRY-RUN hot-lead candidate chat=%s signals=%s",
                decision.chat_id,
                ",".join(prefilter.signals),
            )
            return
        if self.hot_lead_store is None or self.hot_lead_detector is None:
            stats["hot_ai_errors"] += 1
            logger.warning("Hot lead detection is enabled but detector/store is not configured")
            return

        existing = self.hot_lead_store.get(
            decision.chat_id,
            decision.expected_last_message_id,
        )
        if existing is not None:
            if bool(existing.get("is_hot")) and not bool(existing.get("notified")):
                self._notify_hot_event(existing, stats)
            return

        try:
            evaluation = self.hot_lead_detector.evaluate(
                context=decision.context,
                latest_message=decision.latest_message_text,
                vacancy_name=decision.vacancy_name,
                employer_name=decision.employer_name,
            )
        except (HotLeadDetectionError, OpenAIError) as exc:
            stats["hot_ai_errors"] += 1
            logger.warning("Hot lead classifier failed for chat %s: %s", decision.chat_id, exc)
            return

        self.hot_lead_store.record(
            chat_id=decision.chat_id,
            message_id=decision.expected_last_message_id,
            is_hot=evaluation.hot,
            confidence=evaluation.confidence,
            human_likelihood=evaluation.human_likelihood,
            reason=evaluation.reason,
            next_step=evaluation.next_step,
            message_text=decision.latest_message_text,
            vacancy_name=decision.vacancy_name,
            employer_name=decision.employer_name,
        )
        if not evaluation.hot:
            return

        stats["hot_leads"] += 1
        event = self.hot_lead_store.get(
            decision.chat_id,
            decision.expected_last_message_id,
        )
        if event is not None:
            logger.warning(
                "HOT LEAD chat=%s vacancy=%s employer=%s confidence=%.2f",
                decision.chat_id,
                decision.vacancy_name,
                decision.employer_name,
                evaluation.confidence,
            )
            self._notify_hot_event(event, stats)

    def collect_candidate_chats(self) -> list[dict[str, Any]]:
        """Collect chats via /negotiations (/common/chats is forbidden for API tokens)."""
        chats: list[dict[str, Any]] = []
        page = 0
        per_page = min(max(self.config.max_chats, 1), 100)
        while len(chats) < self.config.max_chats:
            payload = self.hh.call_api(f"/negotiations?page={page}&per_page={per_page}")
            items = payload.get("items")
            if not isinstance(items, list) or not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                state = item.get("state")
                if isinstance(state, dict) and state.get("id") == "discard":
                    negotiation_id = str(item.get("id") or "")
                    if negotiation_id and self.manual_queue is not None and not self.config.dry_run:
                        self.manual_queue.resolve_chat(negotiation_id)
                    continue
                # /negotiations exposes messaging_status: only "ok" chats accept
                # POST /negotiations/{id}/messages (no_invitation/disabled_by_employer
                # are rejected server-side; skip them before burning AI/API calls).
                messaging_status = str(item.get("messaging_status") or "")
                if messaging_status and messaging_status != "ok":
                    continue
                negotiation_id = str(item.get("id") or "")
                if not negotiation_id:
                    continue
                if negotiation_id in self.config.skip_chat_ids:
                    continue
                try:
                    messages = self._negotiation_messages(negotiation_id)
                except HHCLIError as exc:
                    logger.warning(
                        "Could not load messages for negotiation %s: %s", negotiation_id, exc
                    )
                    continue
                ordered = sorted_messages(messages)
                if not ordered:
                    continue
                latest = ordered[-1]
                latest_id = message_id(latest)
                if message_role(latest) != EMPLOYER_ROLE:
                    if self.manual_queue is not None and not self.config.dry_run:
                        self.manual_queue.resolve_chat(negotiation_id)
                    continue
                if self.manual_queue is not None and not self.config.dry_run and latest_id:
                    self.manual_queue.resolve_chat(
                        negotiation_id,
                        keep_message_id=latest_id,
                    )
                chats.append(item)
                if len(chats) >= self.config.max_chats:
                    break
            pages = int(payload.get("pages") or 0)
            if not pages or page + 1 >= pages:
                break
            page += 1
        return chats

    def _negotiation_messages(self, negotiation_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        page = 0
        while page < 10:
            payload = self.hh.call_api(f"/negotiations/{negotiation_id}/messages?page={page}")
            items = payload.get("items")
            if not isinstance(items, list) or not items:
                break
            messages.extend(item for item in items if isinstance(item, dict))
            pages = int(payload.get("pages") or 0)
            if not pages or page + 1 >= pages:
                break
            page += 1
        return messages

    def _chat_detail(self, chat_id: str) -> dict[str, Any]:
        return {"messages": self._negotiation_messages(chat_id)}

    @staticmethod
    def _latest_message(detail: dict[str, Any]) -> dict[str, Any] | None:
        raw = detail.get("messages")
        if not isinstance(raw, list):
            return None
        messages = [item for item in raw if isinstance(item, dict)]
        ordered = sorted_messages(messages)
        return ordered[-1] if ordered else None

    @staticmethod
    def _write_allowed(detail: dict[str, Any]) -> bool:
        states = detail.get("chat_states")
        if not isinstance(states, dict):
            return True
        write_state = states.get("write_message_state")
        if not isinstance(write_state, dict):
            return True
        return write_state.get("allowed") is not False

    def _vacancy_info(self, chat: dict[str, Any]) -> tuple[str, str]:
        vacancy = chat.get("vacancy")
        if isinstance(vacancy, dict):
            employer = vacancy.get("employer")
            employer_name = str(employer.get("name") or "") if isinstance(employer, dict) else ""
            return str(vacancy.get("name") or "вакансия"), employer_name
        return self._vacancy_context({})

    def _vacancy_context(self, detail: dict[str, Any]) -> tuple[str, str]:
        display = detail.get("display")
        fallback_title = (
            str(display.get("title") or "вакансия") if isinstance(display, dict) else "вакансия"
        )
        vacancy_id = str(detail.get("vacancy_id") or "")
        if not vacancy_id:
            return fallback_title, ""
        try:
            vacancy = self.hh.call_api(f"/vacancies/{vacancy_id}")
        except HHCLIError as exc:
            logger.warning("Could not load vacancy %s: %s", vacancy_id, exc)
            return fallback_title, ""
        employer = vacancy.get("employer")
        employer_name = ""
        if isinstance(employer, dict):
            employer_name = str(employer.get("name") or "")
        return str(vacancy.get("name") or fallback_title), employer_name

    def make_decision(self, chat: dict[str, Any]) -> ReplyDecision | None:
        chat_id = str(chat.get("id") or "")
        if not chat_id or chat_id in self.config.skip_chat_ids:
            return None
        detail = self._chat_detail(chat_id)
        if detail.get("block_reason") or not self._write_allowed(detail):
            return None

        latest = self._latest_message(detail)
        if latest is None or message_role(latest) != EMPLOYER_ROLE:
            return None
        latest_id = message_id(latest)
        if not latest_id:
            return None

        raw_messages = detail.get("messages")
        messages = (
            [item for item in raw_messages if isinstance(item, dict)]
            if isinstance(raw_messages, list)
            else []
        )
        context, initiated_by_us = build_context(messages)
        if not context:
            return None
        action, reason = classify_chat(messages)
        vacancy_name, employer_name = self._vacancy_info(chat)
        return ReplyDecision(
            chat_id=chat_id,
            expected_last_message_id=latest_id,
            context=context,
            initiated_by_us=initiated_by_us,
            vacancy_name=vacancy_name,
            employer_name=employer_name,
            action=action,
            reason=reason,
            latest_message_text=message_text(latest),
        )

    def _generation_prompt(self, decision: ReplyDecision, correction: str = "") -> str:
        situation = (
            "Кандидат сам откликнулся на вакансию."
            if decision.initiated_by_us
            else "Работодатель инициировал диалог."
        )
        company = decision.employer_name or "не указана"
        correction_text = f"\n\nИсправь предыдущую попытку: {correction}." if correction else ""
        return (
            f"Вакансия: {decision.vacancy_name}\n"
            f"Компания: {company}\n"
            f"Ситуация: {situation}\n\n"
            "История переписки:\n"
            + "\n".join(decision.context)
            + "\n\nОтветь только текстом сообщения работодателю."
            + correction_text
        )

    def generate_reply(self, decision: ReplyDecision) -> str | None:
        if self.config.dry_run:
            return safe_preview_reply(decision.initiated_by_us)
        if self.ai is None:
            raise ValueError("AI client is required for live replies")

        correction = ""
        for attempt in range(self.config.ai_retries + 1):
            try:
                reply = sanitize_reply_text(
                    self.ai.complete(self._generation_prompt(decision, correction)).strip()
                )
            except OpenAIError as exc:
                logger.error("AI failed for chat %s: %s", decision.chat_id, exc)
                return None
            issues = reply_quality_issues(reply)
            if not issues:
                return reply
            logger.warning(
                "Rejected AI reply for chat %s: %s",
                decision.chat_id,
                ", ".join(issues),
            )
            correction = "; ".join(issues)
            if attempt >= self.config.ai_retries:
                return None
        return None

    def is_still_current(self, decision: ReplyDecision) -> bool:
        detail = self._chat_detail(decision.chat_id)
        if not self._write_allowed(detail):
            return False
        latest = self._latest_message(detail)
        if latest is None:
            return False
        return (
            message_role(latest) == EMPLOYER_ROLE
            and message_id(latest) == decision.expected_last_message_id
        )

    def _message_already_sent(self, decision: ReplyDecision, text: str) -> bool:
        detail = self._chat_detail(decision.chat_id)
        latest = self._latest_message(detail)
        return bool(
            latest
            and message_role(latest) == APPLICANT_ROLE
            and message_text(latest) == text.strip()
        )

    def send_reply(self, decision: ReplyDecision, text: str) -> bool:
        if self.config.dry_run:
            return True
        for attempt in range(self.config.send_retries + 1):
            try:
                self.hh.call_api(
                    f"/negotiations/{decision.chat_id}/messages",
                    method="POST",
                    form_params={"message": text},
                )
                return True
            except HHCLIError as exc:
                if self._message_already_sent(decision, text):
                    logger.info(
                        "Chat %s already contains the intended reply; treating retry as success",
                        decision.chat_id,
                    )
                    return True
                if attempt >= self.config.send_retries:
                    logger.error("Failed to send chat %s: %s", decision.chat_id, exc)
                    return False
                time.sleep(self.config.send_retry_delay)
        return False

    def run(self) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "candidates": 0,
            "planned": 0,
            "sent": 0,
            "stale": 0,
            "skipped": 0,
            "ignored": 0,
            "manual": 0,
            "errors": 0,
            "hot_candidates": 0,
            "hot_leads": 0,
            "hot_notified": 0,
            "hot_notify_failed": 0,
            "hot_ai_errors": 0,
        }
        self._flush_pending_hot_notifications(stats)
        candidates = self.collect_candidate_chats()
        stats["candidates"] = len(candidates)
        for chat in candidates:
            try:
                decision = self.make_decision(chat)
                if decision is None:
                    stats["skipped"] += 1
                    continue
                self._process_hot_lead(decision, stats)
                if decision.action == ACTION_IGNORE:
                    logger.info(
                        "Chat %s classified IGNORE: %s",
                        decision.chat_id,
                        decision.reason,
                    )
                    stats["ignored"] += 1
                    continue
                if decision.action == ACTION_MANUAL:
                    logger.warning(
                        "Chat %s requires manual/browser handling: %s",
                        decision.chat_id,
                        decision.reason,
                    )
                    if self.manual_queue is not None and not self.config.dry_run:
                        self.manual_queue.enqueue(
                            chat_id=decision.chat_id,
                            message_id=decision.expected_last_message_id,
                            message_text=decision.latest_message_text,
                            vacancy_name=decision.vacancy_name,
                            employer_name=decision.employer_name,
                            reason=decision.reason,
                        )
                    stats["manual"] += 1
                    continue
                reply = self.generate_reply(decision)
                if not reply:
                    stats["errors"] += 1
                    continue
                if self.config.dry_run:
                    logger.info("DRY-RUN chat=%s reply=%s", decision.chat_id, reply)
                    stats["planned"] += 1
                    continue
                if not self.is_still_current(decision):
                    logger.info(
                        "Chat %s changed while generating; skip stale reply", decision.chat_id
                    )
                    stats["stale"] += 1
                    continue
                if self.send_reply(decision, reply):
                    stats["sent"] += 1
                else:
                    stats["errors"] += 1
            except (HHCLIError, ValueError) as exc:
                logger.error("Reply worker skipped a chat: %s", exc)
                stats["errors"] += 1
        return stats
