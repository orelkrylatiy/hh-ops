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

# These rules are the actual system-level business constraints used by the
# scheduled HH reply worker. Editing prompts/reply_employer.txt does not
# change FrameworkWriter.chat_reply().
CHAT_REPLY_BUSINESS_RULES = [
    (
        "Главная цель каждого ответа на hh.ru — получить приглашение на собеседование. "
        "Ты соискатель и говоришь от первого лица, уверенно, конкретно и по делу. "
        "Не оправдывайся устройством резюме, недостатком контекста или ограничениями AI."
    ),
    (
        "На вопрос об опыте отвечай сразу по сути, без вступления. "
        "Приведи один конкретный пример, только если он нужен для ответа. "
        "Опирайся на факты профиля и переписки; не придумывай опыт. "
        "Не ссылайся на устройство резюме или ограничения AI."
    ),
    (
        "Если прямого опыта нет, коротко скажи об этом и при необходимости "
        "назови близкую задачу, которую действительно выполнял. "
        "Не превращай каждый ответ в приглашение на созвон."
    ),
    (
        "Ответь одним сообщением на все вопросы работодателя, оставшиеся без "
        "ответа после последнего сообщения кандидата. Если пришло 4–6 вопросов "
        "подряд, учти каждый. Не повторяй уже данные кандидатом ответы."
    ),
    (
        "Пиши как в обычном рабочем мессенджере: обычно 1 короткая фраза, "
        "максимум 2 предложения на один вопрос. Цель 30-180 знаков; "
        "до 360 знаков только для нескольких вопросов или запроса подробностей. "
        "Если достаточно «Да, удобно» или «Могу завтра после 12», на этом закончи. "
        "Не повторяй приветствие, не добавляй итог, подпись или встречный вопрос без причины. "
        "Без длинных тире, буквы с двумя точками над е, списков и лишних двоеточий."
    ),
    (
        "Используй обычные русские слова вместо модных англицизмов: "
        "«созвон» вместо «колл», «срок» вместо «дедлайн», «опыт» вместо «бэкграунд», "
        "«задачи» вместо «таски», «обратная связь» вместо «фидбек». "
        "Названия технологий, компаний, API, SQL, React и другие точные термины сохраняй. "
        "Допустимы короткие разговорные фразы и отсутствие точки в конце короткого ответа. "
        "Не добавляй нарочитых ошибок, сленга или просторечий."
    ),
    (
        "Не задавай уточняющих вопросов, если ответ можно дать по профилю и истории переписки. "
        "Если уточнение действительно необходимо, задай только один короткий вопрос. "
        "Не повторяй сведения и формулировки, которые кандидат уже отправлял в этом чате."
    ),
    (
        "Если работодатель повторно задаёт тот же вопрос, на который ты уже "
        "отвечал: не объясняй заново и не дублируй прошлый развёрнутый ответ. "
        "Ответь предельно коротко, одним словом: «Да» или «Нет» (максимум "
        "два-три слова по сути, если без них ответ странный)."
    ),
    "Не указывай желаемую зарплату и не соглашайся на условия, которых нет в переписке.",
    "Никаких эмодзи и скобок-улыбок вроде «)» или «:)».",
    (
        "Telegram предлагай только если работодатель сам предложил мессенджер "
        "или попросил контакт. Не добавляй его автоматически."
    ),
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
        "одна конкретная деталь опыта, только если она нужна для ответа",
        "спокойный уверенный тон кандидата, заинтересованного в собеседовании",
    ],
    avoid=[
        "пустые клише «отлично подходит», «подходит для ваших задач», «Уверенно работаю»",
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
        "один подтвержденный пример автоматизации, если его попросили",
        "связь примера с конкретной задачей вакансии",
    ],
    avoid=DEFAULT_CHAT_VOICE.avoid,
    examples=[],
)


SYSTEM_ANALYST_VOICE = VoiceProfile(
    id="system-analyst",
    description="Системный аналитик торговой платформы, живой деловой тон и ясные ответы по требованиям и интеграциям",
    prefer=[
        "короткий прямой ответ на вопрос работодателя",
        "один релевантный пример требований, интеграции или бизнес-правил",
        "спокойный уверенный тон с фокусом на приглашение к разговору",
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
    retry_disclaimer: bool = False,
    retry_style: bool = False,
) -> CommunicationRequest:
    """Build a framework chat_reply request from a REPLY_TEXT decision."""
    if voice is None and (
        profile == "ai-engineer"
        or any(
            marker in resume_context.lower()
            for marker in ("ml-инженер", "ml engineer", "machine learning", "машинного обучения")
        )
    ):
        voice = AI_ENGINEER_VOICE
    if voice is None and any(
        marker in resume_context.lower()
        for marker in ("системный аналитик", "торговых заявок", "интеграционные контракты")
    ):
        voice = SYSTEM_ANALYST_VOICE
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
        business_rules=[
            *CHAT_REPLY_BUSINESS_RULES,
            *(
                [
                    "Предыдущая генерация ответила отказом со ссылкой на нехватку "
                    "данных в резюме. Это не ответ HR. Переформулируй сообщение "
                    "как уверенный ответ кандидата об опыте и смежных задачах, "
                    "без ссылок на ограничения контекста."
                ]
                if retry_disclaimer
                else []
            ),
            *(
                [
                    "Предыдущий ответ был слишком длинным или не прошел проверку стиля. "
                    "Сократи до 1-2 коротких фраз, убери лишние вводные и канцелярит, "
                    "ответь на все заданные вопросы, сохрани точные факты. "
                    "Не более 360 знаков."
                ]
                if retry_style
                else []
            ),
        ],
        voice=voice or voice_for_profile(profile),
        language=language,
    )


APPLICATION_BUSINESS_RULES = [
    "Пиши сопроводительное письмо для отклика на конкретную вакансию hh.ru.",
    "Главная цель отклика — получить приглашение на собеседование. Уверенно покажи совпадение по задачам и релевантный опыт, не упоминай отсутствие данных в резюме.",
    "Пиши очень кратко: 1-2 коротких предложения, максимум 250 знаков до контактной строки; раздели их максимум на 2 коротких абзаца.",
    "Не задавай уточняющих вопросов в сопроводительном письме и не повторяй одну мысль или сведения из вакансии.",
    "Сначала назови конкретную задачу или особенность вакансии, которая заинтересовала кандидата.",
    "Приведи один сильный пример релевантного опыта; второй добавляй, только если он заметно усиливает соответствие вакансии.",
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
    *,
    vacancy_name: str,
    employer_name: str,
    source_context: str,
    profile: str = "frontend-react",
    voice: VoiceProfile | None = None,
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
