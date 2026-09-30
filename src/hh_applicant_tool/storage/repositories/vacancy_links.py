from __future__ import annotations

from ..models.vacancy_link import VacancyLinkModel
from .base import BaseRepository


class VacancyLinksRepository(BaseRepository):
    __table__ = "vacancy_links"
    model = VacancyLinkModel
    conflict_columns = ("vacancy_id", "value")
