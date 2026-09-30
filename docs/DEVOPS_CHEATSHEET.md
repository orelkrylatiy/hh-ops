# DevOps Шпаргалка

## Локальный Старт

```bash
git clone <repo> && cd hh-applicant-tool
docker compose build
docker compose up -d
docker compose logs -f
curl http://localhost:8000/health
```

## Windows Без Docker

Скрипты `scripts/*.sh` вызывают `python3`, которого нет в виндовом venv.
После создания venv добавьте шим:

```bash
cp .venv/Scripts/python.exe .venv/Scripts/python3.exe
```

Иначе `apply.sh`/`reply.sh`/`daily.sh` падают с `python3: command not found`.
Также `envsubst` должен быть в PATH (идёт с Git for Windows).

## Полезные Docker Команды

```bash
docker compose ps
docker compose logs -f
docker compose exec hh_applicant_tool bash
docker compose down
docker compose up -d --build
```

## Полезные CLI Команды

```bash
hh-applicant-tool whoami
hh-applicant-tool list-resumes
hh-applicant-tool update-resumes
hh-applicant-tool apply-vacancies --dry-run
hh-applicant-tool reply-employers --dry-run
hh-applicant-tool config -p
hh-applicant-tool log -f
```

## Что Проверить На VPS

```bash
docker ps
docker compose ps
docker exec -it hh_applicant_tool ls -la /app/config
curl http://localhost:8000/health
```

## Где Данные

- `/app/config/config.json`
- `/app/config/data`
- `/app/config/cookies.txt`
- `/app/config/log.txt`

## Если Что-то Сломалось

- контейнер не стартует -> `docker compose logs`
- нет данных профиля -> проверить `CONFIG_DIR`
- не работает админка -> проверить `curl /health`
- не обновляется токен -> проверить `config.json` и `whoami`
