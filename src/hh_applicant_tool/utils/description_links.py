"""Извлечение ссылок и контактов из HTML-описания вакансии.

Работодатели часто прячут в описании то, мимо чего автопилот проходит молча:
внешние анкеты (Google Forms и т.п.), телеграм-чаты, прямую почту HR,
форумы и ссылки «заполните форму». Извлечённое складывается в vacancy_links
и просматривается через ``hh-applicant-tool vacancy-links``.

Важно: strip_tags() выбрасывает href, поэтому <a href> разбирается из
сырого HTML, а уже по очищенному тексту ищутся голые URL/почты/телефоны.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

# kinds, которые возвращаются в поле kind
KIND_FORM = "form"
KIND_TELEGRAM = "telegram"
KIND_EMAIL = "email"
KIND_PHONE = "phone"
KIND_EXTERNAL = "external"

_HREF_RE = re.compile(
    r"""<a\b[^>]*?\bhref\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)

_BARE_URL_RE = re.compile(
    r"""(?:https?://|www\.)[^\s<>"'()[\]{}]+""",
    re.IGNORECASE,
)

# Домен с путем без схемы: forum.example.org/threads/123. Без пути не ищем —
# слишком много ложных срабатываний на обычном тексте.
_DOMAIN_PATH_URL_RE = re.compile(
    r"""\b[a-z0-9][a-z0-9.-]*\.(?:ru|com|org|net|io|dev|me|рф)/(?:[^\s<>"'()[\]{}]*)""",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+")

# @username после начала строки/пробела/пунктуации, но не внутри слова или email
_TG_HANDLE_RE = re.compile(r"(?<![\w@.])@([a-zA-Z][a-zA-Z0-9_]{3,30})\b")

_PHONE_RE = re.compile(r"(?:\+7|8)[\s\-()]?\(?\d{3}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")

_MAILTO_RE = re.compile(r"^mailto:(.+)", re.IGNORECASE)
_TEL_RE = re.compile(r"^tel:(.+)", re.IGNORECASE)

_FORM_URL_HOST_RE = re.compile(
    r"""(?:^|\.)(
        forms\.gle
        | docs\.google\.com
        | forms\.yandex\.ru
        | yandex\.ru/forms
        | typeform\.com
        | forms\.office\.com
        | forms\.app
        | airtable\.com
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

_TELEGRAM_URL_RE = re.compile(
    r"""^(?:https?://)?(?:t\.me|telegram\.me)/\S+|tg://""",
    re.IGNORECASE,
)

# Внутренние ссылки hh не требуют ручных действий — их пропускаем
_HH_HOST_RE = re.compile(r"""(?:^|\.)hh\.(?:ru|ua)$""", re.IGNORECASE)


def _url_host(value: str) -> str:
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value, flags=re.IGNORECASE)
    host = host.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.rpartition("@")[2]
    return host.split(":", 1)[0].lower().rstrip(".")


def unwrap_hh_redirect(value: str) -> str:
    """hh.ru оборачивает внешние ссылки в /redirect: достаём цель.

    Если распаковать не удалось, возвращаем как есть — classify_url() такие
    ссылки отбросит, как и раньше.
    """
    if "hh.ru/redirect" not in value:
        return value
    for values in parse_qs(urlparse(value).query).values():
        target = values[0]
        if target.startswith(("http://", "https://")):
            return target
    if match := re.search(r"https?://[^&\s]+", unquote(value)):
        return match.group(0)
    return value


def classify_url(value: str) -> str | None:
    """Тип ссылки или None, если она не нужна для ручного прохода."""
    value = value.strip()
    if not value:
        return None

    if _TELEGRAM_URL_RE.match(value):
        return KIND_TELEGRAM

    host = _url_host(value)
    if not host or _HH_HOST_RE.search(host):
        return None

    if _FORM_URL_HOST_RE.search(host):
        return KIND_FORM

    return KIND_EXTERNAL


def extract_vacancy_links(description: str | None) -> list[dict[str, str]]:
    """Достаёт из HTML-описания ссылки и контакты для ручного прохода.

    Возвращает список ``{"kind": ..., "value": ...}`` без дублей, в порядке
    появления в тексте. Внутренние ссылки hh.ru отбрасываются.
    """
    if not description:
        return []

    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str) -> None:
        value = value.strip().rstrip(".,;:)")
        key = (kind, value.lower())
        if value and key not in seen:
            seen.add(key)
            found.append({"kind": kind, "value": value})

    for raw_href in _HREF_RE.findall(description):
        href = html.unescape(raw_href).strip()
        if mailto := _MAILTO_RE.match(href):
            add(KIND_EMAIL, mailto.group(1))
            continue
        if tel := _TEL_RE.match(href):
            add(KIND_PHONE, tel.group(1).replace("%20", " "))
            continue
        href = unwrap_hh_redirect(href)
        if kind := classify_url(href):
            add(kind, href)

    text = html.unescape(re.sub(r"<[^>]+>", " ", description))

    # Каждый тип ищется в тексте с уже вырезанными предыдущими совпадениями,
    # иначе один и тот же URL попадется дважды в разных нормализациях
    for match in _BARE_URL_RE.finditer(text):
        url = match.group(0)
        if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE):
            url = "https://" + url
        url = unwrap_hh_redirect(url)
        if kind := classify_url(url):
            add(kind, url)
    text = _BARE_URL_RE.sub(" ", text)

    for match in _DOMAIN_PATH_URL_RE.finditer(text):
        if kind := classify_url("https://" + match.group(0)):
            add(kind, "https://" + match.group(0))
    text = _DOMAIN_PATH_URL_RE.sub(" ", text)

    for match in _EMAIL_RE.finditer(text):
        add(KIND_EMAIL, match.group(0))
    text = _EMAIL_RE.sub(" ", text)

    for match in _PHONE_RE.finditer(text):
        add(KIND_PHONE, match.group(0))

    for match in _TG_HANDLE_RE.finditer(text):
        add(KIND_TELEGRAM, f"https://t.me/{match.group(1)}")

    return found


def vacancy_response_link(vacancy: dict[str, Any]) -> str | None:
    """Внешний URL отклика (анкета на стороне работодателя), если есть."""
    for key in ("response_url", "adv_response_url"):
        url = (vacancy.get(key) or "").strip()
        if url:
            return url
    return None
