# HH Applicant Tool — Agent Guide

Этот документ описывает актуальный агентный контур для проекта.

**Главная идея:**
- агент не заменяет инструмент;
- агент не должен быть бесконтрольным live-оператором;
- инструмент выполняет повторяемые команды;
- агент читает результаты, выбирает стратегию и помогает с переговорами.

**Критично:** shell-скрипты `apply.sh`, `reply.sh`, `daily.sh` по умолчанию
работают в dry-run. Реальные write требуют явного `--live` либо
`HH_AUTOMATION_MODE=live` в scheduler. Обычные HH-отклики API-driven;
произвольные внешние формы не заполняются автоматически.

---

## 🚀 Быстрый старт для агента

### 1. Проверка состояния (30 сек)

```bash
# Проверить авторизацию и статистику
hh-applicant-tool whoami

# Проверить резюме
hh-applicant-tool list-resumes

# Проверить активные переговоры
hh-applicant-tool call-api "/negotiations?status=active&per_page=20" 2>/dev/null | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(f'Активных переговоров: {len(d.get(\"items\", []))}')"
```

**Что искать:**
- ✅ `whoami` возвращает ФИО и статистику — авторизация ОК
- ✅ Есть опубликованные резюме — можно работать
- 📬 Количество активных переговоров — нужно ли отвечать

### 2. Ежедневный workflow (автономно)

```bash
# 1. Поднять резюме в топ (1 раз в день)
hh-applicant-tool boost-resume

# 2. Отклики с profile-specific resume lanes (dry-run → explicit live)
./scripts/apply-profile.sh --profile <PROFILE> --dry-run
./scripts/apply-profile.sh --profile <PROFILE> --live

# 3. Ответить работодателям (bounded pass)
./scripts/reply.sh --profile <PROFILE> --dry-run
./scripts/reply.sh --profile <PROFILE> --live

# Или один ручной apply+reply pass:
./scripts/daily.sh --profile <PROFILE> --dry-run
./scripts/daily.sh --profile <PROFILE> --live
```

### 3. Контекст проекта

**Пользователь:** `${HH_NAME}`, Frontend-разработчик (React/TypeScript/Redux)
**Опыт:** 5+ лет (прежние компании)  
**Локация:** Москва, готов к удалёнке  
**Контакты:** Telegram `${HH_TELEGRAM}`

**Текущая стратегия:**
- Откликов в день: 80-120
- Персональные ответы работодателям с упоминанием TG
- Исключать: junior, стажёры, bitrix, web3, crypto, blockchain
- Автоподнятие резюме: ежедневно

---

## Рекомендуемая Роль Агента

**Агенту стоит отдавать:**
- preflight проверку профиля;
- запуск preset/safe команд;
- анализ результата batch run;
- review переговоров;
- выбор follow-up сценариев;
- коррекцию поиска и фильтров.

**Агенту не стоит отдавать без рамок:**
- широкие live-запуски без `dry-run`;
- массовые ответы работодателям без review;
- самостоятельное изобретение новых стратегий прямо в production.

## 📋 Справочник команд

### Основные операции

| Команда | Описание | Пример |
|---------|----------|--------|
| `whoami` | Проверка авторизации | `hh-applicant-tool whoami` |
| `list-resumes` | Показать резюме | `hh-applicant-tool list-resumes` |
| `boost-resume` | Поднять резюме в топ | `hh-applicant-tool boost-resume` |
| `apply-vacancies` | Откликнуться на вакансии | См. ниже |
| `reply-employers` | Ответить работодателям | См. ниже |
| `call-api` | Прямой вызов HH API | `hh-applicant-tool call-api /negotiations` |

### Отклики на вакансии

