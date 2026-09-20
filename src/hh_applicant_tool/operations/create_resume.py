from __future__ import annotations

import argparse
import logging
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..api import ApiError
from ..main import BaseNamespace, BaseOperation
from ..utils import json
from ..utils.resume_md import parse_resume_md

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)


class ResumeTemplateError(ValueError):
    """Raised when a resume template cannot be safely converted to an HH payload."""


def _load_template(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".toml":
        with path.open("rb") as file:
            data = tomllib.load(file)
    elif suffix in {".md", ".markdown"}:
        data = parse_resume_md(path.read_text(encoding="utf-8"))
    else:
        raise ResumeTemplateError(
            f"Неподдерживаемый формат шаблона: {path.suffix or '<без расширения>'}. "
            "Используйте .md, .markdown или .toml"
        )

    if not isinstance(data, dict) or not data:
        raise ResumeTemplateError("Шаблон резюме должен давать непустой объект")
    return data


def _drop_nulls(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: _drop_nulls(value) for key, value in obj.items() if value is not None}
    if isinstance(obj, list):
        return [_drop_nulls(value) for value in obj if value is not None]
    return obj


def _normalize_label(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _suggestion_label(item: dict[str, Any]) -> str:
    return str(item.get("text") or item.get("name") or "").strip()


def _resolve_one_suggestion(
    api_client: Any,
    endpoint: str,
    text: str,
    path: str,
) -> dict[str, str]:
    try:
        response = api_client.get(endpoint, text=text)
    except ApiError as exc:
        raise ResumeTemplateError(
            f"Не удалось разрешить значение {path}={text!r} через {endpoint}: {exc}"
        ) from exc

    if not isinstance(response, dict):
        raise ResumeTemplateError(f"{endpoint} вернул неожиданный формат ответа для {path}")

    raw_items = response.get("items")
    if not isinstance(raw_items, list):
        raise ResumeTemplateError(f"{endpoint} не вернул список items для {path}")

    items = [item for item in raw_items if isinstance(item, dict) and item.get("id") is not None]
    if not items:
        raise ResumeTemplateError(f"HH не нашёл значение {path}={text!r}")

    target = _normalize_label(text)
    exact = [item for item in items if _normalize_label(_suggestion_label(item)) == target]
    if len(exact) == 1:
        selected = exact[0]
    elif len(exact) > 1:
        ids = ", ".join(str(item.get("id")) for item in exact)
        raise ResumeTemplateError(
            f"HH вернул несколько точных совпадений для {path}={text!r}: {ids}"
        )
    elif len(items) == 1:
        selected = items[0]
        logger.warning(
            "HH suggestion for %s=%r is not exact: %r",
            path,
            text,
            _suggestion_label(selected),
        )
    else:
        labels = ", ".join(_suggestion_label(item) or str(item.get("id")) for item in items[:5])
        raise ResumeTemplateError(f"Неоднозначное значение {path}={text!r}. HH предложил: {labels}")

    return {"id": str(selected["id"])}


def _resolve_suggests(api_client: Any, obj: Any, path: str = "$") -> Any:
    """Replace parser {_suggest, text} sentinels with deterministic HH IDs."""
    if isinstance(obj, dict):
        if "_suggest" in obj:
            endpoint = str(obj.get("_suggest") or "").strip()
            text = str(obj.get("text") or "").strip()
            if not endpoint or not text:
                raise ResumeTemplateError(f"Некорректная suggestion-ссылка в {path}")
            return _resolve_one_suggestion(api_client, endpoint, text, path)
        return {
            key: _resolve_suggests(api_client, value, f"{path}.{key}") for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [
            _resolve_suggests(api_client, value, f"{path}[{index}]")
            for index, value in enumerate(obj)
        ]
    return obj


def _flatten_industries(tree: Any) -> dict[str, list[str]]:
    if not isinstance(tree, list):
        raise ResumeTemplateError("/industries вернул неожиданный формат")

    result: dict[str, list[str]] = {}
    for parent in tree:
        if not isinstance(parent, dict):
            continue
        nested = parent.get("industries")
        children = nested if isinstance(nested, list) else []
        for item in [parent, *children]:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            name = _normalize_label(item.get("name"))
            if not name:
                continue
            result.setdefault(name, []).append(str(item["id"]))
    return result


def _resolve_industries(api_client: Any, experience: Any) -> None:
    if not isinstance(experience, list):
        raise ResumeTemplateError("Поле experience должно быть списком")

    unresolved: list[tuple[dict[str, Any], str, str]] = []
    for exp_index, exp in enumerate(experience):
        if not isinstance(exp, dict):
            raise ResumeTemplateError(f"experience[{exp_index}] должен быть объектом")
        industries = exp.get("industries", [])
        if not isinstance(industries, list):
            raise ResumeTemplateError(f"experience[{exp_index}].industries должен быть списком")
        for ind_index, industry in enumerate(industries):
            if not isinstance(industry, dict):
                raise ResumeTemplateError(
                    f"experience[{exp_index}].industries[{ind_index}] должен быть объектом"
                )
            if industry.get("id") is not None:
                continue
            name = str(industry.get("name") or "").strip()
            if not name:
                raise ResumeTemplateError(
                    f"experience[{exp_index}].industries[{ind_index}] не содержит id или name"
                )
            unresolved.append(
                (
                    industry,
                    name,
                    f"experience[{exp_index}].industries[{ind_index}]",
                )
            )

    if not unresolved:
        return

    try:
        tree = api_client.get("/industries")
    except ApiError as exc:
        raise ResumeTemplateError(f"Не удалось загрузить справочник отраслей: {exc}") from exc

    by_name = _flatten_industries(tree)
    for industry, name, path in unresolved:
        ids = by_name.get(_normalize_label(name), [])
        if not ids:
            raise ResumeTemplateError(f"Отрасль не найдена: {path}={name!r}")
        if len(ids) > 1:
            raise ResumeTemplateError(
                f"Неоднозначная отрасль {path}={name!r}: совпали ID {', '.join(ids)}"
            )
        industry.clear()
        industry["id"] = ids[0]


def _find_internal_suggestions(obj: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        if "_suggest" in obj:
            found.append(path)
        for key, value in obj.items():
            found.extend(_find_internal_suggestions(value, f"{path}.{key}"))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(_find_internal_suggestions(value, f"{path}[{index}]"))
    return found


def _validate_payload(payload: dict[str, Any]) -> None:
    required = ("first_name", "last_name", "title", "area", "professional_roles")
    missing = [field for field in required if not payload.get(field)]
    if missing:
        raise ResumeTemplateError("В шаблоне отсутствуют обязательные поля: " + ", ".join(missing))

    unresolved = _find_internal_suggestions(payload)
    if unresolved:
        raise ResumeTemplateError(
            "В payload остались неразрешённые HH suggestions: " + ", ".join(unresolved)
        )

    area = payload.get("area")
    if not isinstance(area, dict) or not area.get("id"):
        raise ResumeTemplateError("Поле area должно быть разрешено в объект с id")

    roles = payload.get("professional_roles")
    if not isinstance(roles, list) or not roles:
        raise ResumeTemplateError("Нужна хотя бы одна professional role")
    if any(not isinstance(role, dict) or not role.get("id") for role in roles):
        raise ResumeTemplateError("Каждая professional role должна содержать id")

    experience = payload.get("experience", [])
    if experience:
        if not isinstance(experience, list):
            raise ResumeTemplateError("Поле experience должно быть списком")
        for index, item in enumerate(experience):
            if not isinstance(item, dict):
                raise ResumeTemplateError(f"experience[{index}] должен быть объектом")
            for field in ("company", "position", "start"):
                if not item.get(field):
                    raise ResumeTemplateError(
                        f"experience[{index}] не содержит обязательное поле {field}"
                    )


def _resume_ids(tool: HHApplicantTool) -> set[str] | None:
    try:
        return {
            str(item["id"])
            for item in tool.get_resumes()
            if isinstance(item, dict) and item.get("id")
        }
    except (ApiError, KeyError, TypeError) as exc:
        logger.warning(
            "Не удалось получить список резюме для определения нового ID: %s",
            exc,
        )
        return None


def _detect_created_resume_id(
    tool: HHApplicantTool,
    before_ids: set[str] | None,
) -> str | None:
    if before_ids is None:
        return None

    after_ids = _resume_ids(tool)
    if after_ids is None:
        return None

    new_ids = sorted(after_ids - before_ids)
    if len(new_ids) == 1:
        return new_ids[0]
    if len(new_ids) > 1:
        logger.error(
            "После создания появилось несколько новых резюме (%s); "
            "автопубликация отключена, чтобы не опубликовать не то резюме",
            ", ".join(new_ids),
        )
    return None


class Namespace(BaseNamespace):
    template: Path
    dry_run: bool
    publish: bool


class Operation(BaseOperation):
    """Создать новое HH-резюме из Markdown/TOML шаблона."""

    __aliases__: list[str] = []

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "template",
            type=Path,
            help="Путь до шаблона резюме (.md/.markdown/.toml)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Разрешить read-only suggest-запросы и показать финальный payload без POST /resumes"
            ),
        )
        parser.add_argument(
            "--publish",
            action="store_true",
            help=("Опубликовать созданное резюме, только если его ID определён однозначно"),
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> int | None:
        if not args.template.exists():
            logger.error("Файл шаблона не найден: %s", args.template)
            return 1
        if not args.template.is_file():
            logger.error("Шаблон должен быть обычным файлом: %s", args.template)
            return 1

        try:
            data = _load_template(args.template)
            payload = _resolve_suggests(tool.api_client, data)
            if not isinstance(payload, dict):
                raise ResumeTemplateError("Шаблон должен преобразовываться в объект")
            if "experience" in payload:
                _resolve_industries(tool.api_client, payload["experience"])
            payload = _drop_nulls(payload)
            _validate_payload(payload)
        except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as exc:
            logger.error("Ошибка шаблона резюме: %s", exc)
            return 1

        if args.dry_run:
            print(json.dumps(payload, indent=2))
            if args.publish:
                logger.info("--publish проигнорирован в --dry-run; внешних изменений нет")
            return None

        before_ids = _resume_ids(tool)

        try:
            result = tool.api_client.post("/resumes", payload, as_json=True)
        except ApiError as exc:
            logger.error("Ошибка при создании резюме: %s", exc)
            return 1

        resume_id: str | None = None
        if isinstance(result, dict) and result.get("id") is not None:
            resume_id = str(result["id"])
        if resume_id is None:
            resume_id = _detect_created_resume_id(tool, before_ids)

        print("✅ Резюме создано")
        if resume_id:
            print(f"   https://hh.ru/resume/{resume_id}")
        else:
            logger.warning(
                "HH не вернул однозначный ID созданного резюме; "
                "резюме создано, но автоматическая публикация недоступна"
            )

        if not args.publish:
            return None
        if not resume_id:
            logger.error("Не удалось безопасно определить ID; публикация пропущена")
            return 1

        try:
            tool.api_client.post(f"/resumes/{resume_id}/publish")
        except ApiError as exc:
            logger.error(
                "Резюме создано, но публикация завершилась ошибкой: %s",
                exc,
            )
            return 1

        print("✅ Резюме опубликовано")
        return None
