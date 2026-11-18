# HH-OPS — Linux / Docker Deployment

Рабочий репозиторий: https://github.com/orelkrylatiy/hh-ops

## Установка на Linux VPS

Нужны Docker Engine, Docker Compose и Git.

    git clone https://github.com/orelkrylatiy/hh-ops.git
    cd hh-ops
    cp .env.example .env

В локальном .env обязательно установите ADMIN_USERNAME и ADMIN_PASSWORD.
Пример безопасного начала (подставьте собственный длинный пароль):

    ADMIN_USERNAME=operator
    ADMIN_PASSWORD=replace-this-with-a-unique-long-password
    HH_AUTOMATION_MODE=off
    APPLY_SEND_EMAIL=0
    CLEANUP_DELETE_CHAT=0
    TZ=Europe/Moscow

Docker admin запускается с ADMIN_REQUIRE_AUTH=1. Без двух credentials
страницы админки будут недоступны; /health останется доступным для healthcheck.

    docker compose up -d --build
    docker compose ps
    docker compose logs -f hh_applicant_tool
    curl -fsS http://127.0.0.1:8000/health

Админка: http://127.0.0.1:8000 (только localhost). Для просмотра с
другого компьютера:

    ssh -L 8000:127.0.0.1:8000 user@your-vps

Не открывайте порт 8000 в интернет без HTTPS и дополнительных ограничений.

## Откуда берутся данные

- /api/stats читает локальную SQLite каждого профиля, показывает
  график за 14, 30 или 90 дней и сравнительные показатели по аккаунтам.
  Время изменения базы не равно времени синхронизации с HH.
- /api/live-summary?profile=default делает ограниченные read-only GET
  запросы к HH API: /negotiations и /resumes/mine. Только по кнопке
  «Проверить HH сейчас». Для режима «все аккаунты» живой агрегат не
  запрашивается автоматически.
- /api/inbox показывает актуальные переписки через HH API; это не
  тот же источник, что локальный график.

## Управление из веб-интерфейса

Страница «Автоматизация» управляет общим cron для всех профилей:
off/dry-run/live, поисковый запрос, лимиты, глубина, ожидание ответов,
напоминания, quick replies, дополнительные email и cleanup.
Live требует отдельного подтверждения.

Изменения пишутся в config/admin_automation.json и в сгенерированный
shell-quoted config/admin_automation.env. Эти файлы создаются с правами
0600 и используются при следующем cron запуске без restart Docker.
Секреты HH и AI сюда не записываются. Не правьте этот файл вручную.

В разделе «Настройки» редактируется профильный контакт Telegram,
модель и задержка HH API. Токены и пароли в UI скрыты.
В разделе «Запуск задач» рекомендуется safe-apply и safe-reply:
это те же Linux worker'ы, что работают по расписанию; общий per-profile
flock защищает от конфликтов с cron.

## Профили, авторизация и безопасность

Профильные SQLite/token/cookie находятся в config/<profile>/.
Первичная HH авторизация может потребовать код/капчу: используйте
доступную Linux-графическую сессию или подходящую консоль.
Обычный Docker не предоставляет GUI для видимой Playwright авторизации.

    docker compose exec hh_applicant_tool hh-applicant-tool --profile-id default whoami
    docker compose exec hh_applicant_tool hh-applicant-tool --profile-id default list-resumes
    docker compose exec hh_applicant_tool bash scripts/reply.sh --profile default --dry-run
    docker compose exec hh_applicant_tool bash scripts/apply.sh --profile default --limit 10 --dry-run

Первый запуск: только off / dry-run, APPLY_LIMIT=10, follow-ups и
auto quick replies выключены. После проверки логов и реальных сообщений
HH постепенно включайте live.

## Контейнерное расписание

Часы относительно TZ: 09:00 boost (live), 09:10 apply,
каждый час 09:25–21:25 reply, 22:10 cleanup, 02:20 ops snapshot.
Логи: logs/cron.log, logs/profiles/, config/<profile>/log.txt.

## Без Docker

На Linux можно установить Python 3.11+ и зависимости в venv,
задать CONFIG_DIR, ADMIN_REQUIRE_AUTH, ADMIN_USERNAME, ADMIN_PASSWORD,
запустить uvicorn admin.ui_app:app --host 127.0.0.1 --port 8000 и
установить cron через bash scripts/setup-cron.sh.
