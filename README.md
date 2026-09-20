# Work Optimization / HH Applicant Tool

Automation worker для работы соискателя на HH.ru: поиск и фильтрация вакансий, ограниченные batch-отклики, AI-сопроводительные, безопасные автоответы работодателям, multi-profile, локальное состояние и cron/Docker deployment.

Проект построен как детерминированный worker, а не как свободно действующий LLM-агент:

```text
cron / manual command
        ↓
scripts/*.sh
        ↓
hh-applicant-tool CLI / ReplyWorker
        ↓
HH API + локальное состояние
        ↓
LLM только там, где нужен текст
```

Подробная схема с Mermaid-диаграммами и привязкой к файлам кода: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Для ежедневного цикла MCP не требуется. Скрипты уже дают стабильную command surface для cron, CI и внешнего агента.

## Что Автоматизировано

- отклики на вакансии через `apply-vacancies`;
- AI-сопроводительные письма;
- пропуск вакансий с тестовыми заданиями в autonomous path;
- лимит именно по успешным откликам, а не по числу просмотренных вакансий;
- автоответы через applicant `/negotiations` chat API с повторной проверкой истории;
- rule-based chat classifier: `REPLY_TEXT / IGNORE / MANUAL`;
- SQLite-очередь ручных/button-flow чатов;
- защита от дублей через re-read после сомнительного POST;
- configurable runtime fallback-сообщение при недоступности LLM в reply path;
- multi-profile с bounded concurrency до 10 профилей по умолчанию и per-profile locks;
- ежедневный cron batch откликов и почасовые проверки чатов;
- Docker + web admin panel;
- SQLite/локальное состояние профилей;
- blocking CI для нового automation layer, тесты, Ruff, formatter, basedpyright, ShellCheck и Docker build.

## Безопасность Автономного Режима

Все scheduled jobs выключены после установки:

```dotenv
HH_AUTOMATION_MODE=off
```

Режимы:

```text
off      cron ничего не делает
dry-run  читает данные и строит preview без внешних действий
live     разрешает реальные отклики, ответы, cleanup и boost
```

Не переключай в `live`, пока не прошли AI probe и ручные dry-run.

## Быстрый Старт

Требуется Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[playwright,pillow]'
pip install -r admin/requirements.txt
cp .env.example .env
```

Для browser-авторизации:

```bash
python -m playwright install chromium
```

Проверка CLI:

```bash
hh-applicant-tool --help
hh-applicant-tool whoami
hh-applicant-tool list-resumes
```

Если аккаунт ещё не авторизован:

```bash
hh-applicant-tool --profile-id default authorize '<phone-or-email>'
hh-applicant-tool --profile-id default whoami
```

Первичная авторизация может потребовать человека: код подтверждения и иногда капчу. После этого токены/cookies сохраняются в профиле.

## Профили И Данные

В директории каждого профиля находятся:

- `config.json` — токены и настройки;
- `data` — SQLite;
- `cookies.txt` — web cookies;
- `log.txt` — CLI log.

Для нескольких аккаунтов создай `.profiles`:

```text
default
account2
```

Проверка:

```bash
hh-applicant-tool --profile-id default whoami
hh-applicant-tool --profile-id account2 whoami
```

Batch по всем профилям:

```bash
./scripts/all-profiles.sh apply --dry-run
./scripts/all-profiles.sh reply --dry-run
```

Параллелизм ограничен и по умолчанию рассчитан на десять аккаунтов:

```dotenv
HH_PROFILE_PARALLELISM=10
```

Для каждого профиля используется отдельный `flock`. Разные аккаунты могут работать параллельно; конфликтующая операция для уже занятого профиля будет skipped.

## AI Конфигурация

Для live-откликов нужен `openai_cover_letter`.

Для reply provider selection порядок такой:

```text
openai_reply -> openai_cover_letter -> configuration error / STOP
```

После выбора provider есть отдельный runtime fallback: если `ChatOpenAI` исчерпал network/provider retries и бросил `OpenAIError`, `FallbackChatAI` может вернуть статическое `reply_fallback.message`. Это не fallback для содержательно плохого ответа модели: такой текст по-прежнему проходит humanizer и corrective generation.

Пример `config.json` профиля:

```json
{
  "openai_cover_letter": {
    "api_key": "...",
    "base_url": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "temperature": 0.35,
    "timeout": 45
  },
  "openai_reply": {
    "api_key": "...",
    "base_url": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "temperature": 0.35,
    "timeout": 45,
    "max_retries": 3
  },
  "reply_fallback": {
    "enabled": true,
    "message": "Здравствуйте! Спасибо за сообщение. Я разработчик, вакансия мне интересна. Готов обсудить задачи, формат работы и ответить на вопросы."
  }
}
```

Подойдёт любой OpenAI-compatible provider, включая OpenRouter и OpenAI-compatible режим Ollama.

Static preflight:

```bash
python scripts/check_ai.py --purpose cover-letter --profile default
python scripts/check_ai.py --purpose reply --profile default
```

Реальный model probe:

```bash
python scripts/check_ai.py --purpose cover-letter --profile default --probe
python scripts/check_ai.py --purpose reply --profile default --probe
```

Подробности: [docs/LLM_SETUP.md](docs/LLM_SETUP.md).

## Отклики

Preview:

```bash
./scripts/apply.sh \
  --profile default \
  --search 'React TypeScript developer' \
  --limit 20 \
  --pages 5 \
  --dry-run
