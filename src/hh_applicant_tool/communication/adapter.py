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

# Fallback writing style only; it must never substitute for selected-resume facts.
# TODO(HH-VOICE-01): FrameworkWriter currently passes neither the selected
# profile nor a personalized VoiceProfile. The frontend-oriented default
# therefore applies to both applications and chat replies for all resumes.
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

AI_ENGINEER_VOICE = VoiceProfile(
    id="maxim-ai-engineer",
    description="AI/LLM automation engineer, живой деловой тон, конкретные технические примеры",
    prefer=[
        "простые фразы без пересказа всего стека",
        "один-два подтвержденных примера автоматизации или LLM-интеграций",
        "связь примера с конкретной задачей вакансии",
    ],
    avoid=DEFAULT_CHAT_VOICE.avoid,
    examples=[],
)


def voice_for_profile(profile: str) -> VoiceProfile:
    """Select writing voice without using it as a source of candidate facts."""
    if profile == "ai-engineer":
        return AI_ENGINEER_VOICE
    return DEFAULT_CHAT_VOICE


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
    resume_context: str = "",
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
            "candidate_profile": resume_context,
        },
        business_rules=CHAT_REPLY_BUSINESS_RULES,
        voice=voice or DEFAULT_CHAT_VOICE,
        language=language,
    )


APPLICATION_BUSINESS_RULES = [
    "Пиши сопроводительное письмо для отклика на конкретную вакансию hh.ru.",
    "Используй только подтвержденные факты кандидата из context. Не выдумывай опыт, проекты и достижения.",
    "Напиши 4-6 предложений объемом примерно 450-700 знаков и раздели их на 2-3 коротких смысловых абзаца.",
    "Сначала назови конкретную задачу или особенность вакансии, которая заинтересовала кандидата.",
    "Приведи один подтвержденный пример релевантного опыта; второй используй только если он заметно усиливает письмо.",
    "Свяжи опыт с одной конкретной задачей вакансии, без абстрактного вывода о пользе для компании.",
    "Не добавляй отдельную финальную фразу с контактом или предложением связаться: система добавит ее после проверки текста.",
    "Начни с короткого приветствия. Не обращайся к работодателю именем кандидата.",
    "Верни только текст письма, без заголовка, подписи-заглушки и контактных данных, которых нет в context.",
    "Не утверждай, что изучил компанию, ее продукт или процессы, если таких сведений нет в context.",
    "Не используй абстрактные тройные перечисления, канцелярит и очевидные выводы за работодателя.",
    "Строго не используй эмодзи, смайлики, конструкции вроде :), :-) или ;), а также отдельную закрывающую скобку ) без парной открывающей скобки.",
    "Перед возвратом проверь текст на запрещенные смайлики и убери их; обычные скобки в паре допустимы.",
]


def build_application_request(
    *, vacancy_name: str, employer_name: str, source_context: str,
    profile: str = "frontend-react", voice: VoiceProfile | None = None,
) -> CommunicationRequest:
    return job_search_request(
        channel="hh",
        message_type="application",
        profile=profile,
        conversation=[],
        context={
            "vacancy": {"name": vacancy_name},
            "employer": {"name": employer_name},
            "source_context": source_context,
        },
        business_rules=APPLICATION_BUSINESS_RULES,
        voice=voice or voice_for_profile(profile),
        language="ru",
    )