```bash
# Рекомендуемый способ: тот же resume-lane routing, что использует cron
./scripts/apply-profile.sh --profile <PROFILE> --dry-run
./scripts/apply-profile.sh --profile <PROFILE> --live

# Или один explicit-resume run напрямую через safe operation:
hh-applicant-tool --no-auto-auth --profile-id <PROFILE> apply-safe \
  --resume-alias primary \
  --search "<запрос>" \
  --ai \
  --system-prompt prompts/cover_letter_frontend.txt \
  --force-message \
  --excluded-filter "junior|стажир|bitrix|web3|crypto|blockchain" \
  --skip-tests \
  --per-page 50 \
  --total-pages 5
```

**Параметры поиска:**
- `--search "Frontend разработчик"` — основной запрос
- `--search "React TypeScript"` — более узкий
- `--search "JavaScript"` — широкий (больше откликов)

**Фильтры:**
- `--experience between1And3` — middle уровень
- `--experience between3And6` — senior уровень
- `--schedule remote` — только удалёнка
- `--area 1` — Москва (ID региона)

### Ответы работодателям

```bash
# Рекомендуемый способ — итеративный AI-ответ (учитывает кто написал первым)
./scripts/reply.sh --profile <PROFILE> --dry-run
./scripts/reply.sh --profile <PROFILE> --live
```

Логика `reply.sh`: пробегает по активным чатам, проверяет кто написал последним.
Если последнее сообщение от работодателя — генерирует персональный AI-ответ.
Если последнее от нас — пропускает (не задваивает ответы).
В промпт передаётся контекст: мы откликнулись сами или нас пригласили.

### API вызовы

```bash
# Получить переговоры
hh-applicant-tool call-api "/negotiations?status=active&per_page=100"

# Получить сообщения чата
hh-applicant-tool call-api "/negotiations/{ID}/messages?per_page=20"

# Отправить сообщение
hh-applicant-tool call-api -X POST "/negotiations/{ID}/messages" \
  -d '{"message": "Текст сообщения"}'

# Черный список работодателей
hh-applicant-tool call-api "/employers/blacklisted"
```

---

## 🤖 Web Agent API (опционально)

