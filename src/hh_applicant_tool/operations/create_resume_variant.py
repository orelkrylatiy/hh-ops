from __future__ import annotations

import argparse
import copy
import logging
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..api import ApiError
from ..main import BaseNamespace, BaseOperation
from ..utils import json
from ..utils.resume_aliases import (
    get_resume_aliases,
    normalize_resume_alias,
    save_resume_aliases,
)
from .create_resume import (
    ResumeTemplateError,
    _detect_created_resume_id,
    _drop_nulls,
    _resolve_industries,
    _resolve_suggests,
    _resume_ids,
    _validate_payload,
)

if TYPE_CHECKING:
    from ..main import HHApplicantTool

logger = logging.getLogger(__package__)

WRITABLE_SOURCE_FIELDS = {
    "last_name",
    "first_name",
    "middle_name",
    "title",
    "birth_date",
    "gender",
    "area",
    "metro",
    "salary",
    "contact",
    "professional_roles",
    "employments",
    "schedules",
    "relocation",
    "business_trip_readiness",
    "citizenship",
    "work_ticket",
    "driver_license_types",
    "has_vehicle",
    "travel_time",
    "language",
    "skill_set",
    "skills",
    "experience",
    "education",
    "recommendation",
    "site",
}

READ_ONLY_NESTED_KEYS = {
    "formatted",
}


