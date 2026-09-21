# Agent Quick Start

Этот файл поможет агенту быстро включиться в работу без изучения всей документации.

---

## 🔐 Авторизация

Проверить:
```bash
hh-applicant-tool whoami
```

Если ошибка — запустить:
```bash
hh-applicant-tool authorize
```

---

## 📊 Контекст

**Пользователь:** `${HH_NAME}`
**Роль:** Frontend-разработчик (React/TypeScript/Redux)  
**Опыт:** 5+ лет (прежние компании)  
**Локация:** Москва, удалёнка OK  
**Контакты:** 
- Telegram: **${HH_TELEGRAM}** (основной)
- Email: укажи локально

**Резюме:** https://hh.ru/resume/YOUR_RESUME_ID

---

## 🎯 Ежедневные задачи

### 1. Утро (первый запуск)

```bash
# Проверить состояние
hh-applicant-tool whoami
hh-applicant-tool list-resumes

# Поднять резюме в топ
hh-applicant-tool boost-resume
```

### 2. Отклики (80-120 в день)

```bash
# Dry-run сначала!
./scripts/apply-profile.sh --profile <PROFILE> --dry-run

# Если всё ок — explicit live
./scripts/apply-profile.sh --profile <PROFILE> --live
```

**Варианты поиска:**
- `"Frontend разработчик"` — основной
- `"React TypeScript"` — узкий
- `"JavaScript"` — широкий

### 3. Ответы работодателям

```bash
# Шаблонное сообщение (быстро)
./scripts/reply.sh --profile <PROFILE> --dry-run

# Реальная bounded отправка
./scripts/reply.sh --profile <PROFILE> --live
```

---

## 📈 KPI

| Метрика | Цель |
|---------|------|
| Откликов в день | 80-120 |
| Ответов работодателям | Все новые за 2 дня |
| Автоподнятие резюме | 1 раз в день |
| Просмотры резюме | Растут |

---

## ⚠️ Важно

1. **Всегда упоминать Telegram `${HH_TELEGRAM}`** в ответах
2. **Dry-run перед explicit `--live`** запуском откликов
3. **Исключать:** junior, стажёры, bitrix, web3, crypto, blockchain
4. **Лимит HH:** ~100-150 откликов в сутки

---

## 🛠 Troubleshooting

| Проблема | Решение |
|----------|---------|
| "Требуется авторизация" | `hh-applicant-tool authorize` |
| "Лимит откликов" | Ждать до завтра |
| "Токен протух" | `hh-applicant-tool refresh-token` |
| "Нет вакансий" | Расширить поиск, убрать фильтры |

---

## 📚 Документация

- [AGENT_GUIDE.md](AGENT_GUIDE.md) — полное руководство
- [AUTONOMOUS_AGENT_WORKFLOW.md](AUTONOMOUS_AGENT_WORKFLOW.md) — автономный контур
- [README.md](../README.md) — обзор проекта
