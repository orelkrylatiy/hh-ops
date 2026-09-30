from __future__ import annotations

from .base import BaseModel


# Заполняется из описания вакансии и response_url, не из hh API напрямую
class VacancyLinkModel(BaseModel):
    vacancy_id: int
    value: str
    kind: str = "external"
    source: str | None = None
