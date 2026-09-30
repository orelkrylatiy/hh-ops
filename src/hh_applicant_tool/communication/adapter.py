"""Mapping hh-ops reply state onto the humanizer-framework request model.

The framework owns wording policy; hh-ops keeps vacancy discovery, chat
classification and send safety. See
humanizer-framework/docs/TEMP_PROJECT_INTEGRATION_PLAN.md, section 9.
"""

from __future__ import annotations

from humanizer_framework.models import CommunicationRequest, Message, VoiceProfile
from humanizer_framework.presets import job_search_request

APPLICANT_PREFIX = "Я: "
EMPLOYER_PREFIX = "Работодатель: "

CHAT_REPLY_BUSINESS_RULES = [
    "Используй только факты кандидата из context. Не выдумывай опыт, проекты, цифры, технологии.",
    (
        "Это ответ работодателю в чате на hh.ru. Ответь на его последнее сообщение по делу, "
        "без повторного питча, без пересказа резюме и без повторных вопросов, на которые "
        "кандидат уже отвечал."
    ),
    "Не указывай желаемую зарплату и не соглашайся на условия, которых нет в переписке.",
    (
        "Никаких смайликов, эмодзи и скобок-улыбок вроде «)» или «:)» — это деловая переписка "
        "с работодателем."
    ),
    (
        "Telegram упоминай только если работодатель сам предлагает мессенджер или просит контакт; "
        "иначе не добавляй."
    ),
    "Если данных для ответа не хватает, задай один короткий уточняющий вопрос вместо выдумки.",
]

DEFAULT_CHAT_VOICE = VoiceProfile(
    id="maxim-frontend",
    description="Фронтенд-инженер, живой деловой тон, короткие фразы разной длины",
    prefer=[
        "простые слова и предложения разной длины",
        "одна-две конкретные детали опыта, релевантные именно этому вопросу",
        "спокойный деловой тон без продаж",
    ],
    avoid=[
        "клише «Готов обсудить», «отлично подходит», «подходит для ваших задач», «Уверенно работаю»",
        "канцелярит: «данный», «осуществляю», «обладаю навыками»",
        "конструкции «X, что повышало/снижало/ускоряло Y»",
        "риторические тройки и искусственные списки из трёх пунктов",
        "длинное тире «—», эмодзи, пафосные итоговые выводы",
    ],
    examples=[],
)


def conversation_from_context(context: list[str]) -> list[Message]:
    """Turn ReplyDecision.context lines ("Я: ...", "Работодатель: ...") into framework messages."""
    messages: list[Message] = []
    for line in context:
        if line.startswith(APPLICANT_PREFIX):
            messages.append(Message("assistant", line[len(APPLICANT_PREFIX) :]))
        elif line.startswith(EMPLOYER_PREFIX):
            messages.append(Message("user", line[len(EMPLOYER_PREFIX) :]))
    return messages


def build_chat_reply_request(
    *,
    context: list[str],
    initiated_by_us: bool,
    vacancy_name: str,
    employer_name: str,
    latest_message_text: str,
    profile: str = "frontend-react",
    voice: VoiceProfile | None = None,
    language: str = "ru",
) -> CommunicationRequest:
    """Build a framework chat_reply request from a REPLY_TEXT decision."""
    return job_search_request(
        channel="hh",
        message_type="chat_reply",
        profile=profile,
        conversation=conversation_from_context(context),
        context={
            "vacancy": {"name": vacancy_name},
            "employer": {"name": employer_name},
            "latest_employer_message": latest_message_text,
            "initiated_by_candidate": initiated_by_us,
        },
        business_rules=CHAT_REPLY_BUSINESS_RULES,
        voice=voice or DEFAULT_CHAT_VOICE,
        language=language,
    )


class ChatCompleteProvider:
    """Adapt a single-prompt ChatOpenAI-style client to the framework Provider protocol.

    Framework package.messages contains several system entries plus bounded
    history; single-prompt clients take one string, so system entries are
    joined and the rest is passed as labelled turns. The wrapped client must
    be built without its own system prompt.
    """

    name = "hh-chat-openai"

    def __init__(self, complete) -> None:
        self._complete = complete

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 300,
    ) -> str:
        del temperature, max_tokens
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        rest = [m for m in messages if m["role"] != "system"]
        user = "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in rest)
        return self._complete(f"{system}\n\n{user}".strip())
