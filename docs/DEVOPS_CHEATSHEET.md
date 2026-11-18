# HH-OPS — Linux / Docker шпаргалка

## Установка

    git clone https://github.com/orelkrylatiy/hh-ops.git
    cd hh-ops
    cp .env.example .env
    # В .env установите ADMIN_USERNAME, ADMIN_PASSWORD, режим off.
    docker compose up -d --build
    docker compose ps
    curl http://127.0.0.1:8000/health

Админка: http://127.0.0.1:8000 с Basic Auth.
Для удаленного доступа: ssh -L 8000:127.0.0.1:8000 user@vps

## Безопасная диагностика

    docker compose logs -f hh_applicant_tool
    docker compose exec hh_applicant_tool hh-applicant-tool --profile-id default whoami
    docker compose exec hh_applicant_tool bash scripts/apply.sh --profile default --dry-run
    docker compose exec hh_applicant_tool bash scripts/reply.sh --profile default --dry-run
    docker compose exec hh_applicant_tool hh-applicant-tool --profile-id default manual-chats
    docker compose exec hh_applicant_tool python scripts/check_framework.py --purpose application

## Данные

- /api/stats: локальная SQLite; 14/30/90-дневная история.
- /api/live-summary: живые read-only счётчики HH по запросу.
- /api/inbox: живые переписки через HH API.
- config/admin_automation.json: настройки cron из админки.
- config/admin_automation.env: генерируемые shell-quoted overrides.
- config/<profile>/data: SQLite; config/<profile>/config.json: секреты HH.
- logs/profiles/: журнал запусков.

Автоматизация off/dry-run/live настраивается через веб-админку
и применяется к следующему cron запуску. Следует начать с off.
