# AGENTS.md — hh-ops production contract

Этот файл — короткий operational source of truth для AI/automation-агентов.
Перед изменением autonomous apply/reply/auth путей также читать:

- `docs/AUTONOMOUS_AGENT_WORKFLOW.md`
- `docs/AUTHORIZATION_INIT.md`
- `docs/ARCHITECTURE.md`

## Что реально автоматизировано

Production-контур **API-driven**, а не generic browser-form agent:

```text
cron / agent
  -> profile lock / resume lanes
  -> HH API search
  -> deterministic filters
  -> LLM cover letter (static fallback in apply-safe)
  -> POST /negotiations
  -> audit + optional Telegram success event
```

Автономно поддерживаются:

- HH vacancy search;
- выбор резюме через profile-local aliases/lanes;
- обычные HH отклики;
- token refresh при наличии refresh_token;
- bounded chat replies с stale-check/dedup;
- resume boost/update по явному live scheduler mode.

Не считать автоматизированными:

- первичную авторизацию, если HH требует SMS/email code или captcha;
- произвольные внешние формы из vacancy `response_url`;
- UI/button recruiter flows, классифицированные как MANUAL.

Такие случаи должны fail-closed, а не имитировать успех.

## Dry-run и live

Shell entrypoints безопасно стартуют в `dry-run` по умолчанию.

Реальные write требуют одного из вариантов:

```bash
./scripts/apply-profile.sh --profile PROFILE --live
./scripts/reply.sh --profile PROFILE --live
./scripts/daily.sh --profile PROFILE --live
```

Scheduled production управляется только:

```dotenv
HH_AUTOMATION_MODE=off      # default
# dry-run
# live
```

Никогда не трактовать запуск `apply.sh`, `reply.sh` или `daily.sh` без
`--live` как успешную реальную отправку.

## Канонический apply path

Для профиля нельзя обходить resume routing:

```text
cron-job.sh
  -> all-profiles.sh apply          # per-profile flock
  -> apply-profile.sh
  -> apply_profile.py
  -> rules/apply-lanes/<profile>.json (если есть)
  -> apply.sh
  -> hh-applicant-tool apply-safe
```

Если у профиля есть lanes, использовать именно `apply-profile.sh`, а не
сырой `apply.sh` / `apply-vacancies`.

Resume lane обязан задавать `resume_alias`. Неизвестный alias fail-closed.
`primary` можно вывести автоматически только когда опубликовано ровно одно
резюме.

Agent API следует той же семантике:

- explicit `resume_id` / `resume_alias` -> один `apply-safe` run;
- selector не задан -> live разрешён только при tracked profile lanes;
- selector отсутствует и lanes нет -> 422/fail-closed.

## Авторизация

Для scheduler/admin background jobs всегда использовать `--no-auto-auth`.
Они не должны ждать stdin.

Порядок:

1. access token рабочий -> продолжить;
2. access token истёк/403 и есть refresh token -> один refresh + retry;
3. refresh не удался / токена нет -> STOP и запрос человеку на re-auth;
4. первичный `authorize` использует Playwright, но SMS/email code и captcha
   могут потребовать человека.

Проверка:

```bash
hh-applicant-tool --profile-id PROFILE whoami
hh-applicant-tool --profile-id PROFILE list-resumes
```

Не обещать "полностью автоматический логин" после потери refresh credentials.

## Внешние формы и тесты

Scheduled `apply.sh` использует `--skip-tests`.

Vacancy с `response_url` (redirect/внешняя анкета) пропускается. Generic
browser filling для сторонних сайтов в production отсутствует. Не добавлять
эвристику "нажать всё подряд" без отдельного typed flow, deterministic
confirmation и regression tests.

## Успешный отклик и Telegram

После подтверждённого live application пишется structured log:

```text
HH_APPLY_SUCCESS profile=... vacancy_id=... resume_id=... resume_alias=...
```

Опциональный bot:

```dotenv
HH_NOTIFY_TELEGRAM_ENABLED=1
HH_NOTIFY_TELEGRAM_BOT_TOKEN=...
HH_NOTIFY_TELEGRAM_CHAT_ID=...
```

Это отдельные настройки от `HH_TELEGRAM`: последняя — контакт кандидата,
который может попадать в тексты работодателю.

Telegram notification — best-effort observability. Его ошибка не должна
превращать уже подтверждённый HH POST в failed/retry и не должна вызывать
повторный отклик.

Никогда не логировать bot token. Cron runtime env может содержать token и
должен храниться mode 0600.

## Платформы

### Docker/Linux — canonical autonomous production

```bash
docker compose up -d
# .env: HH_AUTOMATION_MODE=live только после dry-run/preflight
```

Container запускает cron + admin. Cron — единственный scheduler.

Host Linux/macOS:

```bash
bash scripts/setup-cron.sh
```

Не устанавливать второй scheduler рядом.

### Windows

CLI/admin доступны через:

```bat
hh.bat whoami
admin.bat
```

Bash automation scripts требуют среду с bash (Docker/WSL/Git Bash).
Для полностью автономного scheduler на Windows предпочтителен Docker, чтобы
использовать тот же tested cron path, а не собирать отдельный scheduler.

## Перед merge

Минимум:

```bash
pytest tests/
bash -n scripts/*.sh
shellcheck scripts/*.sh
```

И обязательно дождаться GitHub Actions:

- Automation quality;
- Tests Python 3.11;
- Tests Python 3.13;
- Shellcheck;
- Docker build.

Не считать nonblocking legacy type/lint report новым regression: targeted
critical-path checks должны быть зелёными, а новое type/lint debt добавлять
нельзя.
