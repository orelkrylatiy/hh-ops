# HH-OPS — быстрый запуск на Linux

1. Создайте .env по .env.example, задайте ADMIN_USERNAME и
   ADMIN_PASSWORD. Сохраните HH_AUTOMATION_MODE=off.
2. Запустите docker compose up -d --build.
3. Откройте http://127.0.0.1:8000 по localhost/SSH-туннелю.
4. В меню профилей проверьте авторизацию. Первичный вход в HH
   может потребовать отдельной консольной/графической авторизации.
5. В «Дашборд» увидите снимки SQLite, графики, статусы по аккаунтам.
   Кнопка «Проверить HH сейчас» делает read-only запросы к HH API.
6. В «Настройки» измените контакт и модель конкретного профиля.
7. В «Автоматизация» задайте общие лимиты и dry-run/off.
8. В «Запуск задач» проверьте safe-apply и safe-reply в dry-run.
9. После проверки включайте live с явным подтверждением.

Проверка:

    docker compose logs --tail 100 hh_applicant_tool
    docker compose exec hh_applicant_tool bash scripts/reply.sh --profile default --dry-run

Режим off останавливает запланированные внешние действия. Режим live
действует на все настроенные аккаунты; новые follow-ups и quick replies
изначально выключены.

Документация: docs/DEPLOYMENT.md, docs/ARCHITECTURE.md.
