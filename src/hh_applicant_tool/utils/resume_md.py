"""
Парсер markdown-шаблона резюме в словарь для POST /resumes.

Поля с неизвестным заранее ID (город, профроль, гражданство и т.д.)
возвращаются как {_suggest: endpoint, text: name}. Операция create-resume
разрешает эти значения через read-only HH suggest endpoints перед записью.
"""
from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any


# ── Таблицы перевода русских значений в API-идентификаторы ────────────────────

GENDER_RU = {"мужской": "male", "женский": "female"}

EMPLOYMENT_RU = {
    "полная занятость": "full",
    "полный рабочий день": "full",
    "частичная занятость": "part",
    "неполная занятость": "part",
    "проектная работа": "project",
    "волонтёрство": "volunteer",
    "волонтерство": "volunteer",
    "стажировка": "probation",
}

SCHEDULE_RU = {
    "полный день": "fullDay",
    "сменный график": "shift",
    "гибкий график": "flexible",
    "гибкий": "flexible",
    "удалённая работа": "remote",
    "удаленная работа": "remote",
    "удалённо": "remote",
    "вахта": "flyInFlyOut",
}

RELOCATION_TYPE_RU = {
    "не готов": "no_relocation",
    "невозможен": "no_relocation",
    "нет": "no_relocation",
    "готов": "relocation_possible",
    "возможен": "relocation_possible",
    "возможно": "relocation_possible",
    "желательно": "relocation_desirable",
    "желателен": "relocation_desirable",
}

BUSINESS_TRIP_RU = {
    "никогда": "never",
    "нет": "never",
    "готов": "ready",
    "да": "ready",
    "иногда": "sometimes",
}

TRAVEL_TIME_RU = {
    "любое": "any",
    "не важно": "any",
    "до часа": "less_than_hour",
    "менее часа": "less_than_hour",
    "от часа до полутора": "from_hour_to_one_and_half",
}

EDU_LEVEL_RU = {
    "среднее": "secondary",
    "среднее специальное": "special_secondary",
    "неоконченное высшее": "unfinished_higher",
    "высшее": "higher",
    "бакалавр": "bachelor",
    "бакалавриат": "bachelor",
    "магистр": "master",
    "магистратура": "master",
    "кандидат наук": "candidate",
    "доктор наук": "doctor",
    "аспирантура": "post_graduate_study",
}

LANG_LEVEL_RU = {
    "a1": "a1", "a2": "a2", "b1": "b1", "b2": "b2",
    "c1": "c1", "c2": "c2", "l1": "l1",
    "родной": "l1",
    "начальный": "a1", "элементарный": "a1",
    "базовый": "a2",
    "средний": "b1",
    "выше среднего": "b2",
    "продвинутый": "c1",
    "свободный": "c2",
    "свободное владение": "c2",
}

LANG_NAME_RU = {
    "русский": "rus", "английский": "eng", "немецкий": "deu",
    "французский": "fra", "испанский": "spa", "итальянский": "ita",
    "португальский": "por", "китайский": "zho", "японский": "jpn",
    "корейский": "kor", "арабский": "ara", "турецкий": "tur",
    "польский": "pol", "украинский": "ukr", "белорусский": "bel",
    "казахский": "kaz",
}

CURRENCY_RU = {
    "руб": "RUR", "руб.": "RUR", "рублей": "RUR",
    "rub": "RUR", "rur": "RUR", "₽": "RUR",
    "usd": "USD", "долларов": "USD", "$": "USD",
    "eur": "EUR", "евро": "EUR", "€": "EUR",
}

SITE_TYPE_RU = {
    "github": "github",
    "gitlab": "gitlab",
    "linkedin": "linkedin",
    "личный сайт": "personal",
    "личный": "personal",
    "портфолио": "portfolio",
    "живой журнал": "livejournal",
    "мой круг": "moi_krug",
}

CONTACT_TYPE_RU = {
    "email": "email", "e-mail": "email",
    "почта": "email", "электронная почта": "email",
    "мобильный": "cell", "мобильный телефон": "cell", "сотовый": "cell",
    "телефон": "cell",
    "домашний": "home", "домашний телефон": "home",
    "рабочий": "work", "рабочий телефон": "work",
}