```

Live:

```bash
./scripts/apply.sh \
  --profile default \
  --search 'React TypeScript developer' \
  --limit 20 \
  --pages 5 \
  --live
```

Ключевое различие:

- `--limit` — максимум **успешных** откликов;
- `--pages × --per-page` — максимальная глубина сканирования.

Поэтому worker может пройти значительно больше 20 вакансий, чтобы реально набрать 20 подходящих откликов после фильтрации и пропуска уже обработанных вакансий.

По умолчанию autonomous path использует `--skip-tests`: бот не должен угадывать ответы на тестовые задания.

Весь batch дополнительно ограничен `APPLY_RUN_TIMEOUT` (default 3600 секунд), чтобы зависший request не удерживал profile worker бесконечно.

Для cover letters static fallback не используется: `AIError` пропускает конкретную vacancy и помечает run как неуспешный.

## Создание Резюме Из Шаблона

В репозитории есть пример `docs/resume_template.md`. Перед реальным созданием
рекомендуется всегда смотреть финальный HH payload:

```bash
hh-applicant-tool --profile-id default create-resume docs/resume_template.md --dry-run
```

Реальное создание:

```bash
hh-applicant-tool --profile-id default create-resume docs/resume_template.md
```

Создание с последующей публикацией:

```bash
hh-applicant-tool --profile-id default create-resume docs/resume_template.md --publish
```

Поддерживаются `.md`, `.markdown` и `.toml`. `--dry-run` может делать
только read-only запросы к HH suggestions/directories; `POST /resumes` и
publish в этом режиме не вызываются. Неоднозначные справочники, невалидные даты,
контакты, enum-значения и отрасли останавливают операцию до создания резюме.

Если HH после `POST /resumes` не вернул ID, команда определяет новый ID по
списку резюме. При нескольких одновременно появившихся ID `--publish`
fail-closed и ничего автоматически не публикует.

## Варианты Резюме И Profile-specific Apply Lanes

Для нескольких специализаций используются profile-local aliases:

```json
{
  "resume_aliases": {
    "primary": "<frontend resume id>",
    "ai-engineer": "<ai resume id>"
  }
}
```

Alias хранится только в runtime `config/<profile>/config.json`; реальные HH
resume IDs и персональные данные в Git не коммитятся.

Новый вариант можно собрать из существующего HH-резюме, сохранив личные данные,
образование, контакты и локацию из source resume:

```bash
./scripts/setup-ai-resume.sh --profile 0555 --dry-run
./scripts/setup-ai-resume.sh --profile 0555 --live
```

Для `0555` tracked variant находится в
`resumes/variants/0555-ai-engineer.toml`. Он позиционирует профиль как
`AI Engineer / LLM Automation Engineer` на основе собственных AI/automation
проектов и не подменяет предыдущую коммерческую компанию вымышленным AI-опытом.

Scheduled apply проходит через `scripts/apply-profile.sh`. Если для профиля нет
`rules/apply-lanes/<profile>.json`, выполняется ровно старый одиночный
`apply.sh`. Для `0555` после появления alias `ai-engineer` включаются
отдельные lanes:

- frontend -> только `primary`;
- `AI Engineer` -> только `ai-engineer`;
- `LLM Engineer` -> только `ai-engineer`;
- `AI автоматизация` -> только `ai-engineer`.

Пока alias `ai-engineer` отсутствует, AI lanes fail-safe пропускаются. Для
frontend lane alias `primary` может быть временно выведен только когда в
аккаунте ровно одно опубликованное резюме. Если опубликованных резюме уже
несколько, но alias не сохранён, apply останавливается fail-closed и не смешивает
воронки.

## Автоответы В Чатах

Preview без отправки и без вызова LLM:

```bash
./scripts/reply.sh --profile default --chats 20 --dry-run
```

Live:

```bash
./scripts/reply.sh --profile default --chats 20 --live
```

Worker использует applicant negotiation flow:

```text
GET /negotiations
GET /negotiations/{id}/messages
classifier
  ├─ IGNORE
  ├─ MANUAL -> SQLite manual_chat_queue
  └─ REPLY_TEXT -> LLM / runtime fallback
