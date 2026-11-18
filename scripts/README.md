# Скрипты автоматизации HH.ru

Корневой `scripts/` содержит только runtime entrypoint'ы. Диагностика и
агрегированные отчёты лежат в `scripts/ops/`.

## Runtime entrypoints

| Скрипт | Назначение |
|---|---|
| `apply.sh` | один bounded batch откликов с optional resume alias |
| `apply-profile.sh` | profile-specific apply lanes с legacy fallback |
| `setup-ai-resume.sh` | dry-run/live создание AI resume variant |
| `reply.sh` | до пяти ограниченных проходов по чатам |
| `cleanup.sh` | очистка rejected/discard переговоров |
| `daily.sh` | единый one-shot boost + apply + reply: все аккаунты или `--profiles 0555 1206` |
| `all-profiles.sh` | запуск операции по всем профилям с per-profile lock |
| `cron-job.sh` | safety gate `off/dry-run/live` для cron |
| `setup-cron.sh` | установка канонического host-cron |
| `write-runtime-env.sh` | сохранение non-secret runtime knobs для cron |
| `check_ai.py` | проверка LLM-конфига |
| `check.sh` | ручная диагностика аккаунта |
| `open_browsers.py` | отдельные видимые HH-браузеры с cookies каждого профиля |

Полный цикл на Linux: `bash scripts/daily.sh --dry-run`; после проверки
`bash scripts/daily.sh --live --with-boost`. Для выбранных профилей:
`bash scripts/daily.sh --live --profiles 0555 1206 --with-boost`.

В графической Linux-сессии браузеры профилей доступны через
`python scripts/open_browsers.py 0555 1206 --config-dir ./config`.

Production использует только `crontab -> cron-job.sh -> all-profiles.sh`.
Отдельных Python-daemon/systemd scheduler'ов нет, чтобы один аккаунт случайно
не обрабатывался двумя scheduler'ами одновременно.

## Resume lanes

`all-profiles.sh apply` вызывает `apply-profile.sh`. Для профилей без
`rules/apply-lanes/<profile>.json` runner просто делегирует старому
`apply.sh`, поэтому существующие аккаунты не меняют поведение.

Локальные конфигурации lanes в `rules/apply-lanes/<profile>.json` опциональны.
Если их нет, используется `apply.sh`. Создание AI-варианта требует
пользовательского TOML-файла `resumes/variants/<profile>-ai-engineer.toml`;
в репозитории он не поставляется как готовый для реальных аккаунтов.

Preview создания:

```bash
./scripts/setup-ai-resume.sh --profile 0555 --dry-run
```

Live creation + publish:

```bash
./scripts/setup-ai-resume.sh --profile 0555 --live
```

## Чаты

`reply.sh` по умолчанию запускает один проход; повторные проверки делает cron.
Для пяти проходов в одном запуске: `./scripts/reply.sh --live --rounds 5 --round-interval 60`.
Каждый проход повторно читает историю и отправляет ответ только на новое последнее
сообщение работодателя. Уже обработанное сообщение не дублируется.

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