_END_MARKERS = frozenset({"настоящее время", "по настоящее время", "сейчас", "н.в.", "..."})


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _tr(value: str, mapping: dict[str, str], field: str) -> str:
    result = mapping.get(value.strip().lower())
    if result is None:
        allowed = ", ".join(sorted(mapping))
        raise ValueError(
            f"Неизвестное значение для {field}: {value!r}. Допустимые: {allowed}"
        )
    return result


def _suggest(endpoint: str, text: str) -> dict[str, str]:
    return {"_suggest": endpoint, "text": text}


def _split_sections(text: str, level: int) -> list[tuple[str, str]]:
    """Разбивает текст по заголовкам ровно заданного уровня."""
    hashes = "#" * level
    # Ровно N символов '#', не больше
    pattern = rf"^{hashes}(?!#) (.+)$"
    parts = re.split(pattern, text, flags=re.MULTILINE)
    result = []
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        result.append((heading, body))
    return result


def _parse_kv(text: str) -> dict[str, str]:
    """Собирает '- Ключ: Значение' строки в словарь (ключи в нижнем регистре)."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        item = line[2:]
        if ":" in item:
            key, _, value = item.partition(":")
            result[key.strip().lower()] = value.strip()
    return result


def _parse_values(text: str) -> list[str]:
    """Собирает '- Значение' строки (без двоеточия) в список."""
    result = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- ") and ":" not in line:
            v = line[2:].strip()
            if v:
                result.append(v)
    return result


def _parse_description(text: str) -> str:
    """Возвращает параграфный текст — строки не начинающиеся с '-' или '#'."""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(("-", "#"))
    ]
    return "\n".join(lines)


def _parse_date(s: str) -> str:
    """'03.2021' → '2021-03-01'; '2021-03-01' → ISO date."""
    value = s.strip()
    month_year = re.fullmatch(r"(\d{2})\.(\d{4})", value)
    if month_year:
        month = int(month_year.group(1))
        year = int(month_year.group(2))
        try:
            parsed = date(year, month, 1)
        except ValueError as exc:
            raise ValueError(f"Некорректная дата: {value!r}") from exc
        return parsed.isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(
            f"Не удалось распознать дату: {value!r} "
            "(ожидается ММ.ГГГГ или YYYY-MM-DD)"
        ) from exc


def _parse_phone(s: str) -> dict[str, str]:
    """Parse a Russian HH phone into country/city/number components."""
    value = s.strip()
    comment: str | None = None
    match = re.search(r"\(([^)]+)\)\s*$", value)
    if match:
        comment = match.group(1).strip()
        value = value[: match.start()].strip()

    digits = re.sub(r"\D", "", value)
    if len(digits) != 11 or digits[0] not in ("7", "8"):
        raise ValueError(
            f"Некорректный телефон: {s!r}. "
            "Ожидается российский номер из 11 цифр, например +7 916 123-45-67"
        )

    result: dict[str, str] = {
        "country": "7",
        "city": digits[1:4],
        "number": digits[4:],
    }
    if comment:
        result["comment"] = comment
    return result


def _parse_salary(s: str) -> dict[str, Any]:
    """'200 000 руб.' → {amount: 200000, currency: 'RUR'}."""
    match = re.fullmatch(r"\s*([\d\s]+?)\s*([^\d\s].*)?\s*", s)
    if not match:
        raise ValueError(f"Не удалось распознать зарплату: {s!r}")

    amount = int(re.sub(r"\s", "", match.group(1)))
    if amount <= 0:
        raise ValueError("Зарплата должна быть положительным числом")

    currency_text = (match.group(2) or "").strip().lower()
    if not currency_text:
        currency = "RUR"
    else:
        currency = next(
            (code for key, code in CURRENCY_RU.items() if currency_text == key),
            None,
        )
        if currency is None:
            raise ValueError(f"Неизвестная валюта зарплаты: {currency_text!r}")

    return {"amount": amount, "currency": currency}


# ── Основной парсер ───────────────────────────────────────────────────────────

def parse_resume_md(text: str) -> dict[str, Any]:
    """
    Парсит markdown-резюме в dict для POST /resumes.
    Опасные поля (город, роль, гражданство) возвращаются как
    {_suggest: endpoint, text: name} для последующего разрешения через API.
    """
    result: dict[str, Any] = {}
    secs = {h.lower(): body for h, body in _split_sections(text, level=2)}

    # ── Личные данные ─────────────────────────────────────────────────────────
    if (sec := secs.get("личные данные")):
        kv = _parse_kv(sec)
        for ru_key, api_key in [("имя", "first_name"), ("фамилия", "last_name"),
                                  ("отчество", "middle_name")]:
            if v := kv.get(ru_key):
                result[api_key] = v
        if v := kv.get("дата рождения"):
            try:
                result["birth_date"] = datetime.strptime(v, "%d.%m.%Y").date().isoformat()
            except ValueError as exc:
                raise ValueError(
                    f"Некорректная дата рождения: {v!r} (ожидается ДД.ММ.ГГГГ)"
                ) from exc
        if v := kv.get("пол"):
            result["gender"] = {"id": _tr(v, GENDER_RU, "пол")}

    # ── Желаемая должность ────────────────────────────────────────────────────
    if (sec := secs.get("желаемая должность")) and (
        title := sec.splitlines()[0].strip()
    ):
        result["title"] = title

    # ── Контакты ──────────────────────────────────────────────────────────────
    if (sec := secs.get("контакты")):
        contacts = []
        for line in sec.splitlines():
            line = line.strip()
            if not line.startswith("- ") or ":" not in line:
                continue
            label, _, value = line[2:].partition(":")
            label_key = label.strip().lower()
            label_id = CONTACT_TYPE_RU.get(label_key)
            if label_id is None:
                raise ValueError(f"Неизвестный тип контакта: {label.strip()!r}")
            value = value.strip()
            if not value:
                raise ValueError(f"Пустое значение контакта: {label.strip()!r}")
            if label_id == "email":
                if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                    raise ValueError(f"Некорректный email: {value!r}")
                contacts.append({"type": {"id": "email"}, "value": value})
            else:
                # телефон: value — объект {country, city, number[, formatted]},
                # comment — отдельное поле на верхнем уровне контакта
                phone = _parse_phone(value)
                comment = phone.pop("comment", None)
                entry: dict[str, Any] = {"type": {"id": label_id}, "value": phone}
                if comment:
                    entry["comment"] = comment
                contacts.append(entry)
        if contacts:
            result["contact"] = contacts

    # ── Зарплата ──────────────────────────────────────────────────────────────
    if (sec := secs.get("зарплата")) and (first := sec.splitlines()[0].strip()):
        result["salary"] = _parse_salary(first)

    # ── Место проживания ──────────────────────────────────────────────────────
    for heading in ("место проживания", "город"):
        if (sec := secs.get(heading)):
            if city := sec.splitlines()[0].strip():
                result["area"] = _suggest("/suggests/area_leaves", city)
            break

    # ── Метро ─────────────────────────────────────────────────────────────────
    if (sec := secs.get("метро")) and (station := sec.splitlines()[0].strip()):
        result["metro"] = _suggest("/suggests/metro", station)

    # ── Профессиональные роли ─────────────────────────────────────────────────
    if (sec := secs.get("профессиональные роли")):
        roles = [_suggest("/suggests/professional_roles", v) for v in _parse_values(sec)]
        # Также строки "- Ключ: Значение" с ролью как значением
        kv = _parse_kv(sec)
        for v in kv.values():
            roles.append(_suggest("/suggests/professional_roles", v))
        if not roles and (line := sec.splitlines()[0].strip()):
            # Первая строка как роль
            roles.append(_suggest("/suggests/professional_roles", line))
        if roles:
            result["professional_roles"] = roles

    # ── Занятость ─────────────────────────────────────────────────────────────
    if (sec := secs.get("занятость")):
        employments = []
        for v in _parse_values(sec):
            employments.append({"id": _tr(v, EMPLOYMENT_RU, "занятость")})
        if employments:
            result["employments"] = employments

    # ── График работы ─────────────────────────────────────────────────────────
    if (sec := secs.get("график работы")):
        schedules = []
        for v in _parse_values(sec):
            schedules.append({"id": _tr(v, SCHEDULE_RU, "график")})
        if schedules:
            result["schedules"] = schedules

    # ── Переезд ───────────────────────────────────────────────────────────────
    if (sec := secs.get("переезд")):
        kv = _parse_kv(sec)
        relocation: dict[str, Any] = {}
        if ttype := kv.get("тип"):
            relocation["type"] = {
                "id": _tr(ttype, RELOCATION_TYPE_RU, "тип переезда")
            }
        if cities_str := kv.get("города"):
            cities = [item.strip() for item in cities_str.split(",") if item.strip()]
            if not cities:
                raise ValueError("Раздел 'Переезд': список городов пуст")
            relocation["area"] = [
                _suggest("/suggests/area_leaves", city) for city in cities
            ]
        if relocation:
            result["relocation"] = relocation

    # ── Командировки ──────────────────────────────────────────────────────────
    if (sec := secs.get("командировки")) and (v := sec.splitlines()[0].strip()):
        result["business_trip_readiness"] = {
            "id": _tr(v, BUSINESS_TRIP_RU, "командировки")
        }

    # ── Время в пути ──────────────────────────────────────────────────────────
    if (sec := secs.get("время в пути")) and (v := sec.splitlines()[0].strip()):
        result["travel_time"] = {"id": _tr(v, TRAVEL_TIME_RU, "время в пути")}

    # ── Гражданство ───────────────────────────────────────────────────────────
    if (sec := secs.get("гражданство")):
        result["citizenship"] = [_suggest("/suggests/areas", v) for v in _parse_values(sec)]

    # ── Право на работу ───────────────────────────────────────────────────────
    if (sec := secs.get("право на работу")):
        result["work_ticket"] = [_suggest("/suggests/areas", v) for v in _parse_values(sec)]

    # ── Водительское удостоверение ────────────────────────────────────────────
    if (sec := secs.get("водительское удостоверение")):
        kv = _parse_kv(sec)
        types = [{"id": v.upper()} for v in _parse_values(sec) if len(v) <= 3]
        if types:
            result["driver_license_types"] = types
        if "автомобиль" in kv:
            result["has_vehicle"] = kv["автомобиль"].lower() not in ("нет", "no", "false", "0")

    # ── Языки ─────────────────────────────────────────────────────────────────
    if (sec := secs.get("языки")):
        languages = []
        for line in sec.splitlines():
            line = line.strip()
            if not line.startswith("- ") or ":" not in line:
                continue
            lang_str, _, level_str = line[2:].partition(":")
            language_name = lang_str.strip()
            level_name = level_str.strip()
            lang_id = LANG_NAME_RU.get(language_name.lower())
            level_id = LANG_LEVEL_RU.get(level_name.lower())
            if lang_id is None:
                raise ValueError(f"Неизвестный язык: {language_name!r}")
            if level_id is None:
                raise ValueError(
                    f"Неизвестный уровень языка {language_name!r}: {level_name!r}"
                )
            languages.append({"id": lang_id, "level": {"id": level_id}})
        if languages:
            result["language"] = languages

    # ── Ключевые навыки ───────────────────────────────────────────────────────
    if (sec := secs.get("ключевые навыки")):
        result["skill_set"] = _parse_values(sec)

    # ── О себе ────────────────────────────────────────────────────────────────
    for heading in ("о себе", "обо мне", "навыки"):
        if (sec := secs.get(heading)):
            result["skills"] = sec.strip()
            break

    # ── Опыт работы ───────────────────────────────────────────────────────────
    if (sec := secs.get("опыт работы")):
        experience = []
        for company_name, job_body in _split_sections(sec, level=3):
            kv = _parse_kv(job_body)
            entry: dict[str, Any] = {
                "company": company_name,
                "position": kv.get("должность", ""),
                "description": _parse_description(job_body),
            }
            if city := kv.get("город"):
                entry["area"] = _suggest("/suggests/area_leaves", city)
            # Даты: либо "Начало/Конец", либо "Период"
            if start_str := kv.get("начало"):
                entry["start"] = _parse_date(start_str)
                end_str = kv.get("конец", "")
                if end_str and end_str.strip().lower() not in _END_MARKERS:
                    entry["end"] = _parse_date(end_str)
            elif period := kv.get("период"):
                parts = re.split(r"\s*[—–]\s*", period, maxsplit=1)
                entry["start"] = _parse_date(parts[0])
                if len(parts) > 1 and parts[1].strip().lower() not in _END_MARKERS:
                    entry["end"] = _parse_date(parts[1])
            if industry := kv.get("отрасль"):
                entry["industries"] = [{"name": industry}]
            if url := kv.get("сайт"):
                entry["company_url"] = url
            if company_id_text := kv.get("компания id"):
                entry["company_id"] = company_id_text.strip()
            experience.append(entry)
        if experience:
            result["experience"] = experience

    # ── Образование ───────────────────────────────────────────────────────────
    if (sec := secs.get("образование")):
        edu: dict[str, Any] = {}
        kv = _parse_kv(sec)
        if level_str := kv.get("уровень"):
            edu["level"] = {
                "id": _tr(level_str, EDU_LEVEL_RU, "уровень образования")
            }

        primary: list[dict] = []
        additional: list[dict] = []
        attestation: list[dict] = []

        for h3, h3_body in _split_sections(sec, level=3):
            h3_l = h3.lower()
            if any(k in h3_l for k in ("курс", "тренинг", "повышение квалификации")):
                for name, h4_body in _split_sections(h3_body, level=4):
                    sub = _parse_kv(h4_body)
                    additional.append(_edu_entry(name, sub))
            elif any(k in h3_l for k in ("тест", "экзамен", "аттест", "сертифик")):
                for name, h4_body in _split_sections(h3_body, level=4):
                    sub = _parse_kv(h4_body)
                    attestation.append(_edu_entry(name, sub))
            else:
                sub = _parse_kv(h3_body)
                entry: dict[str, Any] = {"name": h3}
                if fac := sub.get("факультет"):
                    entry["organization"] = fac
                if spec := sub.get("специальность"):
                    entry["result"] = spec
                if year := sub.get("год окончания"):
                    entry["year"] = int(year)
                primary.append(entry)

        if primary:
            edu["primary"] = primary
        if additional:
            edu["additional"] = additional
        if attestation:
            edu["attestation"] = attestation
        if edu:
            result["education"] = edu

    # ── Рекомендации ──────────────────────────────────────────────────────────
    if (sec := secs.get("рекомендации")):
        recs = []
        for name, body in _split_sections(sec, level=3):
            kv = _parse_kv(body)
            rec: dict[str, str] = {"name": name}
            if pos := kv.get("должность"):
                rec["position"] = pos
            if org := kv.get("организация"):
                rec["organization"] = org
            if contact := kv.get("контакт"):
                rec["contact"] = contact
            recs.append(rec)
        if recs:
            result["recommendation"] = recs

    # ── Сайты и профили ───────────────────────────────────────────────────────
    if (sec := secs.get("сайты")):
        sites = []
        for line in sec.splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            # "- GitHub: https://..." — URL может содержать ':'
            m = re.match(r"^-\s+(.+?):\s+(https?://.+)$", line)
            if m:
                type_id = SITE_TYPE_RU.get(m.group(1).strip().lower(), "personal")
                sites.append({"type": {"id": type_id}, "url": m.group(2).strip()})
        if sites:
            result["site"] = sites

    return result


def _edu_entry(name: str, kv: dict[str, str]) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name}
    if org := kv.get("организация"):
        entry["organization"] = org
    if doc := kv.get("документ"):
        entry["result"] = doc
    if year := kv.get("год"):
        entry["year"] = int(year)
    return entry
