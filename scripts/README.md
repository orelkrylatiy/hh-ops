# Скрипты автоматизации HH.ru

Корневой `scripts/` содержит только runtime entrypoint'ы. Диагностика и
агрегированные отчёты лежат в `scripts/ops/`.

## Runtime entrypoints

| Скрипт | Назначение |
|---|---|
| `apply.sh` | один bounded batch откликов с optional resume alias |
| `apply-profile.sh` | profile-specific apply lanes с legacy fallback |
| `setup-ai-resume.sh` | dry-run/live создание AI resume variant |
| `reply.sh` | один bounded pass по чатам |
| `cleanup.sh` | очистка rejected/discard переговоров |
| `daily.sh` | ручной one-shot apply + reply |
| `all-profiles.sh` | запуск операции по всем профилям с per-profile lock |
| `cron-job.sh` | safety gate `off/dry-run/live` для cron |
| `setup-cron.sh` | установка канонического host-cron |
| `write-runtime-env.sh` | shell-safe runtime env для cron (0600; может содержать bot token) |
| `check_ai.py` | проверка LLM-конфига |
| `check.sh` | ручная диагностика аккаунта |

Production использует только `crontab -> cron-job.sh -> all-profiles.sh`.
Отдельных Python-daemon/systemd scheduler'ов нет, чтобы один аккаунт случайно
не обрабатывался двумя scheduler'ами одновременно.

## Resume lanes

`all-profiles.sh apply` вызывает `apply-profile.sh`. Для профилей без
`rules/apply-lanes/<profile>.json` runner просто делегирует старому
`apply.sh`, поэтому существующие аккаунты не меняют поведение.

Для `0555` настроены отдельные frontend и AI/LLM lanes. Пока alias `primary`
ещё не записан, frontend lane может вывести его только из единственного
опубликованного резюме; при нескольких опубликованных резюме без alias запуск
останавливается fail-closed. AI lanes активируются
только после того, как `setup-ai-resume.sh --profile 0555 --live` создаст
резюме и сохранит alias `ai-engineer`.

Preview создания:

```bash
./scripts/setup-ai-resume.sh --profile 0555 --dry-run
```

Live creation + publish:

```bash
./scripts/setup-ai-resume.sh --profile 0555 --live
```

## Чаты

`reply.sh` запускает один проход и завершается. Повторные проверки делает cron.

Classifier перед LLM делит employer-turn на:

- `REPLY_TEXT` — обычный текстовый вопрос/сообщение;
- `IGNORE` — служебное уведомление или acknowledgement без вопроса;
- `MANUAL` — вероятный UI/button flow.

Если работодатель/бот повторяет тот же текст после того, как кандидат уже
ответил, чат считается `MANUAL`: это сильный признак, что бот ожидал кнопку
или структурированное действие.

Manual cases сохраняются в SQLite профиля. Посмотреть очередь:

```bash
hh-applicant-tool --profile-id default manual-chats
hh-applicant-tool --profile-id default --json manual-chats
```

После ручной обработки:

```bash
hh-applicant-tool --profile-id default manual-chats --resolve CHAT_ID
```

## Очистка отказов

Preview:

```bash
./scripts/cleanup.sh --profile default --dry-run
```

Live:

```bash
./scripts/cleanup.sh --profile default --live
```

По умолчанию удаляются только переговоры со state=`discard` и скрывается их
web-chat. Работодатели не блокируются, ATS-эвристика не используется.

Для fleet:

```bash
./scripts/all-profiles.sh cleanup --dry-run
./scripts/all-profiles.sh cleanup --live
```

## Расписание по умолчанию

| Время | Job |
|---|---|
| 09:00 | boost |
| 09:10 | apply |
| 09:25–21:25 каждый час | reply |
| 22:10 | cleanup rejected negotiations |
| 02:20 | privacy-safe ops snapshot |

Внешние write разрешаются только при `HH_AUTOMATION_MODE=live`.