def _load_variant(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".toml":
        with path.open("rb") as file:
            data = tomllib.load(file)
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ResumeTemplateError(
            f"Неподдерживаемый формат variant: {path.suffix or '<без расширения>'}. "
            "Используйте .toml или .json"
        )

    if not isinstance(data, dict) or not data:
        raise ResumeTemplateError("Resume variant должен быть непустым объектом")
    return data


def _sanitize_source_value(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("id") is not None and set(value) <= {"id", "name"}:
            return {"id": str(value["id"])}
        return {
            key: _sanitize_source_value(item)
            for key, item in value.items()
            if key not in READ_ONLY_NESTED_KEYS and item is not None
        }
    if isinstance(value, list):
        return [_sanitize_source_value(item) for item in value if item is not None]
    return value


def _source_payload(full_resume: dict[str, Any]) -> dict[str, Any]:
    payload = {
        field: _sanitize_source_value(copy.deepcopy(full_resume[field]))
        for field in WRITABLE_SOURCE_FIELDS
        if field in full_resume and full_resume[field] is not None
    }
    return _drop_nulls(payload)


def _merge_variant(
    base_payload: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(base_payload)
    for key, value in variant.items():
        if key.startswith("_"):
            continue
        merged[key] = copy.deepcopy(value)
    return merged


def _resume_status_id(resume: dict[str, Any]) -> str:
    status = resume.get("status")
    if isinstance(status, dict):
        return str(status.get("id") or "")
    return ""


def _select_source_resume_id(
    tool: HHApplicantTool,
    explicit_resume_id: str | None,
) -> str:
    resumes = [
        item
        for item in tool.get_resumes()
        if isinstance(item, dict) and item.get("id") is not None
    ]
    if not resumes:
        raise ResumeTemplateError("В профиле нет резюме, которое можно использовать как источник")

    by_id = {str(item["id"]): item for item in resumes}

    if explicit_resume_id:
        resume_id = explicit_resume_id.strip()
        if resume_id not in by_id:
            raise ResumeTemplateError(f"Исходное резюме не найдено: {resume_id}")
        return resume_id

    aliases = get_resume_aliases(tool.config)
    primary_id = aliases.get("primary")
    if primary_id and primary_id in by_id:
        return primary_id

    published = [
        str(item["id"])
        for item in resumes
        if _resume_status_id(item) == "published"
    ]
    if len(published) == 1:
        return published[0]
    if len(resumes) == 1:
        return str(resumes[0]["id"])

    raise ResumeTemplateError(
        "В профиле несколько резюме и alias 'primary' не задан. "
        "Укажите --source-resume-id явно."
    )


class Namespace(BaseNamespace):
    variant: Path
    alias: str
    source_resume_id: str | None
    publish: bool
    dry_run: bool
    replace_alias: bool


class Operation(BaseOperation):
    """Создать вариант резюме, наследуя личные данные из существующего HH resume."""

    __aliases__: list[str] = []

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "variant",
            type=Path,
            help="TOML/JSON patch с полями, которые надо заменить в исходном резюме",
        )
        parser.add_argument(
            "--alias",
            required=True,
            help="Локальное имя нового резюме, например ai-engineer",
        )
        parser.add_argument(
            "--source-resume-id",
            help="Явный ID исходного резюме; иначе используется alias primary или единственное resume",
        )
        parser.add_argument(
            "--publish",
            action="store_true",
            help="Опубликовать созданный вариант",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Собрать и провалидировать payload без POST /resumes и изменения config",
        )
        parser.add_argument(
            "--replace-alias",
            action="store_true",
            help="Разрешить заменить уже существующий resume alias новым ID",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> int | None:
        if not args.variant.exists() or not args.variant.is_file():
            logger.error("Resume variant не найден: %s", args.variant)
            return 1

        try:
            alias = normalize_resume_alias(args.alias)
            aliases_before = get_resume_aliases(tool.config)
            if alias in aliases_before and not args.replace_alias:
                raise ResumeTemplateError(
                    f"Resume alias {alias!r} уже существует. "
                    "Используйте --replace-alias, если хотите создать новую версию."
                )

            source_resume_id = _select_source_resume_id(
                tool,
                args.source_resume_id,
            )
            full_resume = tool.api_client.get(f"/resumes/{source_resume_id}")
            if not isinstance(full_resume, dict):
                raise ResumeTemplateError("HH вернул неожиданный формат исходного резюме")

            base_payload = _source_payload(full_resume)
            variant = _load_variant(args.variant)
            payload = _merge_variant(base_payload, variant)
            payload = _resolve_suggests(tool.api_client, payload)
            if "experience" in payload:
                _resolve_industries(tool.api_client, payload["experience"])
            payload = _drop_nulls(payload)
            _validate_payload(payload)
        except (ApiError, OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as exc:
            logger.error("Не удалось подготовить resume variant: %s", exc)
            return 1

        if args.dry_run:
            print(
                json.dumps(
                    {
                        "source_resume_id": source_resume_id,
                        "alias": alias,
                        "payload": payload,
                    },
                    indent=2,
                )
            )
            if args.publish:
                logger.info("--publish проигнорирован в --dry-run")
            return None

        before_ids = _resume_ids(tool)

        try:
            result = tool.api_client.post("/resumes", payload, as_json=True)
        except ApiError as exc:
            logger.error("Ошибка при создании resume variant: %s", exc)
            return 1

        created_resume_id: str | None = None
        if isinstance(result, dict) and result.get("id") is not None:
            created_resume_id = str(result["id"])
        if created_resume_id is None:
            created_resume_id = _detect_created_resume_id(tool, before_ids)

        if not created_resume_id:
            logger.error(
                "Резюме могло быть создано, но новый ID не удалось определить однозначно. "
                "Alias и publish не изменены."
            )
            return 1

        if args.publish:
            try:
                tool.api_client.post(f"/resumes/{created_resume_id}/publish")
            except ApiError as exc:
                logger.error(
                    "Resume variant создан (%s), но публикация не удалась: %s",
                    created_resume_id,
                    exc,
                )
                return 1

        alias_updates = {alias: created_resume_id}
        if "primary" not in aliases_before:
            alias_updates["primary"] = source_resume_id
        save_resume_aliases(tool.config, alias_updates)

        print("✅ Resume variant создан")
        print(f"   alias: {alias}")
        print(f"   resume_id: {created_resume_id}")
        print(f"   https://hh.ru/resume/{created_resume_id}")
        if args.publish:
            print("✅ Resume variant опубликован")
        return None
