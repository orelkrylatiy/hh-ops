from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ops" / "daily_report.py"


def _run_report(root: pathlib.Path, date: str = "2026-09-09") -> dict:
    output_dir = root / "ops"
    env = os.environ.copy()
    env["CONFIG_DIR"] = str(root / "config")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--date",
            date,
            "--timezone",
            "UTC",
            "--root",
            str(root),
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report_path = output_dir / "daily" / f"{date}.json"
    assert report_path.exists()
    assert (output_dir / "latest.json").read_text(encoding="utf-8") == report_path.read_text(
        encoding="utf-8"
    )
    return json.loads(report_path.read_text(encoding="utf-8"))


def _create_profile_db(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE vacancies (id INTEGER PRIMARY KEY, name TEXT, alternate_url TEXT)"
        )
        conn.execute(
            "INSERT INTO vacancies VALUES (123456, 'Secret vacancy title', 'https://hh.ru/vacancy/123456')"
        )
        conn.execute(
            "CREATE TABLE negotiations (id INTEGER PRIMARY KEY, chat_id INTEGER, state TEXT)"
        )
        conn.execute("INSERT INTO negotiations VALUES (987654, 555555, 'active')")
        conn.commit()
    finally:
        conn.close()


def test_daily_report_collects_metrics_without_copying_raw_data(
    tmp_path: pathlib.Path,
) -> None:
    logs = tmp_path / "logs" / "profiles"
    logs.mkdir(parents=True)

    (logs / "account1-reply.log").write_text(
        "\n".join(
            [
                "[2026-09-09 10:00:00] HH_RUN_START profile=account1 command=reply mode=live",
                "2026-09-09 10:00:01 [WARNING] Rejected AI reply for chat secret-chat-777",
                "Работодатель: secret employer message that must never be exported",
                '{"candidates":3,"planned":2,"sent":1,"stale":1,"skipped":0,"ignored":2,"manual":1,"errors":0,"fallback":1}',
                "[2026-09-09 10:00:03] HH_RUN_END profile=account1 command=reply mode=live status=0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (logs / "account2-apply.log").write_text(
        "\n".join(
            [
                "[2026-09-09 09:10:00] HH_RUN_START profile=account2 command=apply mode=live",
                "📨 Отправили отклик на вакансию https://hh.ru/vacancy/secret-apply-url",
                "Требуется капча: https://hh.ru/captcha/secret-token",
                "AI не сгенерировал письмо для 2 вакансий; запуск помечен как неуспешный",
                "[2026-09-09 09:15:00] HH_RUN_END profile=account2 command=apply mode=live status=1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    profile_log = tmp_path / "config" / "account1" / "log.txt"
    profile_log.parent.mkdir(parents=True, exist_ok=True)
    profile_log.write_text(
        "2026-09-09 10:00:01 - ERROR - recruiter@example.com secret traceback context\n",
        encoding="utf-8",
    )
    _create_profile_db(tmp_path / "config" / "account1" / "data")

    report = _run_report(tmp_path)

    assert report["schema_version"] == 1
    assert report["totals"]["runs_started"] == 2
    assert report["totals"]["runs_failed"] == 1
    assert report["totals"]["applications_sent"] == 1
    assert report["totals"]["application_ai_errors"] == 2
    assert report["totals"]["reply_candidates"] == 3
    assert report["totals"]["replies_sent"] == 1
    assert report["totals"]["replies_stale"] == 1
    assert report["totals"]["reply_fallbacks"] == 1
    assert report["totals"]["replies_ignored"] == 2
    assert report["totals"]["replies_manual"] == 1

    account1 = report["profiles"]["account1"]
    assert account1["runs"]["reply"]["started"] == 1
    assert account1["reply"]["fallback"] == 1
    assert account1["reply"]["ignored"] == 2
    assert account1["reply"]["manual"] == 1
    assert account1["database"]["tables"]["vacancies"] == 1
    assert account1["database"]["tables"]["negotiations"] == 1

    account2 = report["profiles"]["account2"]
    assert account2["runs"]["apply"]["failed"] == 1
    assert account2["apply"]["sent"] == 1
    assert account2["apply"]["captcha_required"] == 1
    assert account2["apply"]["ai_errors"] == 2

    serialized = json.dumps(report, ensure_ascii=False)
    for secret in (
        "secret-chat-777",
        "secret employer message",
        "secret-apply-url",
        "secret-token",
        "recruiter@example.com",
        "Secret vacancy title",
        "123456",
        "987654",
        "555555",
    ):
        assert secret not in serialized

    assert report["privacy"] == {
        "aggregate_only": True,
        "chat_text_included": False,
        "employer_names_included": False,
        "ids_included": False,
        "raw_log_lines_included": False,
        "secrets_included": False,
        "urls_included": False,
        "vacancy_titles_included": False,
    }


def test_daily_report_handles_empty_runtime(tmp_path: pathlib.Path) -> None:
    report = _run_report(tmp_path)

    assert report["profiles"] == {}
    assert report["logs"]["files_scanned"] == 0
    assert report["logs"]["lines_for_day"] == 0
    assert report["totals"]["runs_started"] == 0
    assert report["totals"]["replies_sent"] == 0
    assert report["totals"]["applications_sent"] == 0
