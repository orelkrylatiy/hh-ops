# GLOSSARY — словарь терминов hh-ops

> Чтобы человек и агент понимали друг друга однозначно. Определения — из фактического
> кода (`src/hh_applicant_tool/`, `storage/schema.sql`, `rules/`, `prompts/`) и README.
> Обновляй при добавлении новых сущностей. Дата: 2026-10-21.

## Проект в одном абзаце

Детерминированный automation worker для соискателя на HH.ru (LLM — только генерация
текста писем и ответов, никаких LLM-агентов за рулём): поиск и фильтрация вакансий,
batch-отклики с AI-сопроводительными письмами, автоответы работодателям в чатах,
5 профилей-аккаунтов, SQLite-состояние, web-админка на 127.0.0.1:8000. Режимы работы
переключаются `HH_AUTOMATION_MODE=off|dry-run|live` в `.env` (сейчас `off` —
автономные cron-пути заблокированы, live-отклики запускаются вручную кампаниями).

## Аккаунты и конфигурация

- **Profile (профиль)** — аккаунт HH: директория `%APPDATA%\hh-applicant-tool\<id>\`
  с `config.json` (токены HH + LLM-ключи), SQLite `data`, `cookies.txt`, `log.txt`.
  Профили: `0555`, `1206`, `1558`, `5457`, `8755` (последний — без авторизации).
  Выбор: `--profile-id 0555` / `HH_PROFILE_ID`.
- **HH_AUTOMATION_MODE** — главный предохранитель: `off` | `dry-run` | `live`
  (cron/автономка читает его; ручные скрипты `tmp/mass_apply/*.sh` — основной
  live-путь сейчас).
- **Resume + alias** — резюме HH, привязанные к профилю. Алиасы в `config.json`:
  `primary` (frontend) и `ai-engineer`; резюме между алиасами не смешиваются
  (fail-closed). Контекст кандидата для LLM: `resumes/context/<profile>/<alias>.md`.
- **Apply lane (дорожка отклика)** — правило отклика по search-запросу на профиль:
  `rules/apply-lanes/0555.json` (frontend-primary, ai-engineer, llm-engineer,
  ai-automation: search / limit / pages / system_prompt / hard_filter_file).
- **Hard filter** — regex-фильтр отсева вакансий до отклика: `rules/apply-hard-filter.regex`
  (junior, C++, PHP, 1С, …) и `rules/apply-ai-hard-filter.regex` (AI-воронка, без
  исключения Python).

## Сущности домена (SQLite, `storage/schema.sql`, 11 таблиц)

- **Negotiation (переговоры/отклик)** — строка в `negotiations`, создаётся при отклике.
  Статусы (`state`): `response` (отклик отправлен) → `invitation` (приглашение) /
  `interview` (интервью) / `discard` (отказ или закрыто).
- **Vacancy** — строка `vacancies`: название, работодатель, зарплата from/to/currency,
  remote, опыт, URL.
- **Skipped vacancy** — вакансия, отсеянная фильтром (reason `excluded_filter`).
- **Vacancy link** — внешняя ссылка из вакансии/чата (`vacancy_links`, kind:
  `form` (Google/Yandex-анкеты), `telegram`, `email`) — вход для ручного прохода
  анкет (`tmp/forms/`).
- **Manual chat queue** — `manual_chat_queue`: чаты, отправленные ручной обработке
  (status `pending` / `resolved`).

## Пайплайны

- **Apply (отклик)** — операция отклика: поиск по lane → hard filter → LLM-письмо →
  POST отклика. AI-вариант: `_apply_vacancies_ai` (`--use-ai --force-message`).
- **Campaign (кампания)** — серия откликов одним прогоном через
  `tmp/mass_apply/apply_ai.sh "<поиск>" <алиас> <промпт> [limit]`; промпты
  `honest_frontend | honest_simple | honest_ai` (собес-режим). Обёртка сама пингует
  LLM до старта. **Full cycle** — `full_cycle_*.sh`: boost × профили → replies ×
  профили → кампания.
- **Cover letter** — LLM-сопроводительное письмо (150–900 знаков). Проверяется
  `letter_quality_issues` (клише, placeholders, эмодзи, длинные тире; один
  corrective-ретрай, потом пропуск вакансии).
- **Reply worker** — автоответчик в чатах работодателей: GET negotiations →
  GET messages → классификатор `REPLY_TEXT | IGNORE | MANUAL` → LLM → перечитка
  чата → POST. Состояние в `reply_state`, статические заглушки при сбое LLM —
  `reply_fallback` / `cover_letter_fallback`.
- **Classifier** — решение по входящему сообщению: ответить текстом / игнорировать /
  в ручную очередь (manual).
- **Boost** — ежедневное обновление публикации резюме (подъём в поиске; кулдаун 24 ч —
  «нельзя поднять» это норма). **Cleanup** — очистка discard-переговоров
  (22:10, `CLEANUP_DELETE_CHAT`).
- **Анкеты (forms)** — внешние Google/Yandex-формы из чатов HH: скан
  `py tmp/forms/scan_chat_forms.py`, заполнение `py tmp/forms/gform_fill_multi.py
  <name>.json` (данные `tmp/forms/<name>.json`).

## LLM и humanizer

- **humanizer-framework (live)** — закреплённая зависимость из `pyproject.toml`.
  `CommunicationFramework()` сам выбирает провайдер, планирует и проверяет
  сопроводительные письма (`application`) и ответы работодателям (`chat_reply`).
  Локальные проверки качества дополнительно блокируют плохой текст. При ошибке
  генерации отклик или ответ пропускается, статический текст не отправляется.
- **Email вакансии** — опциональная SMTP-отправка того же проверенного письма
  после успешного отклика (`APPLY_SEND_EMAIL=1`, профильный `smtp`). Локальная
  таблица `vacancy_email_sends` учитывает получателей и предотвращает дубли.
- **Ops snapshots** — агрегированные отчёты `ops/daily/*.json` / `ops/latest.json`
  (только агрегаты, без приватного).

## Инфраструктура

- **Admin** — FastAPI+uvicorn веб-панель `127.0.0.1:8000` (`admin.bat`, `admin/app.py`).
- **CLI** — `hh.bat` (`whoami`, `list-resumes`, `boost-resume`, `vacancy-links`, …);
  канонические обёртки `scripts/*.sh` (`reply.sh --dry-run|--live --profile`).
- **Лимиты/защиты** — HH держит ~100–150 успешных откликов/сутки суммарно на все
  аккаунты; лимит считается по успешным; кампанийные батчи паузы 25–45 с; per-profile
  flock; анти-дубль; re-read чата перед POST; `--skip-tests` (тестовые задания
  ботами не заполняются); `APPLY_RUN_TIMEOUT=3600s`.
- **Браузеры** — `open-browsers.bat` (ручные анкеты/manual-чаты); сами отклики идут
  через API, браузер нужен только для авторизации/капчи (vision-AI solver) и ручных операций.
