from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

QUERIES_PATH: Path = Path(__file__).parent / "queries"
MIGRATION_PATH: Path = QUERIES_PATH / "migrations"


logger: logging.Logger = logging.getLogger(__package__)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _apply_additive_schema_updates(conn: sqlite3.Connection) -> None:
    """Upgrade pre-migration profile databases without dropping user data.

    The project historically only replayed ``schema.sql``, which cannot add a
    column to an already-created SQLite table. Keep additive upgrades here so
    every profile is upgraded on its next normal storage initialization.
    """
    if "employer_id" not in _column_names(conn, "vacancies"):
        conn.execute("ALTER TABLE vacancies ADD COLUMN employer_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vacancies_employer_id "
        "ON vacancies(employer_id)"
    )
    if "views_updated_at" not in _column_names(conn, "resumes"):
        conn.execute("ALTER TABLE resumes ADD COLUMN views_updated_at DATETIME")


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema and apply safe additive upgrades for existing profiles."""
    changes_before = conn.total_changes

    conn.executescript(
        (QUERIES_PATH / "schema.sql").read_text(encoding="utf-8")
    )
    _apply_additive_schema_updates(conn)

    if conn.total_changes > changes_before:
        logger.info("Применена схема бд")


def list_migrations() -> list[str]:
    """Выводит имена миграций без расширения, отсортированные по дате"""
    if not MIGRATION_PATH.exists():
        return []
    return sorted([f.stem for f in MIGRATION_PATH.glob("*.sql")])


def apply_migration(conn: sqlite3.Connection, name: str) -> None:
    """Находит файл по имени и выполняет его содержимое"""
    conn.executescript(
        (MIGRATION_PATH / f"{name}.sql").read_text(encoding="utf-8")
    )
