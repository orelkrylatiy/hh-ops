from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RULES_FILE = ROOT / "rules" / "apply-hard-filter.regex"
APPLY_SCRIPT = ROOT / "scripts" / "apply.sh"


def _active_patterns() -> list[str]:
    return [
        line.strip()
        for line in RULES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _compiled_filter() -> re.Pattern[str]:
    patterns = _active_patterns()
    assert patterns
    return re.compile("|".join(patterns), re.IGNORECASE)


def test_every_hard_filter_line_is_a_valid_regex() -> None:
    for pattern in _active_patterns():
        re.compile(pattern, re.IGNORECASE)


@pytest.mark.parametrize(
    "vacancy_title",
    [
        "C++ Developer",
        "C# Developer",
        "Qt Developer",
        "Junior Frontend Developer",
        "Frontend Intern",
        "Frontend Trainee",
        "Джуниор frontend-разработчик",
        "Стажёр frontend-разработчик",
        "Младший frontend-разработчик",
        "Java Developer",
        "Python Developer",
        "Golang Developer",
        "QA Engineer",
        "DevOps Engineer",
    ],
)
def test_default_hard_filter_rejects_known_non_target_titles(
    vacancy_title: str,
) -> None:
    assert _compiled_filter().search(vacancy_title)


@pytest.mark.parametrize(
    "vacancy_title",
    [
        "Senior Frontend Developer React TypeScript",
        "Frontend Developer JavaScript React",
        "Middle React Developer",
        "TypeScript Engineer",
    ],
)
def test_default_hard_filter_does_not_block_target_frontend_titles(
    vacancy_title: str,
) -> None:
    assert not _compiled_filter().search(vacancy_title)


def test_stop_words_are_configured_outside_apply_script() -> None:
    script = APPLY_SCRIPT.read_text(encoding="utf-8")

    # Filtering is opt-in; the rules live in a separate file and the script
    # accepts its path through APPLY_HARD_FILTER_FILE or --hard-filter-file.
    assert 'HARD_FILTER_FILE="${APPLY_HARD_FILTER_FILE:-}"' in script
    assert '--hard-filter-file' in script
    assert "junior|стажир|bitrix" not in script
    assert 'EXCLUDED_FILTER="${EXCLUDED_FILTER:-}"' in script
