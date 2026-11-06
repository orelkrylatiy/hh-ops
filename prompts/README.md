# 🤖 Промпты генерации

Здесь лежат **системные промпты** для LLM — заготовки для функции «контекст → текст»
(письмо под вакансию, ответ работодателю). Это не «мозг» агента: что и когда запускать,
решает cron, а LLM лишь генерирует текст.

> Подключение модели (ключи, провайдеры) — [docs/LLM_SETUP.md](../docs/LLM_SETUP.md).
> Архитектура — [docs/AGENT_GUIDE.md](../docs/AGENT_GUIDE.md).

## Файлы

```
prompts/
├── cover_letter_frontend.txt   # системный промпт для сопроводительных писем
├── reply_employer.txt          # системный промпт для ответов в чате
└── *.local.txt                 # твои личные версии (в .gitignore, см. ниже)
```

## Как подключать

Промпт можно передать **файлом** (`@file` или просто путь) или инлайн-строкой —
поддерживается в `apply-vacancies` и `reply-employers`:

```bash
# письма
hh-applicant-tool apply-vacancies --ai --system-prompt prompts/cover_letter_frontend.txt ...

# ответы работодателям
hh-applicant-tool reply-employers --use-ai --system-prompt prompts/reply_employer.txt ...

# инлайн тоже работает
hh-applicant-tool reply-employers --use-ai --system-prompt "Отвечай вежливо и кратко."
```

`--prompt` / `--message-prompt` задаёт промпт *генерации*, `--system-prompt` — *системный*.
Оба понимают и путь к файлу, и строку.

## Личные данные (имя, Telegram)

Коммитируемые промпты используют `${HH_NAME}` и `${HH_TELEGRAM}`.
Для скриптов проекта достаточно заполнить `.env` в корне:

```bash
HH_NAME=Максим
HH_TELEGRAM=@maxxwyy
```

Если запускаешь CLI напрямую, переменные тоже должны быть экспортированы в shell.

Контакт в сопроводительных письмах — на аккаунт: `contact.telegram` в
`config.json` профиля (`%APPDATA%\hh-applicant-tool\<профиль>\config.json`),
формат `https://t.me/<handle>`. Приоритет: конфиг профиля → `HH_TELEGRAM` →
`https://t.me/maxxwyy`.

## Стратегия на день (для человека/cron)

| Метрика | Ориентир |
|---|---|
| Откликов в день | 80–120 (лимит HH ~100–150) |
| Поднятие резюме | 1×/сутки, утром |
| Ответы работодателям | все новые за 24ч, через review |
| Паузы между батчами | чтобы не выглядеть спамом |

Конкретное расписание — в `crontab` и [docs/SCHEDULING.md](../docs/SCHEDULING.md).
