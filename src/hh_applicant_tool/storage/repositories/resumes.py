from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ..models.base import BaseModel
from ..models.resume import ResumeModel
from .base import BaseRepository


class ResumesRepository(BaseRepository):
    __table__ = "resumes"
    model = ResumeModel

    def _with_title(self, item: BaseModel | Mapping[str, Any]) -> ResumeModel:
        resume = (
            self.model.from_api({**item, "title": item.get("title") or ""})
            if isinstance(item, Mapping)
            else item
        )
        if not isinstance(resume, ResumeModel):
            raise TypeError("Expected a resume model")
        if resume.title and resume.title.strip():
            return resume
        # HH can return a partial/removed resume without its title. Preserve
        # the known title rather than failing the entire batch or erasing it.
        existing = self.get(resume.id)
        title = existing.title if existing and existing.title else "Резюме без названия"
        return replace(resume, title=title)

    def save(self, obj: BaseModel | Mapping[str, Any], /, **kwargs: Any) -> None:
        super().save(self._with_title(obj), **kwargs)

    def save_batch(self, items: list[BaseModel | Mapping[str, Any]], /, **kwargs: Any) -> None:
        super().save_batch([self._with_title(item) for item in items], **kwargs)
