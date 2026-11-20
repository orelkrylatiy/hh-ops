from __future__ import annotations

from datetime import datetime

from .base import BaseModel, mapped


class ResumeModel(BaseModel):
    id: str
    title: str
    url: str | None = None
    alternate_url: str | None = None
    status_id: str | None = mapped(path="status.id", default=None)
    status_name: str | None = mapped(path="status.name", default=None)
    can_publish_or_update: bool = False
    # HH returns total_views/new_views at the top level of /resumes/mine.
    # None means the API did not provide a counter; it must not look like zero.
    total_views: int | None = None
    new_views: int | None = None
    # Set by ResumesRepository only when the API response contains both counters.
    views_updated_at: str | None = mapped(skip_src=True, default=None)
    created_at: datetime | None = None
    updated_at: datetime | None = None
