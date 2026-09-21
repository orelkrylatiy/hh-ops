#!/usr/bin/env python3
"""Build a privacy-safe daily HH automation snapshot from local logs and SQLite.

The report intentionally persists only counters/statuses. Raw log lines, vacancy
or employer names, chat text, URLs, ids, tokens, cookies and config values are
never copied to ``ops/``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Moscow"
DATABASE_FILENAME = "data"

PYTHON_LOG_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}:\d{2})(?:,\d+)?"
    r"(?: - | \[)(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)(?: - |\])"
)
RUN_MARKER_RE = re.compile(
    r"^\[(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}:\d{2})\] "
    r"HH_RUN_(?P<kind>START|END|SKIP) profile=(?P<profile>[A-Za-z0-9._-]+) "
    r"command=(?P<command>[A-Za-z0-9._-]+)"
    r"(?: mode=(?P<mode>[A-Za-z0-9._-]+))?"
    r"(?: status=(?P<status>\d+))?"
)
AI_ERROR_COUNT_RE = re.compile(
    r"AI не сгенерировал письмо для (?P<count>\d+) ваканс",
    re.IGNORECASE,
)

LOG_EVENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "auth_error": re.compile(
        r"\bAUTH_REQUIRED\b|not authorized|unauthorized|authorization required|HTTP 401|HTTP 403",
        re.IGNORECASE,
    ),
    "ai_error": re.compile(
        r"AI configuration error|AI failed|OpenAIError|AI не сгенерировал|LLM.*(?:error|failed)",
        re.IGNORECASE,
    ),
    "captcha_required": re.compile(r"Требуется капча|CaptchaRequired", re.IGNORECASE),
    "captcha_failed": re.compile(
        r"Не удалось решить капчу|Ошибка при решении капчи|captcha failed",
        re.IGNORECASE,
    ),
    "reply_quality_rejected": re.compile(r"Rejected AI reply", re.IGNORECASE),
    "reply_send_failed": re.compile(r"Failed to send chat|SEND_FAILED", re.IGNORECASE),
    "hh_cli_error": re.compile(r"HHCLIError|HH CLI error|invalid HH JSON", re.IGNORECASE),
    "rate_limit": re.compile(
        r"HTTP 429|rate limit|Достигли лимита на отклики|LimitExceeded",
        re.IGNORECASE,
    ),
    "timeout": re.compile(r"timed out|timeout", re.IGNORECASE),
    "database_locked": re.compile(r"database is locked", re.IGNORECASE),
    "traceback": re.compile(r"Traceback \(most recent call last\)", re.IGNORECASE),
    "profile_busy": re.compile(r"already being processed; skipped", re.IGNORECASE),
    "network_error": re.compile(
        r"ConnectionError|RequestException|connection refused|temporary failure|name or service not known",
        re.IGNORECASE,
    ),
}

TABLE_ALLOWLIST = (
    "vacancies",
    "negotiations",
    "skipped_vacancies",
    "employers",
    "resumes",
    "manual_chat_queue",
    "hot_lead_events",
)


def _timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        fallbacks = {
            "Europe/Moscow": 3,
            "Asia/Yekaterinburg": 5,
            "UTC": 0,
        }
        if name not in fallbacks:
            raise
        return timezone(timedelta(hours=fallbacks[name]), name=name)


def _resolve_date(spec: str, tz: tzinfo) -> date:
    today = datetime.now(tz).date()
    if spec == "today":
        return today
    if spec == "yesterday":
        return today - timedelta(days=1)
    return date.fromisoformat(spec)


def _git_revision(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None


def _profile_metrics() -> dict:
    return {
        "runs": {},
        "apply": {
            "sent": 0,
            "dry_run_planned": 0,
            "ai_errors": 0,
            "captcha_required": 0,
            "captcha_failed": 0,
            "quota_reached": 0,
        },
        "reply": {
            "candidates": 0,
            "planned": 0,
            "sent": 0,
            "stale": 0,
            "skipped": 0,
            "ignored": 0,
            "manual": 0,
            "errors": 0,
            "fallback": 0,
            "hot_candidates": 0,
            "hot_leads": 0,
            "hot_notified": 0,
            "hot_notify_failed": 0,
            "hot_ai_errors": 0,
        },
        "log_events": {},
        "database": {
            "exists": False,
            "read_error": False,
            "tables": {},
        },
    }


def _run_bucket(profile: dict, command: str) -> dict:
    runs = profile["runs"]
    if command not in runs:
        runs[command] = {"started": 0, "failed": 0, "skipped_busy": 0, "modes": {}}
    return runs[command]


def _discover_log_files(root: Path, config_dir: Path) -> list[Path]:
    files: set[Path] = set()
    logs_dir = root / "logs"
    if logs_dir.exists():
        files.update(path for path in logs_dir.rglob("*.log") if path.is_file())
        files.update(path for path in logs_dir.rglob("*.log.*") if path.is_file())
    if config_dir.exists():
        files.update(path for path in config_dir.rglob("log.txt*") if path.is_file())
    return sorted(files)


def _profile_from_log(path: Path, root: Path, config_dir: Path) -> str:
    try:
        relative_config = path.relative_to(config_dir)
    except ValueError:
        relative_config = None
    if relative_config is not None and path.name.startswith("log.txt"):
        return relative_config.parts[0] if len(relative_config.parts) > 1 else "default"

    try:
        relative_logs = path.relative_to(root / "logs" / "profiles")
    except ValueError:
        return "global"
    name = relative_logs.name
    for command in ("apply", "reply", "cleanup", "daily", "boost", "update", "refresh"):
        suffix = f"-{command}.log"
        if name.endswith(suffix):
            return name[: -len(suffix)] or "global"
    return "global"


def _parse_reply_summary(line: str) -> dict[str, int] | None:
    stripped = line.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    required = {"candidates", "planned", "sent", "stale", "skipped", "errors"}
    if not required.issubset(value):
        return None
    result: dict[str, int] = {}
    for key in (
        *sorted(required),
        "ignored",
        "manual",
        "fallback",
        "hot_candidates",
        "hot_leads",
        "hot_notified",
        "hot_notify_failed",
        "hot_ai_errors",
    ):
        raw = value.get(key, 0)
        if isinstance(raw, bool):
            raw = int(raw)
        if not isinstance(raw, (int, float)):
            return None
        result[key] = int(raw)
    return result


def _apply_line_metrics(line: str, metrics: dict) -> None:
    if "📨 Отправили отклик на вакансию" in line:
        metrics["sent"] += 1
    if "dry-run: would send a response" in line:
        metrics["dry_run_planned"] += 1
    if match := AI_ERROR_COUNT_RE.search(line):
        metrics["ai_errors"] += int(match.group("count"))
    if "Требуется капча" in line or "CaptchaRequired" in line:
        metrics["captcha_required"] += 1
    if (
        "Не удалось решить капчу" in line
        or "Ошибка при решении капчи" in line
        or "captcha failed" in line.lower()
    ):
        metrics["captcha_failed"] += 1
    if "Достигли лимита на отклики" in line or "Reached application quota" in line:
        metrics["quota_reached"] += 1


def _scan_logs(root: Path, config_dir: Path, target: date) -> tuple[dict, dict[str, dict]]:
    target_s = target.isoformat()
    levels: Counter[str] = Counter()
    events: Counter[str] = Counter()
    files_by_source: Counter[str] = Counter()
    profiles: dict[str, dict] = defaultdict(_profile_metrics)
    lines_for_day = 0

    for path in _discover_log_files(root, config_dir):
        if str(path).startswith(str(root / "logs" / "profiles")):
            source = "wrapper"
        elif str(path).startswith(str(config_dir)):
            source = "profile"
        elif path.name == "cron.log":
            source = "cron"
        else:
            source = "other"
        files_by_source[source] += 1

        file_profile = _profile_from_log(path, root, config_dir)
        active_target_run: tuple[str, str] | None = None
        current_date: str | None = None

        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue

        with handle:
            for line in handle:
                marker = RUN_MARKER_RE.match(line)
                if marker:
                    current_date = marker.group("date")
                    profile_name = marker.group("profile")
                    command = marker.group("command")
                    kind = marker.group("kind")
                    profile = profiles[profile_name]
                    bucket = _run_bucket(profile, command)

                    if current_date == target_s:
                        lines_for_day += 1
                        if kind == "START":
                            bucket["started"] += 1
                            mode = marker.group("mode") or "unknown"
                            bucket["modes"][mode] = bucket["modes"].get(mode, 0) + 1
                            active_target_run = (profile_name, command)
                        elif kind == "END":
                            if int(marker.group("status") or 0) != 0:
                                bucket["failed"] += 1
                            active_target_run = None
                        elif kind == "SKIP":
                            bucket["skipped_busy"] += 1
                            active_target_run = None
                    elif kind in {"START", "END", "SKIP"}:
                        active_target_run = None
                    continue

                python_log = PYTHON_LOG_RE.match(line)
                if python_log:
                    current_date = python_log.group("date")
                    if current_date == target_s:
                        lines_for_day += 1
                        levels[python_log.group("level").lower()] += 1
                elif active_target_run is not None:
                    lines_for_day += 1

                belongs_to_target = current_date == target_s or active_target_run is not None
                if not belongs_to_target:
                    continue

                profile_name = active_target_run[0] if active_target_run else file_profile
                profile = profiles[profile_name]

                for event_name, pattern in LOG_EVENT_PATTERNS.items():
                    if pattern.search(line):
                        events[event_name] += 1
                        profile_events = profile["log_events"]
                        profile_events[event_name] = profile_events.get(event_name, 0) + 1

                if active_target_run is not None:
                    _apply_line_metrics(line, profile["apply"])
                    if reply_summary := _parse_reply_summary(line):
                        for key, value in reply_summary.items():
                            profile["reply"][key] += value

    return (
        {
            "files_scanned": sum(files_by_source.values()),
            "files_by_source": dict(sorted(files_by_source.items())),
            "lines_for_day": lines_for_day,
            "levels": dict(sorted(levels.items())),
            "events": dict(sorted(events.items())),
        },
        dict(profiles),
    )


def _discover_profile_dirs(config_dir: Path) -> dict[str, Path]:
    profiles: dict[str, Path] = {}
    if not config_dir.exists():
        return profiles
    if (config_dir / DATABASE_FILENAME).exists():
        profiles["default"] = config_dir
    for child in sorted(config_dir.iterdir()):
        if child.is_dir() and (child / DATABASE_FILENAME).exists():
            profiles[child.name] = child
    return profiles


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _database_metrics(path: Path) -> dict:
    result = {"exists": path.exists(), "read_error": False, "tables": {}}
    if not path.exists():
        return result
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        result["read_error"] = True
        return result
    try:
        for table in TABLE_ALLOWLIST:
            if not _table_exists(conn, table):
                continue
            row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            result["tables"][table] = int(row[0] or 0) if row else 0
    except sqlite3.Error:
        result["read_error"] = True
    finally:
        conn.close()
    return result


def _attach_database_metrics(config_dir: Path, profiles: dict[str, dict]) -> None:
    for profile_name, profile_dir in _discover_profile_dirs(config_dir).items():
        profile = profiles.setdefault(profile_name, _profile_metrics())
        profile["database"] = _database_metrics(profile_dir / DATABASE_FILENAME)


def _totals(profiles: dict[str, dict], logs: dict) -> dict:
    totals = {
        "runs_started": 0,
        "runs_failed": 0,
        "applications_sent": 0,
        "applications_dry_run_planned": 0,
        "application_ai_errors": 0,
        "reply_candidates": 0,
        "replies_planned": 0,
        "replies_sent": 0,
        "replies_stale": 0,
        "replies_ignored": 0,
        "replies_manual": 0,
        "reply_errors": 0,
        "reply_fallbacks": 0,
        "hot_lead_candidates": 0,
        "hot_leads_detected": 0,
        "hot_leads_notified": 0,
        "hot_lead_notify_failures": 0,
        "hot_lead_ai_errors": 0,
        "technical_event_occurrences": sum(logs.get("events", {}).values()),
    }
    for profile in profiles.values():
        for run in profile["runs"].values():
            totals["runs_started"] += run["started"]
            totals["runs_failed"] += run["failed"]
        totals["applications_sent"] += profile["apply"]["sent"]
        totals["applications_dry_run_planned"] += profile["apply"]["dry_run_planned"]
        totals["application_ai_errors"] += profile["apply"]["ai_errors"]
        totals["reply_candidates"] += profile["reply"]["candidates"]
        totals["replies_planned"] += profile["reply"]["planned"]
        totals["replies_sent"] += profile["reply"]["sent"]
        totals["replies_stale"] += profile["reply"]["stale"]
        totals["replies_ignored"] += profile["reply"]["ignored"]
        totals["replies_manual"] += profile["reply"]["manual"]
        totals["reply_errors"] += profile["reply"]["errors"]
        totals["reply_fallbacks"] += profile["reply"]["fallback"]
        totals["hot_lead_candidates"] += profile["reply"]["hot_candidates"]
        totals["hot_leads_detected"] += profile["reply"]["hot_leads"]
        totals["hot_leads_notified"] += profile["reply"]["hot_notified"]
        totals["hot_lead_notify_failures"] += profile["reply"]["hot_notify_failed"]
        totals["hot_lead_ai_errors"] += profile["reply"]["hot_ai_errors"]
    return totals


def build_report(root: Path, target: date, tz_name: str) -> dict:
    config_dir = Path(os.environ.get("CONFIG_DIR", str(root / "config")))
    if not config_dir.is_absolute():
        config_dir = root / config_dir
    logs, profiles = _scan_logs(root, config_dir, target)
    _attach_database_metrics(config_dir, profiles)
    tz = _timezone(tz_name)
    return {
        "schema_version": 1,
        "date": target.isoformat(),
        "timezone": tz_name,
        "generated_at": datetime.now(tz).isoformat(timespec="seconds"),
        "code_revision": _git_revision(root),
        "profiles": dict(sorted(profiles.items())),
        "logs": logs,
        "totals": _totals(profiles, logs),
        "privacy": {
            "aggregate_only": True,
            "raw_log_lines_included": False,
            "chat_text_included": False,
            "vacancy_titles_included": False,
            "employer_names_included": False,
            "urls_included": False,
            "ids_included": False,
            "secrets_included": False,
        },
    }


def write_report(report: dict, output_dir: Path) -> Path:
    daily_dir = output_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    report_path = daily_dir / f"{report['date']}.json"
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    report_path.write_text(payload, encoding="utf-8")
    (output_dir / "latest.json").write_text(payload, encoding="utf-8")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build privacy-safe HH ops snapshot")
    parser.add_argument("--date", default="yesterday", help="today, yesterday or YYYY-MM-DD")
    parser.add_argument(
        "--timezone",
        default=os.environ.get("OPS_TIMEZONE") or os.environ.get("TZ") or DEFAULT_TIMEZONE,
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository/runtime root (mainly useful for tests and diagnostics)",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    tz = _timezone(args.timezone)
    target = _resolve_date(args.date, tz)
    output_dir = (args.output_dir or root / "ops").resolve()
    report = build_report(root, target, args.timezone)
    path = write_report(report, output_dir)
    try:
        print(path.relative_to(root))
    except ValueError:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
