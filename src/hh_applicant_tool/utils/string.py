from __future__ import annotations

import random
import re
from typing import Any


def shorten(s: str, limit: int = 75, ellipsis: str = "…") -> str:
    return s[:limit] + bool(s[limit:]) * ellipsis


# Смайлы уместны в неформальных мессенджерах, но не в текстах работодателю:
# письма и ответы уходят массово, эмодзи там выглядят как бот/несерьёзность.
_EMOJI_RE = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff\ufe0f]")
_CLASSIC_SMILEY_RE = re.compile(r"[:;=8xX][-'^o]*[)(DPp]|[(][-'^o]*[:;=]")


def contains_smiley(text: str) -> bool:
    """True, если в тексте эмодзи, классический смайлик или голая скобка-улыбка.

    Голая ")" без парной "(" — это «спасибо)», её и ловим; нумерация «8)»
    (цифра перед скобкой) смайлом не считается.
    """
    if not text:
        return False
    if _EMOJI_RE.search(text) or _CLASSIC_SMILEY_RE.search(text):
        return True
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                previous = text[:index].rstrip()
                if not previous.endswith(tuple("0123456789")):
                    return True
            else:
                depth -= 1
    return False


def rand_text(s: str) -> str:
    while (
        temp := re.sub(
            r"{([^{}]+)}",
            lambda m: random.choice(
                m.group(1).split("|"),
            ),
            s,
        )
    ) != s:
        s = temp
    return s


def render_template(template: str, placeholders: dict[str, str], name: str = "шаблоне") -> str:
    """Подставляет плейсхолдеры `%(имя)s`, а неизвестный плейсхолдер
    превращает из невнятного `KeyError: 'имя'` в понятную ошибку."""
    try:
        return template % placeholders
    except KeyError as ex:
        raise ValueError(
            f"Неизвестный плейсхолдер %({ex.args[0]})s в {name}. "
            f"Доступные: {', '.join(sorted(placeholders))}"
        ) from ex


def bool2str(v: bool) -> str:
    return str(v).lower()


# К удалению
def list2str(items: list[Any] | None) -> str:
    return ",".join(f"{v}" for v in items) if items else ""


def unescape_string(text: str) -> str:
    if not text:
        return ""
    return text.replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t").replace(r"\\", "\\")


def br2nl(s: str) -> str:
    return re.sub(r"<br\b[^>]*\/?>", "\n", s, flags=re.I)


def strip_tags(content: str) -> str:
    content = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1\s*>",
        "",
        content,
        flags=re.I | re.S,
    )
    content = br2nl(content)
    content = re.sub(r"<!--.*?-->", "", content, flags=re.S)
    content = re.sub(r"<[^>]+>", "", content)
    # content = re.sub(r"\s+", " ", content)
    return content.strip()