Базовый URL панели: `http://127.0.0.1:8000`

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/agent/preflight` | GET | Проверка готовности к работе |
| `/api/agent/run` | POST | Запуск операции |
| `/api/agent/digest` | GET | Краткая сводка по аккаунту |
| `/api/agent/review-negotiations` | GET | Рекомендации по переговорам |
| `/api/inbox` | GET | Список чатов |
| `/api/inbox/{neg_id}/messages` | GET | История переписки |
| `/api/inbox/{neg_id}/reply` | POST | Отправить ответ |

### Step 1 — Preflight

```http
GET /api/agent/preflight?profile=default
```

**Интерпретация `action`:**
- `run` — можно запускать операцию
- `refresh` — сначала нужен refresh-token
- `reauth` — нужен человек для повторной авторизации

## Step 2 — Safe Apply

Агенту лучше запускать `apply-vacancies` через `apply_params`.

Пример safe dry-run:

```http
POST /api/agent/run
Content-Type: application/json
```

```json
{
  "profile": "0555",
  "operation": "apply-vacancies",
  "auto_refresh": true,
  "apply_params": {
    "dry_run": true
  }
}
```

Для профиля с tracked lanes такой вызов использует `apply-profile`; search/filter/resume
настройки принадлежат lane config и не переопределяются агентом. Для профиля без
lanes агент обязан передать explicit `resume_id` или `resume_alias`. После
анализа dry-run live требует `confirm_live=true`.

---

## 🧠 Decision Matrix для агента

| Ситуация | Действие |
|----------|----------|
| token expired, есть refresh_token | Сначала `refresh-token` / agent auto-refresh |
| токена/refresh нет | Нужна human-assisted `authorize` |
| Резюме не опубликовано | `update-resumes` или `boost-resume` |
| Откликов < 50 за день | Запустить `apply-vacancies` |
| Откликов > 100 за день | Остановить отклики |
| Есть новые переговоры | Проверить, ответил ли |
| Работодатель написал | Ответить персонально + TG |
| Статус `discard` | Пропустить или отклонить |
| Token expired | `refresh-token` или `authorize` |

---

## ⚠️ Troubleshooting

### "Требуется авторизация"
```bash
hh-applicant-tool authorize
```

### "Лимит откликов достигнут"
HH.ru ограничивает ~100-150 откликов в сутки. Подождать до завтра.

### "Резюме не опубликовано"
```bash
hh-applicant-tool update-resumes
hh-applicant-tool boost-resume
```

### "Токен протух"
```bash
hh-applicant-tool refresh-token
# Или
hh-applicant-tool authorize
```

### "Нет вакансий в поиске"
- Расширить запрос: `"Frontend"` вместо `"Frontend React"`
- Убрать часть фильтров
- Увеличить `--total-pages`

---

## 📁 Полезные файлы

| Файл | Описание |
|------|----------|
| `letter.txt` | Шаблон сопроводительного письма |
| `config/config.json` | Конфигурация (токены, настройки) |
| `config/data` | SQLite база данных |
| `config/log.txt` | Логи операций |
| `config/cookies.txt` | Cookies сессии |

---

## 📞 Контакты пользователя

- **Telegram:** `${HH_TELEGRAM}`
- **Email:** укажи локально при необходимости
- **HH.ru:** https://hh.ru/resume/YOUR_RESUME_ID

**Важно:** При ответах работодателям всегда упоминать Telegram для оперативной связи.

## Step 3 — Digest

```http
GET /api/agent/digest?profile=default
```

Digest нужен, чтобы быстро понять:

- статус токена;
- сколько откликов уже накоплено;
- есть ли ошибки в логах;
- есть ли переговоры, которые требуют внимания.

Если `action_needed=reply_inbox`, агенту не нужно сразу отправлять сообщения. Сначала нужен review.

## Step 4 — Review Negotiations

```http
GET /api/agent/review-negotiations?profile=default
```

Endpoint возвращает agent-friendly поля:

- `days_since_update`
- `last_message_author`
- `recommended_action`
- `recommendation_reason`

Ключевые `recommended_action`:

- `reply_employer_waiting`
- `followup_candidate_silent`
- `skip_already_replied`
- `skip_rejection`
- `wait_recent_application`
- `wait_recent_activity`

Это и есть основной supervisory слой для переговоров.

## Step 5 — Reply

Если review показал, что ответ уместен:

```http
POST /api/inbox/{neg_id}/reply
Content-Type: application/json
```

```json
{
  "profile": "default",
  "message": "",
  "use_ai": true,
  "vacancy_name": "React Frontend Developer",
  "employer_name": "Acme"
}
```

Если `message` пустой и `use_ai=true`, система сама построит ответ по истории переписки.

> AI-ответы используют секцию `openai_reply` из `config.json` (если её нет — `openai_cover_letter`).
> Настройка ключей: [LLM_SETUP.md](LLM_SETUP.md).

## Что Пускать По Автомату

Канонический scheduler может регулярно выполнять bounded apply/reply/boost/cleanup,
если `HH_AUTOMATION_MODE=live` включён осознанно. Apply должен идти через
`apply-profile.sh`, а reply — через deterministic classifier + stale-check/manual queue.

Не автоматизированы и должны fail-closed:

- первичный login при SMS/email code или captcha;
- произвольные внешние `response_url` формы;
- recruiter UI/button flows, классифицированные как MANUAL;
- новый search/lane без dry-run проверки.

## Практический Контур

Оптимальный pipeline:

1. cron запускает `refresh-token`
2. cron запускает `update-resumes`
3. cron или агент запускает safe `apply-vacancies`
4. агент читает `digest`
5. агент читает `review-negotiations`
6. агент предлагает или отправляет только ограниченные осмысленные ответы

## Устаревшее, Чего Лучше Избегать

- модель “после первого логина агент полностью автономен”
- массовые ответы без review
- новый `search` сразу в live
- browser-driven full automation вместо API-driven контура
