# Автономный Workflow

Проект не требует отдельного LLM-агента для ежедневной работы. Оркестрация детерминирована:

```text
cron -> cron-job.sh -> all-profiles.sh -> apply.sh / reply.sh
                                      -> hh-applicant-tool / ReplyWorker
                                      -> HH API
                                      -> LLM только для текста
```

Подробная архитектура по файлам и классам: [ARCHITECTURE.md](ARCHITECTURE.md).

LLM не решает, когда запускать отклики, какой профиль брать, можно ли писать в чат и можно ли отправлять stale reply. Эти решения принимает обычный код.

## Режимы

Все scheduled jobs управляются одной переменной:

```dotenv
HH_AUTOMATION_MODE=off
```

Допустимые значения:

- `off` — cron ничего не делает. Это default;
- `dry-run` — scheduled jobs читают данные и строят preview без внешних write;
- `live` — разрешены реальные отклики, ответы и boost резюме.

После первого deploy сначала оставь `off`, затем проверь ручные dry-run и AI probe, и только после этого переключай в `live`.

## Расписание По Умолчанию

Контейнерный `crontab` использует timezone контейнера/сервера (`TZ`):

| Время | Job | Что делает |
|---|---|---|
| 09:00 | `boost` | поднимает опубликованные резюме, только в live |
| 09:10 | `apply` | один batch откликов до `APPLY_LIMIT` |
| каждый час 09:25–21:25 | `reply` | bounded pass по чатам, где последнее сообщение от работодателя |

Отдельный cron для refresh-token не нужен: `ApiClient` умеет обновлять access token во время authenticated request, а CLI сохраняет обновившийся token после операции.

## Container Runtime Environment

Cron запускается с урезанным environment. Поэтому `container-entrypoint.sh` вызывает `scripts/write-runtime-env.sh`, который shell-safe сохраняет scheduler knobs в `/tmp/hh-runtime.env`.

Scheduled jobs получают, среди прочего:

```text
HH_AUTOMATION_MODE
CONFIG_DIR
TZ
HH_NAME
HH_TELEGRAM
SEARCH_QUERY
APPLY_LIMIT
APPLY_PER_PAGE
APPLY_PAGES
APPLY_RUN_TIMEOUT
REPLY_CHATS
HH_PROFILE_PARALLELISM
```

Это позволяет задавать значения через `docker-compose.yml` / `.env` и реально использовать их внутри cron job.

## Как Работают Отклики

`scripts/apply.sh` разделяет две величины:

- `APPLY_LIMIT` — максимум успешных откликов за запуск;
- `APPLY_PAGES * APPLY_PER_PAGE` — максимальная глубина поиска.

По умолчанию:

```text
APPLY_LIMIT=100
APPLY_PAGES=20
APPLY_PER_PAGE=50
```

То есть worker может просмотреть до 1000 вакансий и закончить раньше, когда реально достигнут лимит успешных откликов.

Live отклики требуют рабочий `openai_cover_letter`. Preflight:

```bash
python scripts/check_ai.py --purpose cover-letter
```

Autonomous path использует `--skip-tests`: вакансии с тестовыми заданиями не решаются автоматически.

При `AIError` во время cover-letter generation конкретная vacancy пропускается, `ai_error_count` увеличивается, а run в конце помечается неуспешным. Static fallback для массовых cover letters не используется.

## Как Работают Автоответы

Primary path использует applicant negotiations API:

```text
GET /negotiations
GET /negotiations/{id}/messages
classifier
POST /negotiations/{id}/messages
```

До LLM worker классифицирует employer turn. `IGNORE` используется для
служебных HH-сообщений и простых acknowledgement без вопроса. `MANUAL`
используется для явных UI/button hints и для повторившегося employer-вопроса
после нашего текстового ответа. Такие cases пишутся в SQLite
`manual_chat_queue` и не получают автоматический текстовый ответ.

`REPLY_TEXT` проходит LLM/humanizer. Перед POST worker повторно читает чат и
сверяет exact message id. Если человек уже ответил вручную или пришло новое
сообщение, подготовленный ответ считается stale.