humanizer
GET /negotiations/{id}/messages   # revalidate exact employer turn
POST /negotiations/{id}/messages
```

Classifier не тратит LLM-вызов на очевидные состояния. Служебные HH-уведомления
и простые acknowledgement без вопроса получают `IGNORE`. Явные button/UI hints
получают `MANUAL`. Если тот же employer-вопрос повторился после нашего
текстового ответа, это считается сильным признаком, что бот ждал кнопку или
структурированное действие, и чат тоже уходит в `MANUAL`.

Обычный `REPLY_TEXT` проходит существующий LLM/humanizer pipeline. Перед POST
worker перечитывает историю и отправляет ответ только если тот же employer
message всё ещё последний. После сетевой ошибки worker снова читает чат и не
повторяет POST, если предполагаемый ответ уже появился.

Pending manual cases можно посмотреть:

```bash
hh-applicant-tool --profile-id default manual-chats
```

После ручного браузерного ответа:

```bash
hh-applicant-tool --profile-id default manual-chats --resolve CHAT_ID
```

## Humanizer

Шаблоны:

- `prompts/cover_letter_frontend.txt`;
- `prompts/reply_employer.txt`.

Они запрещают длинные тире, placeholder'ы, канцелярит, типовые AI-клише и слишком гладкий рекламный стиль. Для autonomous replies действует ещё runtime validator: плохой ответ отклоняется, модель получает одну попытку исправления, после повторной неудачи сообщение пропускается.

Static reply fallback проходит тот же runtime validator до отправки.

Telegram не дописывается программно в каждый ответ. Он используется только когда это уместно по истории диалога.

## Расписание

Container `crontab` по умолчанию:

| Время | Действие |
|---|---|
| 09:00 | boost резюме, только `live` |
| 09:10 | один application batch |
| каждый час 09:25–21:25 | один bounded pass по чатам |
| 22:10 | очистка rejected/discard переговоров |

Время берётся из timezone контейнера/сервера (`TZ`).

Пример `.env`:

```dotenv
TZ=Europe/Moscow
HH_AUTOMATION_MODE=dry-run
SEARCH_QUERY=Frontend разработчик
APPLY_LIMIT=100
APPLY_PER_PAGE=50
APPLY_PAGES=20
APPLY_RUN_TIMEOUT=3600
REPLY_CHATS=100
CLEANUP_DELETE_CHAT=1
HH_PROFILE_PARALLELISM=10
```

После проверки:

```dotenv
HH_AUTOMATION_MODE=live
```

Для установки аналогичного cron вне Docker:

```bash
./scripts/setup-cron.sh
```

## Очистка Rejected Переговоров

Preview:

```bash
./scripts/cleanup.sh --profile default --dry-run
```

Live:

```bash
./scripts/cleanup.sh --profile default --live
```

По умолчанию очищаются только переговоры со state=`discard`. При
`CLEANUP_DELETE_CHAT=1` соответствующий web-chat также скрывается. Cleanup не
blacklist'ит работодателя и не использует ATS-эвристику.

## Docker

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

`docker-compose.yml` передаёт scheduler knobs в container environment. `container-entrypoint.sh` через `scripts/write-runtime-env.sh` сохраняет их в `/tmp/hh-runtime.env`, потому что cron запускается с урезанным environment. Поэтому кастомные `SEARCH_QUERY`, `APPLY_*`, `REPLY_CHATS`, `CLEANUP_DELETE_CHAT` и `HH_PROFILE_PARALLELISM` работают и в scheduled jobs.

Admin panel публикуется только на localhost:

```text
127.0.0.1:8000
```

Если нужен внешний доступ, лучше проксировать через HTTPS и обязательно настроить:

```dotenv
ADMIN_USERNAME=...
ADMIN_PASSWORD=...
```

## Ручной One-shot Workflow

```bash
# Полный preview
./scripts/daily.sh --profile default --dry-run

