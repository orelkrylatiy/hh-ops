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
- генерация исходящих текстов через Humanizer Framework с пропуском неудачных ответов;
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

Live-сопроводительные письма и ответы в чатах вызывают
`CommunicationFramework()` без переданного провайдера. Фреймворк сам выбирает
доступный механизм генерации. Если он не сгенерировал подходящий текст,
сообщение не отправляется. Конфигурация `openai_cover_letter` остаётся для
необязательного старого пути ответов на вопросы анкеты вакансии, когда
`--skip-tests` не указан.

Проверка реальной генерации без отправки работодателям:

```bash
python scripts/check_framework.py --purpose application
python scripts/check_framework.py --purpose chat
```

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

AI-письма генерирует и проверяет `CommunicationFramework()` (`application`). Затем `letter_quality_issues()` проверяет границы длины 150–900 символов, клише и незарендеренные placeholder'ы. Если генерация или проверка не удалась, конкретный отклик пропускается; статический текст не отправляется.

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

Посмотреть реальные ответы LLM без отправки (тот же worker, но POST запрещён):

```bash
python scripts/reply_shadow.py default 30
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
  └─ REPLY_TEXT -> Humanizer Framework
проверка качества текста
GET /negotiations/{id}/messages   # revalidate exact employer turn
POST /negotiations/{id}/messages
```

Classifier не тратит LLM-вызов на очевидные состояния. Служебные HH-уведомления
и простые acknowledgement без вопроса получают `IGNORE`. Явные button/UI hints
получают `MANUAL`. Если тот же employer-вопрос повторился после нашего
текстового ответа, это считается сильным признаком, что бот ждал кнопку или
структурированное действие, и чат тоже уходит в `MANUAL`.

Обычный `REPLY_TEXT` генерируется `CommunicationFramework()` с его собственным
провайдером. Перед POST worker перечитывает историю и отправляет ответ только
если тот же employer message всё ещё последний. Локальный журнал
`reply_message_sends` не допускает повторной отправки на тот же ID вопроса.
После сетевой ошибки worker снова читает чат и не повторяет POST, если
предполагаемый ответ уже появился. Для переписки в несколько шагов используйте
`./scripts/reply.sh --live --rounds 5 --round-interval 60`.

Pending manual cases можно посмотреть:

```bash
hh-applicant-tool --profile-id default manual-chats
```

После ручного браузерного ответа:

```bash
hh-applicant-tool --profile-id default manual-chats --resolve CHAT_ID
```

## Анкеты И Контакты Из Описаний Вакансий

Часть вакансий нельзя закрыть автопилотом: отклик идёт через внешнюю анкету (`response_url`), либо работодатели пишут контакты прямо в описании — Google/Yandex Forms, телеграм-чаты, почту HR, форумы. Apply-прогон вытаскивает это в таблицу `vacancy_links` локальной базы профиля:

- `response_url` / `adv_response_url` — с каждой вакансии из выдачи;
- ссылки и контакты из полного описания (form / telegram / email / phone / external) — при загрузке описания. Внутренние ссылки hh.ru отбрасываются, обёртки `hh.ru/redirect?...` разворачиваются.

Посмотреть накопившееся и пройтись вручную:

```bash
hh-applicant-tool --profile-id 0555 vacancy-links             # сгруппированный список
hh-applicant-tool --profile-id 0555 vacancy-links --kind form # только анкеты
hh-applicant-tool --profile-id 0555 vacancy-links --csv > followups.csv
hh-applicant-tool --profile-id 0555 vacancy-links --json      # для скриптов/агентов
```

Операция читает только локальную SQLite и не требует авторизации. В `ops/`-снапшоты эти данные по privacy-контракту не копируются.

## Humanizer

Сопроводительные письма и ответы работодателям генерирует `CommunicationFramework()`.
Он сам выбирает провайдер, строит план сообщения и проверяет результат. Локальные
`letter_quality_issues()` и `reply_quality_issues()` служат дополнительным барьером.
При сбое генерации сообщение не отправляется.

## Humanizer Framework

Общий communication layer живёт в отдельном репозитории [humanizer-framework](https://github.com/orelkrylatiy/humanizer-framework) и подключён как git-зависимость, запиненная на конкретный коммит (см. `pyproject.toml`). Обновление pin — только явный bump: любое изменение planner'а меняет тексты исходящих сообщений. Для локальной доработки фреймворка удобнее editable-установка: `pip install -e ../humanizer-framework`.

При отклике используется запрос `job_search/application`; при ответе в чате
`job_search/chat_reply`. В рабочем пути вызов `CommunicationFramework()` не получает
провайдер от проекта. `scripts/check_framework.py` делает пробную генерацию без
отправки в hh.ru. Классификатор по-прежнему пропускает служебные и ручные чаты,
а перед отправкой worker перечитывает последнее сообщение работодателя.

Смайлы и эмодзи в hh-коммуникации запрещены на трёх уровнях: business rule в промпте адаптера, hard-валидация `forbidden_smiley` во фреймворке (для неформальных RU-каналов profi/repetit/telegram она выключена) и проверка `contains a smiley or emoji` в `reply_quality_issues`/`letter_quality_issues` основного пайплайна.

Опциональная отправка email после успешного отклика включается через
`APPLY_SEND_EMAIL=1`. Если задан `HH_GMAIL_DIR`, проект использует локальный
Gmail API OAuth (`config.json`, `credentials.json`, `token.json`) и проверяет адрес
аккаунта перед запуском. Без него профиль должен содержать `smtp.host`,
`smtp.port` и `smtp.from` или `smtp.user`; для авторизованного сервера нужны
также `smtp.user` и `smtp.password`. Тело email — то же проверенное фреймворком сопроводительное
письмо плюс ссылка на резюме. Локальная таблица `vacancy_email_sends` хранит
получателя, тему, статус и время отправки, предотвращая повторное письмо по
той же вакансии и адресу. Дополнительно все профили используют общий
`global_email_sends.sqlite` в корне `CONFIG_DIR`: один email после успешной
отправки блокируется для остальных аккаунтов тоже. При первом запуске ledger
подбирает уже отправленные адреса из старых профильных SQLite-баз. Путь можно
переопределить через `HH_EMAIL_LEDGER_PATH`, если профили работают с общим
сетевым каталогом.

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
# Все профили: один проход откликов, затем один проход автоответов
./scripts/all-profiles.sh daily --live

# То же без реальных отправок
./scripts/all-profiles.sh daily --dry-run

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

Fleet-команда `daily` не включает AI-фильтр вакансий (`--ai-filter`), но сохраняет
обычный regex hard filter и использует AI для сопроводительных писем и ответов в
чатах.

На Windows браузеры всех сохранённых HH-профилей открываются командой из корня:

```bat
open-browsers.bat
```

Чтобы открыть только выбранные профили, передай их ID, например
`open-browsers.bat 0555 1206`.

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