Negotiations endpoint не имеет server-side idempotency key, поэтому после
сомнительного POST worker перечитывает историю и не повторяет отправку, если
ожидаемый applicant text уже появился.

## AI Fallback Для Reply

Здесь есть два разных механизма fallback.

### 1. Выбор provider config

```text
openai_reply
    ↓ если секции нет
openai_cover_letter
    ↓ если валидной секции нет
configuration error / STOP
```

Live worker не подставляет скрытый URL/model/API key.

### 2. Runtime fallback сообщения

После выбора provider `ChatOpenAI` выполняет свои network/provider retries. Если они исчерпаны и `complete()` бросает `OpenAIError`, `FallbackChatAI` может вернуть статический `reply_fallback.message`.

Default fallback включён. Его можно переопределить или выключить в `config.json` профиля:

```json
{
  "reply_fallback": {
    "enabled": true,
    "message": "Здравствуйте! Вакансия интересна. Готов обсудить детали и ответить на вопросы."
  }
}
```

Fallback используется именно при runtime LLM failure. Если модель вернула плохой текст, он идёт через humanizer/corrective-generation и при повторной неудаче пропускается.

## ChatOpenAI Retry Policy

`src/hh_applicant_tool/ai/openai.py` повторяет transient failures:

- network errors;
- HTTP 408;
- HTTP 409;
- HTTP 425;
- HTTP 429;
- HTTP 5xx.

`Retry-After` учитывается. Невалидный JSON, provider error или сломанный response shape преобразуются в `OpenAIError`.

## Humanizer

Humanizer состоит из двух слоёв:

1. prompt rules в `prompts/cover_letter_frontend.txt` и `prompts/reply_employer.txt`;
2. runtime validator для autonomous replies в `automation/reply_worker.py`.

Reply validator отклоняет:

- пустые ответы;
- текст > 2000 символов;
- placeholder'ы;
- длинные тире;
- несколько характерных AI-клише.

При нарушении обычный LLM reply получает одну corrective generation. Если результат снова плохой, сообщение не отправляется.

Static reply fallback тоже обязан проходить те же quality checks.

## Multi-profile И Concurrency

`.profiles` содержит по одному profile id на строку. `all-profiles.sh` запускает профили параллельно.

Default:

```dotenv
HH_PROFILE_PARALLELISM=10
```

Для каждого профиля используется отдельный `flock`. Поэтому:

- два conflicting job не работают одновременно с одним и тем же HH-профилем;
- разные профили не блокируют друг друга;
- crash/OOM/SIGKILL автоматически освобождает lock через закрытие file descriptor.

**Глобального fleet lock в `cron-job.sh` нет.** Это намеренно: один занятый аккаунт не должен останавливать остальные.

## Fail-closed / Fail-safe Правила

Live worker прекращает или пропускает действие при:

- отсутствии HH authorization;
- невалидной AI-конфигурации;
- недоступности HH API;
- невозможности писать в чат;
- изменившемся последнем сообщении;
- плохом LLM reply после corrective retry;
- ошибке отправки после retries/read-back.

Исключение из полного fail-closed поведения — специально настроенный **reply runtime fallback** после `OpenAIError`. Он не обходит stale-check, humanizer или idempotency.

## Dry-run

### Reply

`reply.sh --dry-run`:

- читает HH chats;
- строит decisions;
- не вызывает LLM;
- не отправляет сообщения;
- выводит deterministic preview.

### Apply

`apply.sh --dry-run` проходит selection/filtering flow, но external writes блокируются. Потенциальный отклик моделируется как accepted, чтобы quota logic в preview совпадала с live flow.

## MCP

MCP для ежедневного автономного цикла сейчас не нужен. Скрипты уже являются стабильным command surface для cron и внешнего агента.

MCP имеет смысл добавить позже, если Claude/Codex должен интерактивно вызывать typed operations вроде `scan`, `apply`, `get_chats`, `reply` и получать структурированные результаты. Для production scheduler это дополнительный необязательный слой.