# Один live pass: apply + reply
./scripts/daily.sh --profile default --live

# Только отклики
./scripts/daily.sh --profile default --apply-only --dry-run

# Только чаты
./scripts/daily.sh --profile default --reply-only --dry-run

# Boost не выполняется автоматически этим one-shot без отдельного подтверждения
./scripts/daily.sh --profile default --live --with-boost
```

Scheduled production path использует не `daily.sh`, а отдельные `cron-job.sh apply/reply/boost`, чтобы падение одного типа работы не смешивалось с другим.

## Проверки Разработки

Blocking CI проверяет новый automation layer и весь test suite. Локально:

```bash
pytest tests/
ruff check src/hh_applicant_tool/automation scripts/reply_iterative_ai.py scripts/check_ai.py
ruff format --check src/hh_applicant_tool/automation scripts/reply_iterative_ai.py scripts/check_ai.py
basedpyright src/hh_applicant_tool/automation
shellcheck scripts/apply.sh scripts/reply.sh scripts/cron-job.sh scripts/daily.sh scripts/all-profiles.sh scripts/setup-cron.sh scripts/write-runtime-env.sh container-entrypoint.sh
```

CI также smoke-тестирует shell-safe cron runtime env и параллельный запуск десяти профилей.

Полный upstream код содержит legacy lint/type debt; CI отдельно показывает его как report-only, не маскируя ошибки в новом critical automation path.

## Что Не Делать Автономно

- не включать решение vacancy tests;
- не менять search/filters сразу в live без preview;
- не запускать одновременно несколько scheduler'ов для одной установки;
- не копировать token/cookies между профилями;
- не обходить `HH_AUTOMATION_MODE` и per-profile locks внешним параллельным cron;
- не выставлять admin panel в интернет без auth/TLS.

## Документация

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — подробная code-grounded архитектура и Mermaid-схемы;
- [docs/AUTONOMOUS_AGENT_WORKFLOW.md](docs/AUTONOMOUS_AGENT_WORKFLOW.md) — production automation и safety model;
- [docs/LLM_SETUP.md](docs/LLM_SETUP.md) — LLM config/fallbacks/probe;
- [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md) — CLI и agent-oriented use cases;
- [docs/SCHEDULING.md](docs/SCHEDULING.md) — дополнительные варианты запуска;
- [docs/development/TESTING.md](docs/development/TESTING.md) — тестирование;
- [docs/PRODUCTION_REVIEW_2026-09-04.md](docs/PRODUCTION_REVIEW_2026-09-04.md) — результаты hardening review.
