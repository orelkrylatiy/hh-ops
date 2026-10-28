# Подключение LLM

LLM в этом проекте не управляет расписанием и не решает, какие команды запускать. Она получает уже подготовленный контекст и генерирует текст для конкретной операции.

## Где Используется AI

| Задача | Конфиг | Runtime |
|---|---|---|
| сопроводительные письма | `openai_cover_letter` | `apply-vacancies --ai` / `scripts/apply.sh` |
| ответы работодателям | `openai_reply`, fallback `openai_cover_letter` | `scripts/reply.sh` |
| AI-фильтр вакансий | `openai_vacancy_filter` | `apply-vacancies --ai-filter ...` |
| капча | `openai_captcha` | browser authorization / apply captcha flow |

Scheduled reply worker использует общий `ChatOpenAI`, то есть те же timeout, rate limit и transient retries, что и основной Python-код.

## Конфигурация

`config.json` хранится отдельно для каждого профиля. В Docker базовая директория обычно `/app/config`.

Минимальный пример:

```json
{
  "openai_cover_letter": {
    "api_key": "...",
    "base_url": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "temperature": 0.35,
    "max_completion_tokens": 600,
    "rate_limit": 30,
    "timeout": 45
  },
  "openai_reply": {
    "api_key": "...",
    "base_url": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "temperature": 0.35,
    "max_completion_tokens": 500,
    "rate_limit": 30,
    "timeout": 45,
    "max_retries": 3
  }
}
```

`base_url` должен быть полным OpenAI-compatible chat-completions endpoint.

## Генерация Ответов

Живой ответ всегда генерирует humanizer-framework (`FrameworkWriter.chat_reply`):
план, валидация и repair происходят внутри фреймворка, качество дополнительно
проверяет `reply_quality_issues`. Забракованный или провалившийся ответ — это
`None`: чат пропускается, статических шаблонных подстановок нет.

## Проверка Конфига

Статическая проверка, без отправки данных модели:

```bash
python scripts/check_ai.py --purpose cover-letter
python scripts/check_ai.py --purpose reply
```

Проверка конкретного профиля:

```bash
python scripts/check_ai.py --purpose reply --profile account2
```

Реальный probe одним коротким запросом:

```bash
python scripts/check_ai.py --purpose reply --probe
```

`--probe` удобно запускать вручную после изменения provider/model. Его не нужно выполнять каждый час из cron.

## OpenAI / OpenRouter

Пример OpenAI:

```json
{
  "api_key": "...",
  "base_url": "https://api.openai.com/v1/chat/completions",
  "model": "gpt-4o-mini"
}
```

Пример OpenRouter:

```json
{
  "api_key": "...",
  "base_url": "https://openrouter.ai/api/v1/chat/completions",
  "model": "openai/gpt-4o-mini"
}
```

## Ollama

Проект ожидает OpenAI-compatible interface Ollama:

```bash
ollama serve
ollama pull qwen2.5:14b
```

```json
{
  "api_key": "ollama",
  "base_url": "http://localhost:11434/v1/chat/completions",
  "model": "qwen2.5:14b"
}
```

Нативный `/api/generate` имеет другой формат payload и для этого клиента не подходит.

Если приложение работает в Docker, `localhost` внутри контейнера означает сам контейнер. Если Ollama запущена на host-машине, укажи сетевой адрес, доступный контейнеру, либо запусти провайдер в общей Docker network.

## Промпты И Humanizer

Основные шаблоны:

- `prompts/cover_letter_frontend.txt`
- `prompts/reply_employer.txt`

Они запрещают длинные тире, placeholder'ы, канцелярит, типовые AI-переходы и излишне гладкий рекламный стиль.

Для autonomous replies prompt не является единственной защитой. `src/hh_applicant_tool/automation/reply_worker.py` дополнительно валидирует полученный текст. Если ответ содержит длинное тире, placeholder или явное AI-клише, worker делает одну corrective generation. Вторая неудача означает `skip`, а не отправку плохого сообщения.

Telegram больше не приписывается кодом в каждый ответ. Prompt разрешает его только когда это действительно следует из контекста.

## Privacy Dry-run

`reply.sh --dry-run` не отправляет историю переписки LLM-провайдеру. Он показывает deterministic preview и проверяет логику выбора чатов без утечки содержимого чатов внешней модели.

Live mode, естественно, передаёт текст последних сообщений выбранному LLM provider для генерации ответа.

## Диагностика

```bash
# AI config
python scripts/check_ai.py --purpose reply

# реальный provider probe
python scripts/check_ai.py --purpose reply --probe

# чат без отправки и без LLM
./scripts/reply.sh --dry-run --chats 20

# отклики без отправки (AI письмо генерируется)
./scripts/apply.sh --dry-run --limit 10 --pages 2
```

Если генерация упала или текст забракован, worker пропускает чат и run получает ошибку, как раньше. В JSON-статистике `scripts/reply_iterative_ai.py` поле `fallback` осталось как исторический счётчик (всегда 0).
