# Scheduling

В проекте один канонический scheduler: **cron**. В Docker он устанавливается из
корневого `crontab`; на обычном Linux/macOS host можно использовать
`scripts/setup-cron.sh`.

Это намеренно: отдельные Python-daemon/systemd timer реализации удалены, чтобы
не было двух независимых scheduler'ов, одновременно работающих с одним HH
профилем.

## Установка на host

```bash
bash scripts/setup-cron.sh
```

Настраиваемые времена:

```bash
BOOST_TIME=09:00 \
APPLY_TIME=09:10 \
REPLY_START_HOUR=9 \
REPLY_END_HOUR=21 \
CLEANUP_TIME=22:10 \
bash scripts/setup-cron.sh
```

Фактические HH-write всё равно контролирует `.env`:

```dotenv
HH_AUTOMATION_MODE=off
# off | dry-run | live
```

## Расписание контейнера

| Время | Действие |
|---|---|
| 09:00 | boost-resume |
| 09:10 | bounded apply batch |
| :25 каждый час 09–21 | bounded reply pass |
| 22:10 | cleanup state=discard |
| 02:20 | local aggregate ops report |

`cleanup` не blacklist'ит работодателей и не удаляет старые активные
переговоры. `CLEANUP_DELETE_CHAT=1` дополнительно скрывает rejected web-chat.

## Ручные one-shot команды

```bash
./scripts/apply.sh --dry-run
./scripts/reply.sh --dry-run
./scripts/cleanup.sh --dry-run
./scripts/daily.sh --dry-run
```

Для нескольких профилей:

```bash
./scripts/all-profiles.sh apply --dry-run
./scripts/all-profiles.sh reply --dry-run
./scripts/all-profiles.sh cleanup --dry-run
```

Per-profile `flock` в `all-profiles.sh` не даёт конфликтующим операциям
одновременно работать с одним аккаунтом.
