# Документация Проекта

Эта папка хранит operational, deployment и development-документацию вне корня репозитория.

## 🚀 Для агента (начать здесь)

| Файл | Описание |
|------|----------|
| **[QUICKSTART.md](QUICKSTART.md)** | ⭐ Быстрый старт для агента — читать первым! |
| **[AGENT_GUIDE.md](AGENT_GUIDE.md)** | 📚 Полное руководство агента — команды, API, troubleshooting |
| **[LLM_SETUP.md](LLM_SETUP.md)** | 🧠 Подключение AI — провайдеры, секции конфига, troubleshooting |
| [AUTONOMOUS_AGENT_WORKFLOW.md](AUTONOMOUS_AGENT_WORKFLOW.md) | Ограниченный автономный контур |
| [SCHEDULING.md](SCHEDULING.md) | Автозапуск по cron / Python scheduler |
| [AUTHORIZATION_INIT.md](AUTHORIZATION_INIT.md) | Как устроена авторизация и auto-auth поведение |

## Runtime И Operations

- `AGENT_GUIDE.md` — как использовать агент поверх инструмента
- `LLM_SETUP.md` — подключение AI (OpenAI / OpenRouter / Ollama)
- `AUTONOMOUS_AGENT_WORKFLOW.md` — ограниченный автономный контур
- `SCHEDULING.md` — автозапуск по cron
- `AUTHORIZATION_INIT.md` — как устроена авторизация и auto-auth поведение
- `DEVOPS.md` — подробный DevOps-гайд
- `DEVOPS_CHEATSHEET.md` — краткая DevOps-шпаргалка
- `DEPLOYMENT.md` — развёртывание на VPS
- `resume_template.md` — пример шаблона резюме

## Development

- `development/LINTING.md`
- `development/LINTING_SETUP.md`
- `development/TESTING.md`
- `development/TESTS.md`

## API

- `hhapi/openapi.yml` — snapshot HH API schema

- [HOT_LEADS.md](HOT_LEADS.md) — точное определение горячего лида, deterministic prefilter, LLM confirmation, SQLite dedup и Telegram alerts.
