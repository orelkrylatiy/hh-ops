#!/usr/bin/env python3
"""Probe the framework's own provider and validation without sending to HH."""

from __future__ import annotations

import argparse
import sys

from hh_applicant_tool.automation.reply_worker import reply_quality_issues
from hh_applicant_tool.communication import FrameworkWriter
from hh_applicant_tool.operations._apply_vacancies_apply_flow import letter_quality_issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--purpose", choices=("application", "chat"), required=True)
    args = parser.parse_args()
    writer = FrameworkWriter()
    try:
        if args.purpose == "application":
            result = writer.application(
                vacancy_name="Frontend разработчик",
                employer_name="Тестовая компания",
                source_context=(
                    "Вакансия: Frontend разработчик. Задачи: React, TypeScript, "
                    "пользовательские интерфейсы. Резюме: пять лет разработки "
                    "интерфейсов на React и TypeScript, опыт в финтехе."
                ),
            )
            issues = letter_quality_issues(result.text)
        else:
            result = writer.chat_reply(
                context=["Работодатель: Работали с React и TypeScript?"],
                initiated_by_us=True,
                vacancy_name="Frontend разработчик",
                employer_name="Тестовая компания",
                latest_message_text="Работали с React и TypeScript?",
                resume_context="Пять лет разработки интерфейсов на React и TypeScript.",
            )
            issues = reply_quality_issues(result.text)
    except Exception as exc:
        print(f"Framework probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if issues:
        print(f"Framework probe failed local quality: {', '.join(issues)}", file=sys.stderr)
        return 1
    print(f"Framework probe OK: purpose={args.purpose} provider={result.provider} chars={len(result.text)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
