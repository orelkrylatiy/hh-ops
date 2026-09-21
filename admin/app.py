"""
HH Applicant Tool — Web Admin Panel
FastAPI backend

Запуск:
    pip install fastapi uvicorn
    python -m uvicorn admin.app:app --reload --port 8000
"""
from __future__ import annotations

import base64
import json
import os
import platform
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hh_applicant_tool import constants
from hh_applicant_tool.ai import ChatOpenAI, OpenAIError
from hh_applicant_tool.api import errors as api_errors
from hh_applicant_tool.storage.utils import init_db
from hh_applicant_tool.utils.date import parse_api_datetime

app = FastAPI(title="HH Admin Panel", version="1.0.0")

# Отслеживание запущенных операций
running_operations: dict[str, dict[str, Any]] = {}
active_operations: dict[tuple[str, str], str] = {}
operations_lock = threading.Lock()
operation_history_lock = threading.Lock()

# Устанавливаем UTF-8 для всех JSON ответов
app.default_response_class = JSONResponse


def _admin_credentials() -> tuple[str, str] | None:
    """Return optional Basic Auth credentials configured for the local admin."""
    username = os.getenv("ADMIN_USERNAME", "")
    password = os.getenv("ADMIN_PASSWORD", "")
    if bool(username) != bool(password):
        # A half-configured access boundary must not silently leave the panel open.
        raise RuntimeError(
            "Set both ADMIN_USERNAME and ADMIN_PASSWORD, or leave both unset."
        )
    return (username, password) if username else None


def _is_authorized(request: Request, credentials: tuple[str, str]) -> bool:
    """Validate a Basic Auth header without logging or reflecting its value."""
    authorization = request.headers.get("authorization", "")
    scheme, _, encoded = authorization.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return False
    username, separator, password = decoded.partition(":")
    return bool(separator) and secrets.compare_digest(username, credentials[0]) and secrets.compare_digest(password, credentials[1])


@app.middleware("http")
async def require_admin_auth(request: Request, call_next):
    """Protect UI and API when credentials are configured; keep health probes open."""
    if request.url.path != "/health":
        try:
            credentials = _admin_credentials()
        except RuntimeError as ex:
            return JSONResponse({"detail": str(ex)}, status_code=500)
        if credentials and not _is_authorized(request, credentials):
            return JSONResponse(
                {"detail": "Admin authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="HH Admin"'},
            )
    return await call_next(request)

# Middleware для явного указания UTF-8 кодировки
@app.middleware("http")
async def add_utf8_header(request, call_next):
    response = await call_next(request)
    if "content-type" in response.headers:
        response.headers["content-type"] = response.headers["content-type"].replace("charset=", "").split(";")[0] + "; charset=utf-8"
    return response

_cors_origins = [
    origin.strip()
    for origin in os.getenv("ADMIN_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )

# ---------------------------------------------------------------------------
# Helpers: paths
# ---------------------------------------------------------------------------

# Корень проекта (на уровень выше папки admin/)
PROJECT_ROOT = Path(__file__).parent.parent
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _build_local_cli_cmd(args: list[str]) -> list[str]:
    """Run CLI from local src tree to avoid env drift."""
    return [
        sys.executable,
        "-c",
        (
            "import sys; "
            "sys.path.insert(0, 'src'); "
            "from hh_applicant_tool.main import main; "
            "raise SystemExit(main(sys.argv[1:]))"
        ),
        *args,
    ]


def _config_root() -> Path:
    # Поддержка CONFIG_DIR (Docker-режим: /app/config или ./config)
    env_config_dir = os.getenv("CONFIG_DIR")
    if env_config_dir:
        return Path(env_config_dir)

    # Проверяем config/ в корне проекта (Docker legacy)
    # Считаем папку "с данными" только если в ней есть config.json
    # или подпапки с config.json (профили) — игнорируем .gitkeep и yaml-файлы
    local_config = PROJECT_ROOT / "config"
    if local_config.exists() and (
        (local_config / "config.json").exists()
        or any(
            d.is_dir() and (d / "config.json").exists()
            for d in local_config.iterdir()
        )
    ):
        return local_config

    # Стандартный путь ОС
    match platform.system():
        case "Windows":
            base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        case "Darwin":
            base = Path.home() / "Library" / "Application Support"
        case _:
            base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "hh-applicant-tool"


def _profile_dir(profile: str = "default") -> Path:
    profile = _validate_profile_name(profile)
    root = _config_root()
    # Keep compatibility with a legacy CONFIG_DIR that points directly to one
    # profile, but never collapse named accounts into the same directory.
    if profile == constants.ADMIN_DEFAULT_PROFILE and (root / "config.json").exists():
        return root
    return root / profile


def _validate_profile_name(profile: str) -> str:
    normalized = (profile or "").strip()
    if not normalized:
        raise HTTPException(400, "Profile name is required")
    if normalized in {".", ".."} or not PROFILE_NAME_RE.fullmatch(normalized):
        raise HTTPException(
            400,
            "Invalid profile name. Use letters, numbers, dot, dash or underscore.",
        )
    return normalized


def _ensure_profile_storage(profile: str) -> dict[str, Any]:
    profile = _validate_profile_name(profile)
    profile_dir = _profile_dir(profile)
    created = not profile_dir.exists()
    profile_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        with open(cfg_path, "w", encoding="utf-8") as fp:
            json.dump({}, fp, indent=2, ensure_ascii=False)

    db_path = _db_path(profile)
    if not db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            init_db(conn)
            conn.commit()
        finally:
            conn.close()

    return {
        "profile": profile,
        "created": created,
        "config_path": str(cfg_path),
        "db_path": str(db_path),
    }


def _db_path(profile: str = "default") -> Path:
    return _profile_dir(profile) / "data"


def _config_path(profile: str = "default") -> Path:
    return _profile_dir(profile) / "config.json"


def _cookies_path(profile: str = "default") -> Path:
    return _profile_dir(profile) / "cookies.txt"


def _log_path(profile: str = "default") -> Path:
    return _profile_dir(profile) / "log.txt"


def get_profiles() -> list[str]:
    root = _config_root()
    if not root.exists():
        return []
    profiles = [
        d.name
        for d in sorted(root.iterdir())
        if d.is_dir() and PROFILE_NAME_RE.fullmatch(d.name)
    ]
    if (root / "config.json").exists():
        return [
            constants.ADMIN_DEFAULT_PROFILE,
            *[p for p in profiles if p != constants.ADMIN_DEFAULT_PROFILE],
        ]
    return profiles


def _validate_scope(scope: str) -> str:
    normalized = (scope or "profile").strip().lower()
    if normalized not in {"profile", "all"}:
        raise HTTPException(400, "Scope must be either 'profile' or 'all'.")
    return normalized


def _profiles_for_scope(profile: str, scope: str) -> tuple[str, list[str]]:
    """Resolve an explicit profile or the read-only aggregate account scope."""
    normalized_profile = _validate_profile_name(profile)
    normalized_scope = _validate_scope(scope)
    if normalized_scope == "all":
        return normalized_scope, get_profiles()
    return normalized_scope, [normalized_profile]


def _snapshot_updated_at(profile: str) -> str | None:
    """Local SQLite snapshot freshness, deliberately not an HH source timestamp."""
    db_path = _db_path(profile)
    if not db_path.exists():
        return None
    return datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat()


def _profile_account_summary(profile: str) -> dict[str, Any]:
    """Return local, non-secret account metadata without making an HH API request."""
    profile = _validate_profile_name(profile)
    config_path = _config_path(profile)
    db_path = _db_path(profile)
    return {
        "profile": profile,
        "has_config": config_path.exists(),
        "has_db": db_path.exists(),
        "ready": config_path.exists() and db_path.exists(),
        "token": _get_token_info(profile) if config_path.exists() else {"status": "no_config", "profile": profile},
        "snapshot_updated_at": _snapshot_updated_at(profile),
    }


def _iter_profile_connections(
    profile: str,
    scope: str,
) -> tuple[str, list[tuple[str, sqlite3.Connection]], list[dict[str, str]]]:
    """Open only existing profile snapshots and make omissions visible to callers."""
    normalized_scope, profiles = _profiles_for_scope(profile, scope)
    connections: list[tuple[str, sqlite3.Connection]] = []
    unavailable: list[dict[str, str]] = []
    for account_profile in profiles:
        if not _db_path(account_profile).exists():
            unavailable.append({"profile": account_profile, "reason": "database_missing"})
            continue
        try:
            connections.append((account_profile, get_conn(account_profile)))
        except HTTPException as ex:
            unavailable.append({"profile": account_profile, "reason": str(ex.detail)})
    if normalized_scope == "profile" and not connections:
        raise HTTPException(404, f"Database not found for profile: {profile}")
    return normalized_scope, connections, unavailable


def get_conn(profile: str = "default") -> sqlite3.Connection:
    db = _db_path(profile)
    if not db.exists():
        raise HTTPException(404, f"База данных не найдена: {db}")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    # Existing profile snapshots are upgraded before dashboard queries use the
    # newly persisted vacancy→employer relation.
    init_db(conn)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Helpers: queries
# ---------------------------------------------------------------------------

def q(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def q1(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


def _build_optional_filter(
    column: str,
    value: str | None,
    *,
    like: bool = False,
) -> tuple[str, list[str]]:
    if not value:
        return "", []
    if like:
        return f"WHERE {column} LIKE ?", [f"%{value}%"]
    return f"WHERE {column} = ?", [value]


def _days_since_update(updated_at: str | None) -> int | None:
    if not updated_at:
        return None
    try:
        updated_dt = parse_api_datetime(updated_at)
    except ValueError:
        return None
    return (datetime.now(updated_dt.tzinfo) - updated_dt).days


def _get_last_message_author(profile: str, negotiation_id: int | None) -> str | None:
    if not negotiation_id:
        return None
    data = _hh_get(profile, f"/negotiations/{negotiation_id}/messages", {"per_page": 20})
    items = data.get("items", [])
    if not items:
        return None
    participant_type = ((items[-1].get("author") or {}).get("participant_type"))
    if participant_type == "employer":
        return "employer"
    if participant_type:
        return "candidate"
    return None


def _recommend_negotiation_action(
    negotiation: dict[str, Any],
    *,
    days_for_followup: int = 5,
    recent_grace_days: int = 3,
    last_message_author: str | None = None,
) -> tuple[str, str]:
    state_id = (negotiation.get("state") or {}).get("id", "")
    if state_id == "discard":
        return "skip_rejection", "Negotiation already rejected"

    days_since_update = _days_since_update(negotiation.get("updated_at"))
    viewed_by_opponent = negotiation.get("viewed_by_opponent")
    has_updates = negotiation.get("has_updates", False)

    if last_message_author == "employer" or has_updates:
        return "reply_employer_waiting", "Employer is waiting for a response"

    if last_message_author == "candidate":
        return "skip_already_replied", "Last message was already sent by the candidate"

    if (
        viewed_by_opponent is False
        and days_since_update is not None
        and days_since_update < recent_grace_days
    ):
        return "wait_recent_application", "Application is still fresh and not viewed yet"

    if days_since_update is not None and days_since_update >= days_for_followup:
        return "followup_candidate_silent", "No visible progress for several days"

    return "wait_recent_activity", "No follow-up needed yet"


def _build_negotiation_review_item(
    profile: str,
    negotiation: dict[str, Any],
    *,
    include_last_message_author: bool = False,
    days_for_followup: int = 5,
    recent_grace_days: int = 3,
) -> dict[str, Any]:
    vacancy = negotiation.get("vacancy") or {}
    employer = vacancy.get("employer") or {}
    negotiation_id = negotiation.get("id")
    last_message_author = (
        _get_last_message_author(profile, negotiation_id)
        if include_last_message_author
        else None
    )
    recommended_action, recommendation_reason = _recommend_negotiation_action(
        negotiation,
        days_for_followup=days_for_followup,
        recent_grace_days=recent_grace_days,
        last_message_author=last_message_author,
    )
    return {
        "id": negotiation_id,
        "state": (negotiation.get("state") or {}).get("id", ""),
        "vacancy_name": vacancy.get("name"),
        "employer_name": employer.get("name"),
        "updated_at": negotiation.get("updated_at"),
        "viewed_by_opponent": negotiation.get("viewed_by_opponent"),
        "has_updates": negotiation.get("has_updates", False),
        "days_since_update": _days_since_update(negotiation.get("updated_at")),
        "last_message_author": last_message_author,
        "recommended_action": recommended_action,
        "recommendation_reason": recommendation_reason,
    }


# ---------------------------------------------------------------------------
# Routes: system
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    return {"ok": True}


@app.get("/api/status")
def get_status(
    profile: str = Query(constants.ADMIN_DEFAULT_PROFILE),
    scope: str = Query("profile"),
):
    """Return readiness for the selected profile or an explicit aggregate scope."""
    normalized_scope, profiles = _profiles_for_scope(profile, scope)
    accounts = [_profile_account_summary(item) for item in profiles]
    selected = (
        next((item for item in accounts if item["profile"] == profile), None)
        if normalized_scope == "profile"
        else None
    )
    if normalized_scope == "profile" and selected is None:
        selected = {
            "profile": _validate_profile_name(profile),
            "has_config": False,
            "has_db": False,
            "ready": False,
            "token": {"status": "no_config", "profile": profile},
            "snapshot_updated_at": None,
        }
    ready_accounts = [item for item in accounts if item["ready"]]
    return {
        "config_root": str(_config_root()),
        "scope": normalized_scope,
        "profiles": profiles,
        "accounts": accounts,
        "ready": selected["ready"] if selected else bool(ready_accounts),
        "has_config": selected["has_config"] if selected else bool(ready_accounts),
        "has_db": selected["has_db"] if selected else bool(ready_accounts),
        "selected_profile": selected["profile"] if selected else None,
    }


@app.get("/api/constants")
def get_constants():
    """Возвращает константы API для фронтенда."""
    return {
        "API_PREFIX": constants.ADMIN_API_PREFIX,
        "endpoints": {
            "status": constants.ADMIN_API_STATUS,
            "profiles": constants.ADMIN_API_PROFILES,
            "stats": constants.ADMIN_API_STATS,
            "negotiations": constants.ADMIN_API_NEGOTIATIONS,
            "vacancies": constants.ADMIN_API_VACANCIES,
            "skipped": constants.ADMIN_API_SKIPPED,
            "employers": constants.ADMIN_API_EMPLOYERS,
            "resumes": constants.ADMIN_API_RESUMES,
            "config": constants.ADMIN_API_CONFIG,
            "logs": constants.ADMIN_API_LOGS,
            "user": constants.ADMIN_API_USER,
            "whoami": "/api/whoami",
            "logout": constants.ADMIN_API_AUTH_LOGOUT,
            "reauthorize": constants.ADMIN_API_AUTH_REAUTHORIZE,
            "generate_letter": constants.ADMIN_API_GENERATE_LETTER,
            "run": constants.ADMIN_API_RUN,
            "cancel": constants.ADMIN_API_CANCEL,
            "operations": constants.ADMIN_API_OPERATIONS,
            "operation_status": constants.ADMIN_API_OPERATION_STATUS,
        },
        "operations": {
            "update_resumes": constants.ADMIN_OP_UPDATE_RESUMES,
            "apply_vacancies": constants.ADMIN_OP_APPLY_VACANCIES,
        },
        "defaults": {
            "profile": constants.ADMIN_DEFAULT_PROFILE,
            "response_delay": f"{constants.RESPONSE_DELAY_MIN}-{constants.RESPONSE_DELAY_MAX}",
        }
    }


@app.get("/", response_class=HTMLResponse)
def index():
    html = Path(__file__).parent / "index.html"
    if html.exists():
        return FileResponse(str(html))
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


@app.get("/api/profiles")
def list_profiles():
    profiles = get_profiles()
    return {
        "profiles": profiles,
        "accounts": [_profile_account_summary(profile) for profile in profiles],
    }


@app.get("/api/accounts")
def list_accounts():
    """Account overview for the visible account switcher and aggregate dashboard."""
    profiles = get_profiles()
    return {"accounts": [_profile_account_summary(profile) for profile in profiles]}


class ProfileCreateRequest(BaseModel):
    profile: str


@app.post("/api/profiles")
def create_profile(body: ProfileCreateRequest):
    return {"ok": True, **_ensure_profile_storage(body.profile)}


@app.get("/api/whoami")
def get_whoami(profile: str = Query("default")):
    """Return raw output of `hh-applicant-tool whoami -v` for selected profile."""
    profile = _validate_profile_name(profile)
    # Используем --no-auto-auth чтобы не запускалась инициализация браузера
    cmd = _build_local_cli_cmd(["-v", "--profile-id", profile, "whoami"])
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["CONFIG_DIR"] = str(_config_root())
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,  # Увеличен таймаут с 20с до 60с
            env=env,
            check=False,
        )
        stdout = proc.stdout or ""
        # Expected line format:
        # 🆔 <id> <ФИО> [ ...counters... ]
        full_name = None
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "🆔" in line and "[" in line:
                prefix = line.split("[", 1)[0].strip()
                parts = prefix.split()
                if len(parts) >= 3:
                    full_name = " ".join(parts[2:]).strip() or None
                break

        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": proc.stderr or "",
            "command": "hh-applicant-tool whoami -v",
            "profile": profile,
            "full_name": full_name,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Команда whoami превысила лимит времени (60с)")
    except Exception as ex:
        raise HTTPException(500, f"Ошибка запуска whoami: {ex}") from ex


# ---------------------------------------------------------------------------
# Routes: statistics
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def get_stats(
    profile: str = Query("default"),
    scope: str = Query("profile"),
):
    """Aggregate dashboard metrics from explicitly scoped local SQLite snapshots.

    ``scope=all`` is intentionally a local aggregate. It never fans out to HH
    while rendering the dashboard, so each metric can state its snapshot
    freshness and stay usable when one account is temporarily unavailable.
    """
    normalized_scope, connections, unavailable = _iter_profile_connections(profile, scope)
    totals: Counter[str] = Counter()
    states: Counter[str] = Counter()
    daily_counts: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    recent_negotiations: list[dict[str, Any]] = []
    resume_views: list[dict[str, Any]] = []
    profile_totals: list[dict[str, Any]] = []
    # Дни считаем по UTC — date('now') в SQLite тоже UTC, иначе крайние дни теряются
    day_keys = [
        (datetime.now(timezone.utc).date() - timedelta(days=offset)).isoformat()
        for offset in range(13, -1, -1)
    ]

    try:
        for account_profile, conn in connections:
            account_totals = {
                "profile": account_profile,
                "negotiations": 0,
                "vacancies": 0,
                "employers": 0,
                "skipped": 0,
                "resumes": 0,
                "snapshot_updated_at": _snapshot_updated_at(account_profile),
            }
            for table, key in (
                ("negotiations", "negotiations"),
                ("vacancies", "vacancies"),
                ("employers", "employers"),
                ("skipped_vacancies", "skipped"),
                ("resumes", "resumes"),
            ):
                count = (q1(conn, f"SELECT count(*) AS c FROM {table}") or {}).get("c", 0)
                account_totals[key] = count
                totals[key] += count

            for row in q(conn, "SELECT state, count(*) AS cnt FROM negotiations GROUP BY state"):
                states[row["state"] or "unknown"] += row["cnt"]
            for row in q(
                conn,
                """
                SELECT date(created_at) AS day, count(*) AS cnt
                FROM negotiations
                WHERE date(created_at) >= date('now', '-13 days')
                GROUP BY date(created_at)
                """,
            ):
                if row["day"] in day_keys:
                    daily_counts[row["day"]] += row["cnt"]
            for row in q(
                conn,
                "SELECT reason, count(*) AS cnt FROM skipped_vacancies GROUP BY reason",
            ):
                skip_reasons[row["reason"] or "unknown"] += row["cnt"]

            for row in q(
                conn,
                """
                SELECT n.id, n.state, n.resume_id, n.created_at, n.updated_at,
                       v.name AS vacancy_name, v.alternate_url AS vacancy_url,
                       e.name AS employer_name
                FROM negotiations n
                LEFT JOIN vacancies v ON v.id = n.vacancy_id
                LEFT JOIN employers e ON e.id = n.employer_id
                ORDER BY n.created_at DESC
                LIMIT 10
                """,
            ):
                recent_negotiations.append({"profile": account_profile, **row})

            for row in q(
                conn,
                """
                SELECT id, title, url, alternate_url, status_id, status_name,
                       can_publish_or_update, total_views, new_views, created_at, updated_at
                FROM resumes
                ORDER BY updated_at DESC
                """,
            ):
                resume_views.append({"profile": account_profile, **row})
            profile_totals.append(account_totals)
    finally:
        for _, conn in connections:
            conn.close()

    recent_negotiations.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    # Свежие резюме сверху, как и в /api/resumes
    resume_views.sort(
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )
    snapshots = [item["snapshot_updated_at"] for item in profile_totals if item["snapshot_updated_at"]]
    return {
        "scope": normalized_scope,
        "profile": _validate_profile_name(profile),
        "profiles": [account_profile for account_profile, _ in connections],
        "unavailable_profiles": unavailable,
        "snapshot_updated_at": max(snapshots) if snapshots else None,
        "totals": {
            "negotiations": totals["negotiations"],
            "vacancies": totals["vacancies"],
            "employers": totals["employers"],
            "skipped": totals["skipped"],
            "resumes": totals["resumes"],
        },
        "profile_totals": profile_totals,
        "negotiations_by_state": [
            {"state": state, "cnt": count}
            for state, count in states.most_common()
        ],
        "daily_applications": [
            {"day": day, "cnt": daily_counts[day]}
            for day in day_keys
        ],
        "skip_reasons": [
            {"reason": reason, "cnt": count}
            for reason, count in skip_reasons.most_common(5)
        ],
        "recent_negotiations": recent_negotiations[:5],
        "resume_views": resume_views,
    }


# ---------------------------------------------------------------------------
# Routes: negotiations
# ---------------------------------------------------------------------------

@app.get("/api/negotiations")
def list_negotiations(
    profile: str = Query("default"),
    scope: str = Query("profile"),
    state: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    normalized_scope, connections, unavailable = _iter_profile_connections(profile, scope)
    items: list[dict[str, Any]] = []
    try:
        for account_profile, conn in connections:
            where, where_params = _build_optional_filter("n.state", state)
            rows = q(conn, """
                SELECT n.id, n.state, n.chat_id, n.resume_id, n.created_at, n.updated_at,
                       v.name as vacancy_name, v.alternate_url as vacancy_url,
                       v.salary_from, v.salary_to, v.currency,
                       e.name as employer_name
                FROM negotiations n
                LEFT JOIN vacancies v ON v.id = n.vacancy_id
                LEFT JOIN employers e ON e.id = n.employer_id
            """ + where + """
                ORDER BY n.created_at DESC
            """, where_params)
            items.extend({"profile": account_profile, **row} for row in rows)
    finally:
        for _, conn in connections:
            conn.close()
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {
        "scope": normalized_scope,
        "items": items[offset : offset + limit],
        "total": len(items),
        "unavailable_profiles": unavailable,
    }


# ---------------------------------------------------------------------------
# Routes: vacancies
# ---------------------------------------------------------------------------

@app.get("/api/vacancies")
def list_vacancies(
    profile: str = Query("default"),
    scope: str = Query("profile"),
    search: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    normalized_scope, connections, unavailable = _iter_profile_connections(profile, scope)
    items: list[dict[str, Any]] = []
    try:
        for account_profile, conn in connections:
            where, where_params = _build_optional_filter("v.name", search, like=True)
            rows = q(conn, """
                SELECT v.*, e.name AS employer_name
                FROM vacancies v
                LEFT JOIN employers e ON e.id = COALESCE(
                    v.employer_id,
                    (SELECT n.employer_id FROM negotiations n WHERE n.vacancy_id = v.id LIMIT 1)
                )
            """ + where + """
                ORDER BY v.created_at DESC
            """, where_params)
            items.extend({"profile": account_profile, **row} for row in rows)
    finally:
        for _, conn in connections:
            conn.close()
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {
        "scope": normalized_scope,
        "items": items[offset : offset + limit],
        "total": len(items),
        "unavailable_profiles": unavailable,
    }


# ---------------------------------------------------------------------------
# Routes: skipped vacancies
# ---------------------------------------------------------------------------

@app.get("/api/skipped")
def list_skipped(
    profile: str = Query("default"),
    scope: str = Query("profile"),
    reason: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    normalized_scope, connections, unavailable = _iter_profile_connections(profile, scope)
    items: list[dict[str, Any]] = []
    try:
        for account_profile, conn in connections:
            where, where_params = _build_optional_filter("reason", reason)
            rows = q(conn, """
                SELECT * FROM skipped_vacancies
            """ + where + """
                ORDER BY created_at DESC
            """, where_params)
            items.extend({"profile": account_profile, **row} for row in rows)
    finally:
        for _, conn in connections:
            conn.close()
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {
        "scope": normalized_scope,
        "items": items[offset : offset + limit],
        "total": len(items),
        "unavailable_profiles": unavailable,
    }


# ---------------------------------------------------------------------------
# Routes: employers
# ---------------------------------------------------------------------------

@app.get("/api/employers")
def list_employers(
    profile: str = Query("default"),
    scope: str = Query("profile"),
    search: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    normalized_scope, connections, unavailable = _iter_profile_connections(profile, scope)
    items: list[dict[str, Any]] = []
    try:
        for account_profile, conn in connections:
            where, where_params = _build_optional_filter("e.name", search, like=True)
            rows = q(conn, """
                SELECT e.*,
                       (SELECT count(*) FROM negotiations n WHERE n.employer_id = e.id) AS applications_count
                FROM employers e
            """ + where + """
                ORDER BY applications_count DESC, e.created_at DESC
            """, where_params)
            items.extend({"profile": account_profile, **row} for row in rows)
    finally:
        for _, conn in connections:
            conn.close()
    items.sort(
        key=lambda item: (item.get("applications_count") or 0, item.get("created_at") or ""),
        reverse=True,
    )
    return {
        "scope": normalized_scope,
        "items": items[offset : offset + limit],
        "total": len(items),
        "unavailable_profiles": unavailable,
    }


# ---------------------------------------------------------------------------
# Routes: resumes
# ---------------------------------------------------------------------------

@app.get("/api/resumes")
def list_resumes(
    profile: str = Query("default"),
    scope: str = Query("profile"),
):
    normalized_scope, connections, unavailable = _iter_profile_connections(profile, scope)
    items: list[dict[str, Any]] = []
    try:
        for account_profile, conn in connections:
            rows = q(
                conn,
                """
                SELECT id, title, url, alternate_url, status_id, status_name,
                       can_publish_or_update, total_views, new_views, created_at, updated_at
                FROM resumes
                ORDER BY updated_at DESC
                """,
            )
            items.extend({"profile": account_profile, **row} for row in rows)
    finally:
        for _, conn in connections:
            conn.close()
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {
        "scope": normalized_scope,
        "items": items,
        "total": len(items),
        "unavailable_profiles": unavailable,
    }


# ---------------------------------------------------------------------------
# Routes: config
# ---------------------------------------------------------------------------

MASKED_KEYS = constants.MASKED_CONFIG_KEYS


def _mask_config(obj: Any, depth: int = 0) -> Any:
    if isinstance(obj, dict):
        return {
            k: "***" if k in MASKED_KEYS else _mask_config(v, depth + 1)
            for k, v in obj.items()
        }
    return obj


@app.get("/api/config")
def get_config(profile: str = Query("default"), show_secrets: bool = Query(False)):
    if show_secrets:
        raise HTTPException(
            403,
            "Secrets are never returned by the admin API. Edit them directly in the profile config.",
        )
    profile = _validate_profile_name(profile)
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        raise HTTPException(404, "config.json не найден")
    try:
        data = _load_and_validate_config(cfg_path)
    except ValidationError as ex:
        raise HTTPException(400, f"Некорректный config.json: {ex}") from ex
    return _mask_config(data)


class ConfigUpdate(BaseModel):
    data: dict


class OpenAIConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


class AdminConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    api_delay: float | int | None = None
    proxy_url: str | None = None
    email_settings: dict[str, Any] | None = None
    openai: OpenAIConfigModel | dict[str, Any] | None = None
    letter_templates: dict[str, str] | None = None   # именованные шаблоны писем


# Встроенные шаблоны — будут добавлены при первом вызове /api/letter-templates/seed
DEFAULT_LETTER_TEMPLATES: dict[str, str] = {
    "universal": (
        "{Здравствуйте|Добрый день}!\n\n"
        "Меня зовут %(first_name)s, и я {хотел бы|хочу} откликнуться на вакансию «%(vacancy_name)s» в компании %(employer_name)s.\n\n"
        "Я внимательно ознакомился с требованиями и уверен, что мой опыт {соответствует|совпадает с} вашим ожиданиям. "
        "{Готов|Буду рад} {рассказать подробнее|обсудить детали} на собеседовании.\n\n"
        "С уважением, %(first_name)s %(last_name)s"
    ),
    "short": (
        "{Здравствуйте|Добрый день}! "
        "Откликаюсь на вакансию «%(vacancy_name)s». "
        "{Уверен, что|Считаю, что} мой опыт будет полезен вашей команде. "
        "Готов к {обсуждению деталей|собеседованию} в {удобное для вас время|любое время}."
    ),
    "motivated": (
        "{Здравствуйте|Добрый день}, команда %(employer_name)s!\n\n"
        "Вакансия «%(vacancy_name)s» сразу привлекла моё внимание — "
        "{она точно совпадает с моим опытом|требования совпадают с моими компетенциями}. "
        "В резюме «%(resume_title)s» подробно описан мой путь, но если коротко: "
        "я {умею решать задачи системно|нацелен на результат} и {быстро вхожу в контекст|легко адаптируюсь}.\n\n"
        "Буду рад {познакомиться с командой|пообщаться} и рассказать о своём опыте подробнее.\n\n"
        "С уважением, %(first_name)s"
    ),
    "remote": (
        "{Здравствуйте|Добрый день}!\n\n"
        "Откликаюсь на вакансию «%(vacancy_name)s». "
        "Я {успешно работаю|работаю} в удалённом формате {уже несколько лет|давно} — "
        "умею {самостоятельно организовывать работу|эффективно работать без офиса}, "
        "{соблюдать дедлайны|не срывать сроки} и {поддерживать связь с командой|быть на связи}.\n\n"
        "Готов приступить {в ближайшее время|сразу после согласования}.\n\n"
        "С уважением, %(first_name)s %(last_name)s"
    ),
    "experienced": (
        "{Здравствуйте|Добрый день}!\n\n"
        "Рассматриваю вакансию «%(vacancy_name)s» в %(employer_name)s как {интересную|привлекательную} возможность. "
        "За {годы практики|время работы} я {накопил|приобрёл} опыт, который {напрямую|точно} "
        "соответствует вашим требованиям.\n\n"
        "Подробности — в резюме «%(resume_title)s». "
        "{Буду рад|С удовольствием} {обсудить детали|познакомиться} при {созвоне|собеседовании}.\n\n"
        "%(first_name)s %(last_name)s"
    ),
}


def _load_and_validate_config(cfg_path: Path) -> dict[str, Any]:
    with open(cfg_path, encoding="utf-8") as f:
        raw = json.load(f)
    validated = AdminConfigModel.model_validate(raw)
    return validated.model_dump(mode="python", exclude_none=True)


@app.put("/api/config")
def update_config(body: ConfigUpdate, profile: str = Query("default")):
    profile = _validate_profile_name(profile)
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        raise HTTPException(404, "config.json не найден")
    # Читаем текущий конфиг и мёрдж только безопасные ключи
    try:
        current = _load_and_validate_config(cfg_path)
    except ValidationError as ex:
        raise HTTPException(400, f"Некорректный config.json: {ex}") from ex

    # Запрещаем перезаписывать токены через API; секции AI мёрджатся по ключам,
    # поэтому api_key из config.json не затирается
    safe_keys = {
        "api_delay",
        "openai",
        "openai_cover_letter",
        "openai_reply",
        "email_settings",
        "proxy_url",
        "letter_templates",
    }
    for k, v in body.data.items():
        if k in safe_keys:
            if isinstance(v, dict) and isinstance(current.get(k), dict):
                current[k].update(v)
            else:
                current[k] = v

    try:
        current = AdminConfigModel.model_validate(current).model_dump(
            mode="python",
            exclude_none=True,
        )
    except ValidationError as ex:
        raise HTTPException(400, f"Некорректные данные конфига: {ex}") from ex

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Routes: user / authorization
# ---------------------------------------------------------------------------

@app.get("/api/user")
def get_user_info(profile: str = Query("default")):
    """Получить информацию о текущем авторизованном пользователе и статусе токена."""
    profile = _validate_profile_name(profile)
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        return {"token_valid": False, "error": "Profile not found"}

    try:
        config = _load_and_validate_config(cfg_path)
    except Exception:
        return {"token_valid": False, "error": "Failed to load config"}

    # Проверяем есть ли токен
    token_info = config.get("token", {})
    access_token = token_info.get("access_token")
    refresh_token = token_info.get("refresh_token")
    expires_at = token_info.get("access_expires_at")

    if not access_token:
        return {"token_valid": False, "error": "Not authorized"}

    # Проверяем истекает ли токен
    expires_in_seconds = None
    if expires_at:
        expires_in_seconds = max(0, expires_at - time.time())

    # Пытаемся получить информацию о пользователе через HH API
    try:
        from hh_applicant_tool.api import client as api_client
        from hh_applicant_tool.constants import DESKTOP_USER_AGENT

        # Создаем API клиент с токеном
        api = api_client.ApiClient(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=expires_at,
            user_agent=DESKTOP_USER_AGENT,
        )

        # Получаем информацию о текущем пользователе
        user_data = api.get("/me")

        return {
            "token_valid": True,
            "email": user_data.get("email"),
            "first_name": user_data.get("first_name", ""),
            "last_name": user_data.get("last_name", ""),
            "expires_in_seconds": expires_in_seconds,
        }
    except api_errors.Forbidden as ex:
        return {
            "token_valid": False,
            "error": str(ex),
            "expires_in_seconds": expires_in_seconds,
        }
    except api_errors.ClientError as ex:
        return {
            "token_valid": False,
            "error": str(ex),
            "expires_in_seconds": expires_in_seconds,
        }
    except Exception as ex:
        # Неизвестная ошибка ≠ валидный токен: показываем профиль как проблемный
        return {
            "token_valid": False,
            "email": None,
            "first_name": None,
            "last_name": None,
            "expires_in_seconds": expires_in_seconds,
            "error": str(ex),
        }


@app.post("/api/auth/logout")
def logout(profile: str = Query("default")):
    """Удалить токены авторизации."""
    profile = _validate_profile_name(profile)
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        raise HTTPException(404, "Profile not found")

    try:
        config = _load_and_validate_config(cfg_path)
    except Exception as ex:
        raise HTTPException(400, f"Failed to load config: {ex}")

    # Удаляем токены
    if "token" in config:
        config["token"] = {}

    # Сохраняем конфиг
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as ex:
        raise HTTPException(500, f"Failed to save config: {ex}")

    cookies_path = _cookies_path(profile)
    cookies_deleted = False
    if cookies_path.exists():
        cookies_path.unlink(missing_ok=True)
        cookies_deleted = True

    return {
        "ok": True,
        "message": "Logged out successfully",
        "cookies_deleted": cookies_deleted,
    }


@app.post("/api/auth/reauthorize")
def reauthorize(
    profile: str = Query("default"),
    manual: bool = Query(True),
    visible: bool = Query(True),
):
    """Запустить операцию авторизации."""
    profile = _validate_profile_name(profile)
    _ensure_profile_storage(profile)

    body = RunRequest(profile=profile)
    extra = []
    if manual:
        extra.append("--manual")
    if visible:
        extra.append("--no-headless")

    result = _run_operation("authorize", body, extra=extra)
    result["profile"] = profile
    return result


# ---------------------------------------------------------------------------
# Routes: logs
# ---------------------------------------------------------------------------

@app.get("/api/logs")
def get_logs(profile: str = Query("default"), lines: int = Query(200)):
    log_path = _log_path(profile)
    if not log_path.exists():
        return {"lines": []}
    with open(log_path, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return {"lines": all_lines[-lines:]}


# ---------------------------------------------------------------------------
# Routes: generate cover letter
# ---------------------------------------------------------------------------

# --- Единый AI-слой ----------------------------------------------------------
# Админка и CLI используют один и тот же клиент (ChatOpenAI) и одни и те же
# секции config.json. Это убирает прежний рассинхрон, когда CLI читал
# `openai_cover_letter`, а админка — `openai`.

def _normalize_chat_url(base_url: str | None) -> str:
    """Приводит base_url к полному endpoint'у /chat/completions.

    Терпит оба формата: короткий ('.../v1') и полный ('.../v1/chat/completions').
    """
    url = (base_url or constants.OPENAI_DEFAULT_BASE_URL).rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    return url


def _resolve_ai_section(profile: str, sections: list[str]) -> dict[str, Any]:
    """Находит первую настроенную AI-секцию из списка приоритетов.

    `sections` — порядок поиска, напр. ['openai_reply', 'openai_cover_letter', 'openai'].
    Последний 'openai' оставлен для обратной совместимости со старым конфигом.
    """
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        raise HTTPException(404, "config.json не найден — нет доступа к AI")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    for name in sections:
        section = cfg.get(name) or {}
        if section.get("api_key"):
            return section
    raise HTTPException(
        400,
        f"AI не настроен: добавьте секцию '{sections[0]}' с 'api_key' и 'base_url' в config.json. "
        "См. docs/LLM_SETUP.md",
    )


def _make_ai_client(
    profile: str,
    sections: list[str],
    system_prompt: str,
    *,
    temperature: float = 0.7,
    max_tokens: int = 600,
) -> ChatOpenAI:
    """Собирает ChatOpenAI из config.json — с retry, rate-limit и таймаутами."""
    c = _resolve_ai_section(profile, sections)
    return ChatOpenAI(
        api_key=c["api_key"],
        base_url=_normalize_chat_url(c.get("base_url")),
        model=c.get("model") or constants.OPENAI_DEFAULT_MODEL,
        system_prompt=system_prompt,
        temperature=c.get("temperature", temperature),
        max_completion_tokens=c.get("max_completion_tokens", max_tokens),
        rate_limit=c.get("rate_limit", 40),
    )


class LetterRequest(BaseModel):
    vacancy_name: str
    vacancy_description: str = ""
    employer_name: str = ""
    resume_title: str = ""
    extra: str = ""
    profile: str = "default"


@app.post("/api/generate-letter")
def generate_letter(body: LetterRequest):
    system_prompt = (
        "Ты опытный HR-специалист и помогаешь писать сопроводительные письма для откликов на вакансии. "
        "Пиши живо, искренне, без шаблонных фраз. Письмо должно быть персонализировано под вакансию. "
        "Объём: 3-4 абзаца, не более 250 слов. Язык: русский."
    )
    user_prompt = (
        f"Напиши сопроводительное письмо для отклика на вакансию.\n\n"
        f"Вакансия: {body.vacancy_name}\n"
        f"Работодатель: {body.employer_name or 'не указан'}\n"
        f"Моё резюме / должность: {body.resume_title or 'не указана'}\n"
    )
    if body.vacancy_description:
        user_prompt += f"\nОписание вакансии:\n{body.vacancy_description[:1500]}\n"
    if body.extra:
        user_prompt += f"\nДополнительные пожелания: {body.extra}\n"

    client = _make_ai_client(
        body.profile,
        ["openai_cover_letter", "openai"],
        system_prompt,
        temperature=0.8,
        max_tokens=600,
    )
    try:
        letter = client.complete(user_prompt)
    except OpenAIError as ex:
        raise HTTPException(502, f"Ошибка AI: {ex}") from ex
    return {"letter": letter}


# ---------------------------------------------------------------------------
# Routes: run operations
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    profile: str = "default"
    dry_run: bool = False
    confirm_live: bool = False
    extra_args: list[str] = Field(default_factory=list)
    response_delay: str = f"{constants.RESPONSE_DELAY_MIN}-{constants.RESPONSE_DELAY_MAX}"


@app.post("/api/run/update-resumes")
def run_update_resumes(body: RunRequest):
    return _run_operation("update-resumes", body)


@app.post("/api/run/apply-vacancies")
def run_apply_vacancies(body: RunRequest):
    args = []
    if body.dry_run:
        args.append("--dry-run")
    if body.response_delay and body.response_delay != f"{constants.RESPONSE_DELAY_MIN}-{constants.RESPONSE_DELAY_MAX}":
        args.extend(["--response-delay", body.response_delay])
    return _run_operation("apply-safe", body, extra=args)


def _requires_live_confirmation(op: str, cli_args: list[str]) -> bool:
    if op == "update-resumes":
        return True
    return op in {
        "apply-vacancies",
        "apply-safe",
        "apply-profile",
        "reply-employers",
    } and "--dry-run" not in cli_args


def _operation_key(profile: str, op: str) -> tuple[str, str]:
    # All application entrypoints mutate the same HH/profile state and must
    # serialize against each other inside the admin process.
    operation_group = (
        "apply"
        if op in {"apply-vacancies", "apply-safe", "apply-profile"}
        else op
    )
    return profile, operation_group


def _operation_history_path(profile: str) -> Path:
    return _profile_dir(profile) / "admin_operation_history.jsonl"


def _operation_history_item(record: dict[str, Any]) -> dict[str, Any]:
    """Persist auditable metadata only; verbose subprocess output stays ephemeral."""
    keys = (
        "op_id",
        "operation",
        "profile",
        "dry_run",
        "started_at",
        "finished_at",
        "returncode",
        "cancelled",
        "error",
    )
    return {key: record[key] for key in keys if key in record}


def _append_operation_history(record: dict[str, Any]) -> None:
    try:
        path = _operation_history_path(record["profile"])
        if not path.parent.exists():
            return
        with operation_history_lock, open(path, "a", encoding="utf-8") as history:
            history.write(json.dumps(_operation_history_item(record), ensure_ascii=False) + "\n")
    except OSError:
        # An operation must never be reported as failed only because its optional
        # local audit record could not be written.
        pass


def _finalize_operation(
    op_id: str,
    operation_key: tuple[str, str],
    outcome: dict[str, Any],
) -> None:
    """Finish an operation once and persist one concise audit record.

    The worker thread and a cancellation request can reach completion at nearly
    the same time.  Reserving finalization under the operation lock keeps the
    visible state and the JSONL history consistent without holding the lock for
    file I/O.
    """
    history_record: dict[str, Any] | None = None
    with operations_lock:
        record = running_operations.get(op_id)
        if not record or record.get("_finalized"):
            return
        record.update(
            {
                "completed": True,
                "running": False,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "process": None,
            }
        )
        record.update(outcome)
        record["_finalized"] = True
        active_operations.pop(operation_key, None)
        history_record = dict(record)
    _append_operation_history(history_record)


def _terminate_operation_process(process: subprocess.Popen) -> None:
    """Terminate a child process and any commands it spawned."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (AttributeError, OSError):
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, OSError):
            process.kill()
        process.wait()


def _run_operation(op: str, body: RunRequest, extra: list[str] | None = None) -> dict:
    import uuid

    profile = _validate_profile_name(body.profile)
    extra = extra or []
    all_args = [*extra, *body.extra_args]
    operation_key = _operation_key(profile, op)
    flock_path: str | None = None
    profile_lock: Path | None = None
    bash_path: str | None = None
    if op == "apply-profile":
        bash_path = shutil.which("bash")
        if not bash_path:
            raise HTTPException(
                503,
                "bash is required for profile-lane application runs.",
            )
    if operation_key[1] == "apply" and os.name != "nt":
        flock_path = shutil.which("flock")
        if not flock_path:
            raise HTTPException(
                503,
                "flock is required for cross-process application locking.",
            )
        lock_dir = Path(
            os.getenv("HH_PROFILES_LOCK_DIR", "/tmp/hh-profile-locks")
        )
        lock_dir.mkdir(parents=True, exist_ok=True)
        profile_lock = lock_dir / f"{profile}.lock"

    if _requires_live_confirmation(op, all_args) and not body.confirm_live:
        raise HTTPException(
            409,
            "Live operation requires an explicit confirm_live=true acknowledgement.",
        )

    # Генерируем уникальный ID для операции
    op_id = str(uuid.uuid4())[:8]
    with operations_lock:
        existing_id = active_operations.get(operation_key)
        if existing_id:
            raise HTTPException(
                409,
                f"Operation {op} is already running for profile {profile} ({existing_id}).",
            )
        active_operations[operation_key] = op_id
        running_operations[op_id] = {
            "op_id": op_id,
            "operation": op,
            "profile": profile,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "running": True,
            "process": None,
            "dry_run": "--dry-run" in all_args,
        }

    if op == "apply-profile":
        assert bash_path is not None
        cmd = [
            bash_path,
            str(PROJECT_ROOT / "scripts" / "apply-profile.sh"),
            "--profile",
            profile,
            *all_args,
        ]
    else:
        cli_args = ["--profile-id", profile]
        if op != "authorize":
            cli_args.append("--no-auto-auth")
        cmd = _build_local_cli_cmd(cli_args + [op] + all_args)

    if flock_path and profile_lock:
        cmd = [
            flock_path,
            "-n",
            "-E",
            "75",
            str(profile_lock),
            *cmd,
        ]

    # Функция для выполнения в потоке
    def execute_operation():
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env["CONFIG_DIR"] = str(_config_root())
            if op == "apply-profile" and flock_path and profile_lock:
                env["HH_PROFILE_LOCK_HELD"] = "1"
            print(f"DEBUG: Starting operation {op_id}: {op} for profile {profile}")

            # Используем Popen чтобы можно было отменить процесс.
            # stdin=DEVNULL гарантирует что процесс не зависнет ожидая ввода —
            # любая попытка читать stdin сразу получит EOF.
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(PROJECT_ROOT),
                env=env,
                start_new_session=True,
            )

            # Сохраняем процесс для возможности отмены
            with operations_lock:
                record = running_operations.get(op_id)
                cancellation_requested = not record or record.get("cancel_requested")
                if record:
                    record["process"] = process
            if cancellation_requested:
                _terminate_operation_process(process)
                stdout, stderr = process.communicate()
                _finalize_operation(
                    op_id,
                    operation_key,
                    {
                        "cancelled": True,
                        "returncode": process.returncode if process.returncode is not None else -signal.SIGTERM,
                        "stdout": stdout[-constants.ADMIN_LOG_OUTPUT_LIMIT:] if stdout else "",
                        "stderr": "Cancelled before operation started",
                    },
                )
                return
            print(f"DEBUG: Process {op_id} started with PID {process.pid}")

            try:
                # Ждём завершения с таймаутом
                stdout, stderr = process.communicate(timeout=constants.ADMIN_OPERATION_TIMEOUT)
                returncode = process.returncode
                print(f"DEBUG: Process {op_id} completed with code {returncode}")
            except subprocess.TimeoutExpired:
                print(f"DEBUG: Process {op_id} timeout - terminating")
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=3)
                    returncode = process.returncode
                except subprocess.TimeoutExpired:
                    print(f"DEBUG: Force killing process {op_id}")
                    process.kill()
                    stdout, stderr = process.communicate()
                    returncode = process.returncode

            _finalize_operation(
                op_id,
                operation_key,
                {
                    "returncode": int(returncode) if returncode is not None else 0,
                    "stdout": stdout[-constants.ADMIN_LOG_OUTPUT_LIMIT:] if stdout else "",
                    "stderr": stderr[-constants.ADMIN_LOG_ERROR_LIMIT:] if stderr else "",
                },
            )

        except Exception as e:
            print(f"DEBUG: Exception in operation {op_id}: {type(e).__name__}: {e}")
            import traceback
            print(traceback.format_exc())
            _finalize_operation(
                op_id,
                operation_key,
                {
                    "error": str(e),
                    "returncode": 1,
                    "stdout": "",
                    "stderr": str(e),
                },
            )
        finally:
            for index, value in enumerate(all_args[:-1]):
                if value != "--letter-file":
                    continue
                candidate = Path(all_args[index + 1])
                temp_dir = _profile_dir(profile) / ".admin-tmp"
                try:
                    if candidate.parent == temp_dir and candidate.exists():
                        candidate.unlink()
                except OSError:
                    pass  # cleanup failure must not hide operation result

    # Запускаем операцию в отдельном потоке
    thread = threading.Thread(target=execute_operation, daemon=True)
    thread.start()

    # Сразу возвращаем ID операции
    return {
        "op_id": op_id,
        "profile": profile,
        "operation": op,
        "dry_run": "--dry-run" in all_args,
        "stdout": "Operation started in background...",
        "stderr": "",
    }


# ---------------------------------------------------------------------------
# Routes: cancel operations
# ---------------------------------------------------------------------------

@app.post("/api/cancel/{op_id}")
def cancel_operation(op_id: str, profile: str | None = Query(None)):
    """Cancel a single profile-scoped operation, including its process group."""
    with operations_lock:
        record = running_operations.get(op_id)
        if not record or record.get("completed"):
            raise HTTPException(404, f"Operation {op_id} was not found or has already completed")
        if profile is not None and record["profile"] != _validate_profile_name(profile):
            raise HTTPException(404, f"Operation {op_id} was not found for this profile")
        process = record.get("process")
        record["cancel_requested"] = True

    if process is None:
        # The background thread has reserved the lock but not spawned the child
        # yet. It will observe cancel_requested and terminate immediately.
        return {"ok": True, "message": f"Operation {op_id} cancellation requested"}

    try:
        print(f"DEBUG: Terminating process {op_id}")
        _terminate_operation_process(process)
        _finalize_operation(
            op_id,
            _operation_key(record["profile"], record["operation"]),
            {
                "cancelled": True,
                "returncode": process.returncode if process.returncode is not None else -signal.SIGTERM,
                "stderr": "Cancelled by admin user",
                "stdout": record.get("stdout", ""),
            },
        )
        return {"ok": True, "message": f"Operation {op_id} cancelled"}
    except Exception as e:
        raise HTTPException(500, f"Could not cancel operation: {e}") from e


@app.get("/api/operations")
def list_operations(
    profile: str = Query("default"),
    scope: str = Query("profile"),
):
    """List in-memory operations without mixing profiles by accident."""
    normalized_scope, profiles = _profiles_for_scope(profile, scope)
    allowed_profiles = set(profiles)
    with operations_lock:
        ops = [
            {
                key: value
                for key, value in record.items()
                if key not in {"process", "stdout", "stderr"} and not key.startswith("_")
            }
            | {
                "pid": record["process"].pid if record.get("process") else None,
            }
            for record in running_operations.values()
            if record["profile"] in allowed_profiles
        ]
    ops.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    return {"scope": normalized_scope, "operations": ops}


@app.get("/api/operation-status/{op_id}")
def get_operation_status(op_id: str, profile: str | None = Query(None)):
    """Get the status only when it belongs to the requested account."""
    with operations_lock:
        item = running_operations.get(op_id)

    if not item:
        raise HTTPException(404, f"Operation {op_id} was not found")
    if profile is not None and item["profile"] != _validate_profile_name(profile):
        raise HTTPException(404, f"Operation {op_id} was not found for this profile")
    result = {
        "op_id": op_id,
        "profile": item["profile"],
        "operation": item["operation"],
        "dry_run": item.get("dry_run", False),
        "running": item.get("running", False),
        "returncode": item.get("returncode"),
        "stdout": item.get("stdout", ""),
        "stderr": item.get("stderr", ""),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "cancelled": item.get("cancelled", False),
    }
    if item.get("process"):
        result["pid"] = item["process"].pid
    return result


@app.get("/api/operations/history")
def list_operation_history(
    profile: str = Query("default"),
    scope: str = Query("profile"),
    limit: int = Query(30, ge=1, le=200),
):
    """Read durable per-account run metadata after a server restart."""
    normalized_scope, profiles = _profiles_for_scope(profile, scope)
    entries: list[dict[str, Any]] = []
    for account_profile in profiles:
        path = _operation_history_path(account_profile)
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as history:
                for line in history:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("profile") == account_profile:
                        entries.append(item)
        except OSError:
            continue
    entries.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    return {"scope": normalized_scope, "operations": entries[:limit]}


# ---------------------------------------------------------------------------
# HH API proxy helper
# ---------------------------------------------------------------------------

import requests as _requests


def _hh_request(profile: str, method: str, path: str, **kwargs) -> Any:
    """Прямой запрос к HH API через сохранённый access_token."""
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        raise HTTPException(404, "config.json не найден")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)

    token = (cfg.get("token") or {}).get("access_token")
    if not token:
        raise HTTPException(
            401,
            "Нет access_token. Авторизуйтесь: python -m hh_applicant_tool auth"
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": cfg.get("user_agent", constants.DESKTOP_USER_AGENT),
        "X-HH-App-Active": "true",
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    resp = _requests.request(
        method,
        f"https://api.hh.ru{path}",
        headers=headers,
        timeout=15,
        **kwargs,
    )
    if resp.status_code == 401:
        raise HTTPException(
            401,
            "Токен устарел. Обновите: python -m hh_applicant_tool refresh-token"
        )
    if not resp.ok:
        raise HTTPException(resp.status_code, f"HH API: {resp.text[:300]}")
    if resp.status_code == 204:
        return {}
    return resp.json()


def _hh_get(profile: str, path: str, params: dict | None = None) -> Any:
    return _hh_request(profile, "GET", path, params=params or {})


def _hh_post(profile: str, path: str, data: dict | None = None) -> Any:
    return _hh_request(profile, "POST", path, json=data or {})


def _hh_delete(profile: str, path: str) -> Any:
    return _hh_request(profile, "DELETE", path)


# ---------------------------------------------------------------------------
# Routes: inbox (сообщения от работодателей)
# ---------------------------------------------------------------------------

@app.get("/api/inbox")
def get_inbox(
    profile: str = Query("default"),
    status: str = Query(""),      # active, archived, discard, invitation, etc.
    page: int = Query(0),
    per_page: int = Query(20),
):
    """Список переписок с работодателями напрямую из HH API."""
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if status:
        params["status"] = status

    data = _hh_get(profile, "/negotiations", params)
    # Нормализуем поля для удобства фронтенда
    items = []
    for n in data.get("items", []):
        vacancy = n.get("vacancy") or {}
        employer = vacancy.get("employer") or {}
        last_msg = (n.get("messages_url") or "")
        items.append({
            "id": n.get("id"),
            "state": (n.get("state") or {}).get("id", ""),
            "state_name": (n.get("state") or {}).get("name", ""),
            "created_at": n.get("created_at"),
            "updated_at": n.get("updated_at"),
            "vacancy_id": vacancy.get("id"),
            "vacancy_name": vacancy.get("name"),
            "vacancy_url": vacancy.get("alternate_url"),
            "employer_id": employer.get("id"),
            "employer_name": employer.get("name"),
            "employer_logo": (employer.get("logo_urls") or {}).get("90"),
            "viewed_by_opponent": n.get("viewed_by_opponent"),
            "has_updates": n.get("has_updates", False),
            "messages_url": last_msg,
        })
    return {
        "items": items,
        "found": data.get("found", 0),
        "page": data.get("page", 0),
        "pages": data.get("pages", 0),
        "per_page": data.get("per_page", per_page),
    }


@app.get("/api/inbox/{neg_id}/messages")
def get_messages(neg_id: int, profile: str = Query("default")):
    """Получить историю сообщений переписки."""
    data = _hh_get(profile, f"/negotiations/{neg_id}/messages", {"per_page": 50})
    messages = []
    for m in data.get("items", []):
        author = m.get("author") or {}
        messages.append({
            "id": m.get("id"),
            "text": m.get("text", ""),
            "created_at": m.get("created_at"),
            "is_employer": author.get("participant_type") == "employer",
            "author_name": author.get("name", ""),
            "viewed": m.get("viewed_by_opponent", False),
        })
    return {"messages": messages, "found": data.get("found", 0)}


@app.get("/api/agent/review-negotiations")
def review_negotiations(
    profile: str = Query("default"),
    status: str = Query(""),
    page: int = Query(0),
    per_page: int = Query(20),
    include_last_message_author: bool = Query(True),
    days_for_followup: int = Query(5),
    recent_grace_days: int = Query(3),
):
    """Agent-friendly review endpoint for inbox decisioning without sending messages."""
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if status:
        params["status"] = status

    data = _hh_get(profile, "/negotiations", params)
    items = [
        _build_negotiation_review_item(
            profile,
            negotiation,
            include_last_message_author=include_last_message_author,
            days_for_followup=days_for_followup,
            recent_grace_days=recent_grace_days,
        )
        for negotiation in data.get("items", [])
    ]
    return {
        "items": items,
        "found": data.get("found", 0),
        "page": data.get("page", 0),
        "pages": data.get("pages", 0),
        "per_page": data.get("per_page", per_page),
    }


class ReplyRequest(BaseModel):
    message: str = Field("", max_length=4_000)
    use_ai: bool = False
    profile: str = "default"
    vacancy_name: str = Field("", max_length=500)
    employer_name: str = Field("", max_length=500)
    send: bool = False
    confirm_live: bool = False
    # Если передана история — AI учтёт контекст
    # Если не передана — endpoint сам загрузит её из HH API
    history: list[dict] | None = None
    fetch_history: bool = True   # автоматически загрузить историю если не передана


@app.post("/api/inbox/{neg_id}/reply")
def send_reply(neg_id: int, body: ReplyRequest):
    """
    Create a reply draft by default. A separate explicit confirmation is needed
    for the irreversible HH message POST.
    """
    profile = _validate_profile_name(body.profile)
    text = body.message.strip()

    if body.use_ai and not text:
        # Загружаем историю переписки для контекста
        history = body.history
        if history is None and body.fetch_history:
            try:
                msgs_data = _hh_get(profile, f"/negotiations/{neg_id}/messages", {"per_page": 20})
                history = msgs_data.get("items", [])
            except Exception:
                history = []

        # Форматируем историю для AI
        history_text = ""
        last_own_message = ""
        last_employer_message = ""
        if history:
            lines = []
            for m in history[-20:]:
                author = m.get("author") or {}
                who = "Работодатель" if author.get("participant_type") == "employer" else "Я"
                msg_text = (m.get("text") or "").strip()
                if msg_text:
                    lines.append(f"{who}: {msg_text}")
                    if who == "Я":
                        last_own_message = msg_text
                    else:
                        last_employer_message = msg_text
            history_text = "\n".join(lines)

        system = (
            "Ты — соискатель, ищущий работу на hh.ru. "
            "Отвечай на сообщения HR вежливо, профессионально, кратко (2-4 предложения). "
            "Учитывай контекст переписки. Язык: русский. "
            "Не используй шаблонные фразы — пиши живо. "
            "Не повторяй уже отправленные кандидатом сообщения и не дублируй просьбы "
            "в духе 'рассмотрите резюме', если это уже было сказано."
        )
        user_parts = [f"Вакансия: «{body.vacancy_name}»", f"Компания: «{body.employer_name}»"]
        if last_own_message:
            user_parts.append(f"Последнее сообщение кандидата: {last_own_message}")
        if last_employer_message:
            user_parts.append(f"Последнее сообщение работодателя: {last_employer_message}")
        if history_text:
            user_parts.append(f"\nИстория переписки:\n{history_text}")
        user_parts.append("\nНапиши мой следующий ответ работодателю.")

        client = _make_ai_client(
            profile,
            ["openai_reply", "openai_cover_letter", "openai"],
            system,
            temperature=0.7,
            max_tokens=400,
        )
        try:
            text = client.complete("\n".join(user_parts))
        except OpenAIError as ex:
            raise HTTPException(502, f"Ошибка AI: {ex}") from ex

    if not text:
        raise HTTPException(400, "Сообщение не может быть пустым")

    if not body.send:
        return {"ok": True, "profile": profile, "draft": text, "sent": False}
    if not body.confirm_live:
        raise HTTPException(409, "Sending a message requires confirm_live=true.")

    _hh_post(profile, f"/negotiations/{neg_id}/messages", {"message": text})
    return {"ok": True, "profile": profile, "sent": text, "draft": text}


# ---------------------------------------------------------------------------
# Routes: очистка отказов
# ---------------------------------------------------------------------------

@app.post("/api/inbox/clear-rejections")
def clear_rejections(
    profile: str = Query("default"),
    dry_run: bool = Query(False),
    confirm_live: bool = Query(False),
):
    """Скрыть все переписки со статусом 'discard' (отказ)."""
    profile = _validate_profile_name(profile)
    if not dry_run and not confirm_live:
        raise HTTPException(409, "Clearing conversations requires confirm_live=true.")
    total = 0
    cleared: list[int] = []
    errors: list[dict[str, Any]] = []
    failed_ids: set[int] = set()
    page = 0
    max_pages = 50  # защита от вечного цикла: 50 * 50 = до 2500 переписок

    while page < max_pages:
        data = _hh_get(
            profile,
            "/negotiations",
            {"status": "discard", "per_page": 50, "page": page},
        )
        total = max(total, int(data.get("found", 0) or 0))
        items = data.get("items", [])
        if not items:
            break
        fresh = [n for n in items if n.get("id") and n["id"] not in failed_ids and n["id"] not in cleared]
        if not fresh:
            break
        for n in fresh:
            neg_id = n["id"]
            if dry_run:
                cleared.append(neg_id)
                continue
            try:
                _hh_delete(profile, f"/negotiations/active/{neg_id}")
                cleared.append(neg_id)
            except HTTPException as e:
                failed_ids.add(neg_id)
                errors.append({"id": neg_id, "error": e.detail})
        if dry_run:
            # ничего не удаляем — просто листаем страницы
            if len(cleared) >= total:
                break
            page += 1
        else:
            # после скрытия список сдвигается — читаем первую страницу заново,
            # пока в ней есть что скрывать
            if len(items) < 50:
                break

    return {
        "total_discards": total,
        "cleared": len(cleared),
        "cleared_ids": cleared[:100],
        "errors": errors,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Routes: именованные шаблоны писем (хранятся в config.json → letter_templates)
# ---------------------------------------------------------------------------

@app.get("/api/letter-templates")
def list_letter_templates(profile: str = Query("default")):
    """Список всех шаблонов писем для профиля."""
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        return {"templates": {}, "default_templates": DEFAULT_LETTER_TEMPLATES}
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    return {
        "templates": cfg.get("letter_templates") or {},
        "default_templates": DEFAULT_LETTER_TEMPLATES,
    }


class LetterTemplateUpsert(BaseModel):
    name: str
    content: str
    profile: str = "default"


@app.post("/api/letter-templates")
def upsert_letter_template(body: LetterTemplateUpsert):
    """Создать или обновить именованный шаблон письма."""
    name = body.name.strip()
    if not name or not re.match(r"^[A-Za-z0-9_-]{1,64}$", name):
        raise HTTPException(400, "Имя шаблона: только буквы, цифры, _ и - (1-64 символа)")
    if len(body.content) > 10_000:
        raise HTTPException(400, "Шаблон слишком большой (>10KB)")
    cfg_path = _config_path(body.profile)
    if not cfg_path.exists():
        raise HTTPException(404, "Профиль не найден")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    templates = cfg.get("letter_templates") or {}
    templates[name] = body.content
    cfg["letter_templates"] = templates
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return {"ok": True, "name": name}


@app.delete("/api/letter-templates/{name}")
def delete_letter_template(name: str, profile: str = Query("default")):
    """Удалить шаблон письма."""
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        raise HTTPException(404, "Профиль не найден")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    templates = cfg.get("letter_templates") or {}
    if name not in templates:
        raise HTTPException(404, f"Шаблон '{name}' не найден")
    del templates[name]
    cfg["letter_templates"] = templates
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return {"ok": True}


@app.post("/api/letter-templates/seed")
def seed_letter_templates(profile: str = Query("default"), overwrite: bool = Query(False)):
    """
    Заполнить шаблоны встроенными заготовками.
    По умолчанию не перезаписывает уже существующие.
    """
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        raise HTTPException(404, "Профиль не найден")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    templates = cfg.get("letter_templates") or {}
    added = []
    for name, content in DEFAULT_LETTER_TEMPLATES.items():
        if overwrite or name not in templates:
            templates[name] = content
            added.append(name)
    cfg["letter_templates"] = templates
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return {"ok": True, "added": added, "total": len(templates)}


def _resolve_letter_file(profile: str, template_name: str) -> Path | None:
    """
    Достаёт шаблон по имени из config.json и записывает во временный файл профиля.
    Возвращает путь к файлу или None если шаблон не найден.
    """
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        return None
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)

    # Ищем сначала в пользовательских шаблонах, потом в встроенных
    templates = cfg.get("letter_templates") or {}
    content = templates.get(template_name) or DEFAULT_LETTER_TEMPLATES.get(template_name)
    if not content:
        return None

    # A unique per-run file prevents concurrent account jobs from overwriting
    # each other's letter. `_run_operation` removes files from this directory.
    tmp_dir = _profile_dir(profile) / ".admin-tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"letter-{uuid4().hex}.txt"
    tmp_path.write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Routes: шаблон письма (letter.txt)
# ---------------------------------------------------------------------------

LEGACY_LETTER_FILE = PROJECT_ROOT / "letter.txt"


def _profile_letter_file(profile: str) -> Path:
    return _profile_dir(_validate_profile_name(profile)) / "letter.txt"


@app.get("/api/letter-template")
def get_letter_template(profile: str = Query("default")):
    """Get the account-specific fallback letter used by the admin apply form."""
    profile = _validate_profile_name(profile)
    profile_file = _profile_letter_file(profile)
    if profile_file.exists():
        return {
            "content": profile_file.read_text(encoding="utf-8", errors="replace"),
            "exists": True,
            "profile": profile,
            "source": "profile",
        }
    # Preserve read-only compatibility with old single-account installations.
    if profile == constants.ADMIN_DEFAULT_PROFILE and LEGACY_LETTER_FILE.exists():
        return {
            "content": LEGACY_LETTER_FILE.read_text(encoding="utf-8", errors="replace"),
            "exists": True,
            "profile": profile,
            "source": "legacy",
        }
    return {"content": "", "exists": False, "profile": profile, "source": "none"}


class LetterTemplateUpdate(BaseModel):
    content: str


@app.put("/api/letter-template")
def update_letter_template(body: LetterTemplateUpdate, profile: str = Query("default")):
    """Save the fallback letter for exactly one account, never globally."""
    if len(body.content) > 50_000:
        raise HTTPException(400, "Шаблон слишком большой (>50KB)")
    profile = _validate_profile_name(profile)
    _ensure_profile_storage(profile)
    _profile_letter_file(profile).write_text(body.content, encoding="utf-8")
    return {"ok": True, "size": len(body.content), "profile": profile}


# ---------------------------------------------------------------------------
# Routes: полная форма запуска откликов
# ---------------------------------------------------------------------------

# Промпты по умолчанию для AI-генерации писем (лучше дефолтных в CLI)
DEFAULT_SYSTEM_PROMPT = (
    "Ты опытный специалист, помогающий писать сопроводительные письма для откликов на вакансии на hh.ru. "
    "Пиши живо, от первого лица, без шаблонных клише. "
    "Письмо должно быть персонализировано под конкретную вакансию и компанию. "
    "Объём: 3-4 предложения, не более 150 слов. Язык: русский. "
    "Не используй placeholder'ы — твой ответ отправляется напрямую."
)
DEFAULT_MESSAGE_PROMPT = (
    "Напиши краткое сопроводительное письмо от моего имени для вакансии"
)


class ApplyFullRequest(BaseModel):
    profile: str = "default"
    dry_run: bool = False
    confirm_live: bool = False
    confirm_external_email: bool = False
    # Поиск
    search: str = Field("", max_length=500)
    resume_id: str = Field("", max_length=256)
    resume_alias: str = Field("", max_length=128)
    # Фильтры
    experience: str = ""          # noExperience / between1And3 / between3And6 / moreThan6
    salary: int | None = Field(None, ge=0, le=10_000_000)
    only_with_salary: bool = False
    schedule: list[str] = Field(default_factory=list)      # fullDay, shift, flexible, remote, flyInFlyOut
    employment: list[str] = Field(default_factory=list)    # full, part, project, volunteer, probation
    area: list[str] = Field(default_factory=list)          # коды городов: "1" = Москва, "2" = СПб
    excluded_filter: str = Field("", max_length=500)     # regex для исключения по названию
    # AI
    ai_filter: str = ""           # "" | "light" | "heavy"
    use_ai: bool = False          # AI-генерация писем через OpenAI
    system_prompt: str = Field("", max_length=8_000)       # если пусто — используется DEFAULT_SYSTEM_PROMPT
    message_prompt: str = Field("", max_length=8_000)      # если пусто — используется DEFAULT_MESSAGE_PROMPT
    # Письмо
    force_message: bool = False   # всегда прикреплять письмо
    template_name: str = Field("", max_length=64)       # имя шаблона из letter_templates (если не use_ai)
    # Тесты
    skip_tests: bool = True
    # Контроль
    max_responses: int = Field(100, ge=0, le=1_000)
    response_delay: str = Field("1-3", max_length=32)
    per_page: int = Field(20, ge=1, le=100)
    total_pages: int = Field(1, ge=1, le=100)
    send_email: bool = False


def _validate_apply_request(
    body: ApplyFullRequest,
    *,
    require_live_confirmation: bool = False,
    allow_profile_lanes: bool = False,
) -> None:
    """Validate values that need cross-field or CLI-compatible constraints."""
    _validate_profile_name(body.profile)
    allowed_schedule = {"fullDay", "shift", "flexible", "remote", "flyInFlyOut"}
    allowed_employment = {"full", "part", "project", "volunteer", "probation"}
    if not set(body.schedule).issubset(allowed_schedule):
        raise HTTPException(422, "Unsupported schedule value.")
    if len(body.schedule) > 1:
        raise HTTPException(
            422,
            "The HH search endpoint supports one schedule per run; choose one schedule.",
        )
    if not set(body.employment).issubset(allowed_employment):
        raise HTTPException(422, "Unsupported employment value.")
    if body.experience and body.experience not in {
        "noExperience",
        "between1And3",
        "between3And6",
        "moreThan6",
    }:
        raise HTTPException(422, "Unsupported experience value.")
    if body.ai_filter and body.ai_filter not in {"light", "heavy"}:
        raise HTTPException(422, "Unsupported AI filter value.")
    if any(not area.isdigit() for area in body.area):
        raise HTTPException(422, "Each area must be a numeric HH area id.")
    try:
        delay_parts = [float(part) for part in body.response_delay.split("-")]
    except ValueError as ex:
        raise HTTPException(422, "Response delay must be a number or min-max range.") from ex
    if len(delay_parts) not in {1, 2} or any(part < 0 for part in delay_parts):
        raise HTTPException(422, "Response delay must be non-negative.")
    if len(delay_parts) == 2 and delay_parts[0] > delay_parts[1]:
        raise HTTPException(422, "Response delay minimum cannot exceed maximum.")
    if body.excluded_filter:
        try:
            re.compile(body.excluded_filter)
        except re.error as ex:
            raise HTTPException(422, f"Invalid excluded-filter regex: {ex}") from ex
    if body.resume_id and body.resume_alias:
        raise HTTPException(422, "Choose resume_id or resume_alias, not both.")
    if require_live_confirmation and not body.dry_run:
        if not body.confirm_live:
            raise HTTPException(409, "Live applications require confirm_live=true.")
        if not (body.resume_id or body.resume_alias or allow_profile_lanes):
            raise HTTPException(
                422,
                "Choose one resume_id/resume_alias or configure profile apply lanes.",
            )
        if body.send_email and not body.confirm_external_email:
            raise HTTPException(
                409,
                "External email requires confirm_external_email=true.",
            )


def _build_apply_args(body: ApplyFullRequest) -> list[str]:
    """Собирает список аргументов CLI для apply-vacancies из ApplyFullRequest."""
    _validate_apply_request(body)
    args: list[str] = []

    if body.dry_run:
        args.append("--dry-run")
    if body.search:
        args += ["--search", body.search]
    if body.resume_id:
        args += ["--resume-id", body.resume_id]
    elif body.resume_alias:
        args += ["--resume-alias", body.resume_alias]
    if body.experience:
        args += ["--experience", body.experience]
    if body.salary is not None:
        args += ["--salary", str(body.salary)]
    if body.only_with_salary:
        args.append("--only-with-salary")
    if body.schedule:
        args += ["--schedule", body.schedule[0]]
    if body.employment:
        args += ["--employment", *body.employment]
    if body.area:
        args += ["--area", *body.area]
    if body.excluded_filter:
        args += ["--excluded-filter", body.excluded_filter]
    if body.ai_filter:
        args += ["--ai-filter", body.ai_filter]
    if body.use_ai:
        args.append("--use-ai")
        # Используем улучшенные промпты если пользователь не задал свои
        system_prompt = body.system_prompt or DEFAULT_SYSTEM_PROMPT
        message_prompt = body.message_prompt or DEFAULT_MESSAGE_PROMPT
        args += ["--system-prompt", system_prompt]
        args += ["--message-prompt", message_prompt]
    elif body.template_name:
        # Не AI: используем шаблон из библиотеки
        letter_path = _resolve_letter_file(body.profile, body.template_name)
        if letter_path:
            args += ["--letter-file", str(letter_path)]
        # Если шаблон не найден — продолжаем без него (будет дефолтное)
    else:
        profile_letter = _profile_letter_file(body.profile)
        if profile_letter.exists():
            args += ["--letter-file", str(profile_letter)]
        elif body.profile == constants.ADMIN_DEFAULT_PROFILE and LEGACY_LETTER_FILE.exists():
            args += ["--letter-file", str(LEGACY_LETTER_FILE)]
    if body.force_message:
        args.append("--force-message")
    if body.skip_tests:
        args.append("--skip-tests")
    if body.send_email:
        args.append("--send-email")
    args += ["--max-responses", str(body.max_responses)]
    args += ["--response-delay", body.response_delay]
    args += ["--per-page", str(body.per_page)]
    args += ["--total-pages", str(body.total_pages)]
    return args


@app.post("/api/run/apply-vacancies-full")
def run_apply_vacancies_full(body: ApplyFullRequest):
    """Запустить автоотклики со всеми параметрами."""
    _validate_apply_request(body, require_live_confirmation=True)
    req = RunRequest(profile=body.profile, confirm_live=body.confirm_live)
    return _run_operation("apply-safe", req, extra=_build_apply_args(body))


# ---------------------------------------------------------------------------
# Routes: token status + agent-friendly operations
# ---------------------------------------------------------------------------

def _get_token_info(profile: str) -> dict:
    """Возвращает состояние токена без сетевых запросов."""
    cfg_path = _config_path(profile)
    if not cfg_path.exists():
        return {"status": "no_config", "profile": profile}
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as ex:
        return {"status": "read_error", "error": str(ex), "profile": profile}

    token = cfg.get("token") or {}
    access_token = token.get("access_token")
    refresh_token_val = token.get("refresh_token")
    expires_at = token.get("access_expires_at")

    if not access_token:
        return {"status": "no_token", "profile": profile, "can_refresh": False}

    now = time.time()
    expires_in = None
    expired = False
    if expires_at:
        expires_in = int(expires_at - now)
        expired = expires_in <= 0

    return {
        "status": "expired" if expired else "ok",
        "profile": profile,
        "expired": expired,
        "expires_in_seconds": expires_in,
        "can_refresh": bool(refresh_token_val),
        "has_access_token": bool(access_token),
        "has_refresh_token": bool(refresh_token_val),
    }


def _refresh_token_sync(profile: str, timeout: int = 30) -> dict:
    """Запускает refresh-token CLI и возвращает результат."""
    cmd = _build_local_cli_cmd(["--profile-id", profile, "--no-auto-auth", "refresh-token"])
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["CONFIG_DIR"] = str(_config_root())
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
            env=env,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "refresh-token timeout", "returncode": -1}
    except Exception as ex:
        return {"ok": False, "error": str(ex), "returncode": -1}


@app.get("/api/token-status")
def get_token_status(profile: str = Query("default")):
    """
    Быстрая проверка токена без сетевых запросов.
    Возвращает: status (ok/expired/no_token/no_config), expires_in_seconds, can_refresh.
    Использовать перед запуском операций чтобы знать нужен ли refresh или re-auth.
    """
    return _get_token_info(_validate_profile_name(profile))


@app.get("/api/agent/preflight")
def agent_preflight(profile: str = Query("default")):
    """
    Pre-flight проверка для AI агента: всё ли готово чтобы запускать операции?
    Возвращает: ready (bool), нужен ли refresh, нужен ли full re-auth.
    """
    profile = _validate_profile_name(profile)
    token = _get_token_info(profile)

    cfg_path = _config_path(profile)
    db = _db_path(profile)

    return {
        "ready": token["status"] == "ok",
        "profile": profile,
        "token_status": token["status"],       # ok / expired / no_token / no_config
        "token_expires_in": token.get("expires_in_seconds"),
        "can_refresh": token.get("can_refresh", False),
        "needs_reauth": token["status"] in ("no_token", "no_config"),
        "needs_refresh": token["status"] == "expired" and token.get("can_refresh", False),
        "config_exists": cfg_path.exists(),
        "db_exists": db.exists(),
        "action": (
            "run"           if token["status"] == "ok" else
            "refresh"       if (token["status"] == "expired" and token.get("can_refresh")) else
            "reauth"        # нужна ручная авторизация
        ),
    }


class AgentRunRequest(BaseModel):
    """
    Запрос операции от AI агента.
    Агент вызывает этот endpoint — панель сама:
    1. Проверит токен
    2. Обновит если истёк (и есть refresh_token)
    3. Запустит операцию
    4. Вернёт op_id для polling статуса

    Для apply-vacancies можно передать apply_params вместо голых args —
    тогда шаблоны писем и промпты разрешаются автоматически.
    """
    profile: str = "default"
    operation: str = "apply-vacancies"   # apply-vacancies | update-resumes | refresh-token
    auto_refresh: bool = True            # попробовать refresh-token если истёк
    confirm_live: bool = False
    args: list[str] = Field(default_factory=list, max_length=40)   # дополнительные аргументы CLI
    # Удобный способ запустить apply-vacancies без ручного составления args
    apply_params: ApplyFullRequest | None = None


@app.post("/api/agent/run")
def agent_run(body: AgentRunRequest):
    """
    Единая точка входа для AI агента.
    Агент не должен думать о токенах — всё обрабатывается автоматически.
    """
    profile = _validate_profile_name(body.profile)
    allowed_ops = {"apply-vacancies", "update-resumes", "refresh-token", "reply-employers"}
    if body.operation not in allowed_ops:
        raise HTTPException(400, f"Операция должна быть одной из: {', '.join(sorted(allowed_ops))}")
    if any(arg in {"--profile-id", "--no-auto-auth"} for arg in body.args):
        raise HTTPException(400, "Agent arguments cannot override the account or auth mode.")

    token = _get_token_info(profile)

    # Если токен истёк и можно обновить — обновляем синхронно перед запуском
    refreshed = False
    if body.auto_refresh and token["status"] == "expired" and token.get("can_refresh"):
        refresh_result = _refresh_token_sync(profile)
        if refresh_result["ok"]:
            refreshed = True
            token = _get_token_info(profile)  # перечитать после refresh
        else:
            raise HTTPException(
                502,
                f"Не удалось обновить токен: {refresh_result.get('stderr', '')[:300]}. "
                "Нужна ручная авторизация: запустите 'python -m hh_applicant_tool auth' в терминале."
            )

    # Если токена нет вообще — нужна ручная авторизация
    if token["status"] in ("no_token", "no_config"):
        raise HTTPException(
            401,
            "Нет токена авторизации. Нужна ручная авторизация: "
            "запустите 'python -m hh_applicant_tool auth' в терминале. "
            "Это единственное что нельзя автоматизировать — ввод SMS-кода."
        )

    if token["status"] == "expired":
        raise HTTPException(
            401,
            "Токен истёк и нет refresh_token. "
            "Нужна ручная авторизация: запустите 'python -m hh_applicant_tool auth' в терминале."
        )

    # Application runs use the same safe semantics as production cron.
    # Explicit resume selector -> one apply-safe run.
    # No selector -> tracked profile lanes are mandatory and own search/filter
    # configuration; agent request fields may not silently override a lane.
    extra_args = list(body.args)
    actual_operation = body.operation
    if body.operation == "apply-vacancies":
        params = body.apply_params
        requested_param_fields: set[str] = set()
        if params:
            requested_param_fields = set(params.model_fields_set)
            params.profile = profile
            params.confirm_live = body.confirm_live
            has_selector = bool(params.resume_id or params.resume_alias)
        else:
            has_selector = any(
                arg in {"--resume-id", "--resume-alias"} for arg in extra_args
            )

        lane_file = PROJECT_ROOT / "rules" / "apply-lanes" / f"{profile}.json"
        use_profile_lanes = not has_selector and lane_file.is_file()

        if params:
            _validate_apply_request(
                params,
                require_live_confirmation=True,
                allow_profile_lanes=use_profile_lanes,
            )

        if use_profile_lanes:
            allowed_lane_fields = {"profile", "dry_run", "confirm_live"}
            unexpected_fields = requested_param_fields - allowed_lane_fields
            if unexpected_fields:
                raise HTTPException(
                    422,
                    "Profile lanes own search/filter/resume settings; remove "
                    f"apply_params fields: {', '.join(sorted(unexpected_fields))}.",
                )
            unexpected_args = [
                arg for arg in extra_args if arg not in {"--dry-run", "--live"}
            ]
            if unexpected_args:
                raise HTTPException(
                    422,
                    "Profile lane runs only accept dry-run/live mode from agent args.",
                )
            is_dry_run = bool(params and params.dry_run) or "--dry-run" in extra_args
            extra_args = ["--dry-run" if is_dry_run else "--live"]
            actual_operation = "apply-profile"
        else:
            if not has_selector:
                raise HTTPException(
                    422,
                    "Live/scheduled agent apply needs a resume selector or "
                    "tracked profile apply lanes.",
                )
            if params:
                extra_args = _build_apply_args(params) + extra_args
            actual_operation = "apply-safe"

    req = RunRequest(
        profile=profile,
        extra_args=extra_args,
        confirm_live=body.confirm_live,
    )
    result = _run_operation(actual_operation, req)
    result["refreshed_token"] = refreshed
    result["token_status"] = token["status"]
    return result


# ---------------------------------------------------------------------------
# Routes: дайджест / daily summary для агента
# ---------------------------------------------------------------------------

@app.get("/api/agent/digest")
def agent_digest(profile: str = Query("default")):
    """
    Компактный дайджест для AI агента — одним вызовом получить полную картину.
    Включает: статистику за сегодня, входящие требующие ответа,
    статус токена, последние ошибки из лога.
    """
    import datetime as dt

    profile = _validate_profile_name(profile)
    token = _get_token_info(profile)

    # --- Статистика из БД ---
    today_str = dt.date.today().isoformat()
    stats: dict[str, Any] = {}
    inbox_needs_reply: list[dict] = []

    try:
        conn = get_conn(profile)
        try:
            # Отклики за сегодня
            stats["applied_today"] = (q1(conn,
                "SELECT count(*) as c FROM negotiations WHERE date(created_at) = ?", (today_str,)) or {}).get("c", 0)
            # По статусам
            states = q(conn, "SELECT state, count(*) as cnt FROM negotiations GROUP BY state ORDER BY cnt DESC")
            stats["by_state"] = {r["state"]: r["cnt"] for r in states}
            # Итого
            stats["total_applied"] = (q1(conn, "SELECT count(*) as c FROM negotiations") or {}).get("c", 0)
            stats["total_skipped"] = (q1(conn, "SELECT count(*) as c FROM skipped_vacancies") or {}).get("c", 0)
            # Резюме
            resumes = q(conn, "SELECT title, total_views, new_views FROM resumes")
            stats["resumes"] = resumes
        finally:
            conn.close()
    except HTTPException:
        stats["db_error"] = "DB not found"

    # --- Входящие требующие ответа (из HH API) ---
    try:
        inbox_data = _hh_get(profile, "/negotiations", {"per_page": 50, "page": 0})
        for n in inbox_data.get("items", []):
            if not n.get("has_updates"):
                continue
            review_item = _build_negotiation_review_item(
                profile,
                n,
                include_last_message_author=False,
            )
            if review_item["recommended_action"] == "skip_rejection":
                continue
            inbox_needs_reply.append(review_item)
    except HTTPException:
        pass   # нет токена — пропускаем

    # --- Последние строки лога (ошибки) ---
    recent_errors: list[str] = []
    log_path = _log_path(profile)
    if log_path.exists():
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        recent_errors = [
            l.strip() for l in lines[-100:]
            if "ERROR" in l or "WARNING" in l
        ][-10:]

    return {
        "profile": profile,
        "date": today_str,
        "token": {
            "status": token["status"],
            "expires_in_seconds": token.get("expires_in_seconds"),
            "can_refresh": token.get("can_refresh", False),
        },
        "stats": stats,
        "inbox_needs_reply": inbox_needs_reply,
        "inbox_needs_reply_count": len(inbox_needs_reply),
        "recent_errors": recent_errors,
        "action_needed": (
            "reauth" if token["status"] in ("no_token", "no_config")
            else "refresh" if token["status"] == "expired" and token.get("can_refresh")
            else "reply_inbox" if inbox_needs_reply
            else "none"
        ),
    }


# ---------------------------------------------------------------------------
# Routes: контент резюме (для контекстных AI-писем)
# ---------------------------------------------------------------------------

@app.get("/api/resumes/{resume_id}/content")
def get_resume_content(resume_id: str, profile: str = Query("default")):
    """
    Получить текст резюме из HH API для использования в AI-генерации писем.
    Возвращает структурированный текст: навыки, опыт, образование.
    Передайте этот текст в system_prompt при запуске apply-vacancies.
    """
    profile = _validate_profile_name(profile)
    data = _hh_get(profile, f"/resumes/{resume_id}")

    def _strip(html_text: str) -> str:
        """Убирает HTML-теги."""
        return re.sub(r"<[^>]+>", " ", html_text or "").strip()

    lines: list[str] = []

    # Заголовок
    lines.append(f"РЕЗЮМЕ: {data.get('title', '')}")
    lines.append(f"Статус: {(data.get('status') or {}).get('name', '')}")

    # Зарплата
    sal = data.get("salary")
    if sal:
        lines.append(f"Ожидаемая зарплата: {sal.get('amount', '')} {sal.get('currency', '')}")

    # Навыки
    skills = [s.get("name", "") for s in (data.get("skills") or [])]
    if skills:
        lines.append(f"Ключевые навыки: {', '.join(skills)}")

    skill_set = data.get("skill_set") or []
    if skill_set:
        lines.append(f"Технологии: {', '.join(skill_set)}")

    # Опыт
    experiences = data.get("experience") or []
    if experiences:
        lines.append("\nОПЫТ РАБОТЫ:")
        for exp in experiences[:5]:  # топ-5
            company = exp.get("company") or (exp.get("employer") or {}).get("name") or ""
            position = exp.get("position") or ""
            start = exp.get("start") or ""
            end = exp.get("end") or "по настоящее время"
            desc = _strip(exp.get("description") or "")[:300]
            lines.append(f"- {position} в {company} ({start[:7]} — {end[:7] if end != 'по настоящее время' else end})")
            if desc:
                lines.append(f"  {desc}")

    # Образование
    educations = data.get("education") or {}
    edu_list = educations.get("primary") or []
    if edu_list:
        lines.append("\nОБРАЗОВАНИЕ:")
        for edu in edu_list[:2]:
            lines.append(f"- {edu.get('name', '')} — {edu.get('organization', '')}")

    resume_text = "\n".join(lines)
    return {
        "resume_id": resume_id,
        "title": data.get("title"),
        "text": resume_text,
        "system_prompt_suggestion": (
            f"Ты — соискатель с опытом. Вот твоё резюме:\n\n{resume_text}\n\n"
            "На основе этого резюме пиши персонализированные сопроводительные письма. "
            "Письмо должно быть кратким (3-4 предложения), живым, без шаблонных фраз. "
            "Выдели один-два факта из опыта, релевантных вакансии. Язык: русский."
        ),
    }


# ---------------------------------------------------------------------------
# Routes: управление чёрным списком работодателей
# ---------------------------------------------------------------------------

@app.get("/api/employers/blacklist")
def get_blacklist(profile: str = Query("default")):
    """Получить список заблокированных работодателей из HH API."""
    data = _hh_get(profile, "/employers/blacklisted", {"per_page": 50})
    items = []
    for e in data.get("items", []):
        items.append({
            "id": e.get("id"),
            "name": e.get("name"),
            "alternate_url": e.get("alternate_url"),
            "logo": (e.get("logo_urls") or {}).get("90"),
        })
    return {"items": items, "found": data.get("found", 0)}


@app.post("/api/employers/blacklist/{employer_id}")
def add_to_blacklist(
    employer_id: str,
    profile: str = Query("default"),
    confirm_live: bool = Query(False),
):
    """Добавить работодателя в чёрный список HH."""
    if not employer_id.isdigit():
        raise HTTPException(400, "employer_id должен быть числом")
    if not confirm_live:
        raise HTTPException(409, "Blacklisting requires confirm_live=true.")
    _hh_request(profile, "PUT", f"/employers/blacklisted/{employer_id}")
    return {"ok": True, "employer_id": employer_id, "action": "blacklisted"}


@app.delete("/api/employers/blacklist/{employer_id}")
def remove_from_blacklist(
    employer_id: str,
    profile: str = Query("default"),
    confirm_live: bool = Query(False),
):
    """Удалить работодателя из чёрного списка HH."""
    if not employer_id.isdigit():
        raise HTTPException(400, "employer_id должен быть числом")
    if not confirm_live:
        raise HTTPException(409, "Removing a blacklist entry requires confirm_live=true.")
    _hh_delete(profile, f"/employers/blacklisted/{employer_id}")
    return {"ok": True, "employer_id": employer_id, "action": "unblacklisted"}


# ---------------------------------------------------------------------------
# Routes: массовый ответ работодателям (reply-employers CLI)
# ---------------------------------------------------------------------------

class ReplyEmployersRequest(BaseModel):
    profile: str = "default"
    use_ai: bool = True
    only_invitations: bool = False   # только отвечать на приглашения
    dry_run: bool = False
    confirm_live: bool = False
    max_pages: int = Field(10, ge=1, le=50)
    period: int | None = Field(None, ge=0, le=3650)
    system_prompt: str = Field("", max_length=8_000)
    message_prompt: str = Field("", max_length=8_000)
    reply_message: str = Field("", max_length=4_000)


@app.post("/api/run/reply-employers")
def run_reply_employers(body: ReplyEmployersRequest):
    """
    Запустить reply-employers — автоматически ответить на все переписки где
    последнее сообщение от работодателя.
    CLI читает полную историю чата и передаёт AI для контекстного ответа.
    """
    args: list[str] = []
    if body.dry_run:
        args.append("--dry-run")
    if body.use_ai:
        args.append("--use-ai")
        sp = body.system_prompt or (
            "Ты — соискатель на hh.ru. Отвечай на сообщения HR вежливо, "
            "кратко, профессионально. Учитывай историю переписки. Язык: русский."
        )
        mp = body.message_prompt or "Напиши ответ работодателю на основе истории переписки."
        args += ["--system-prompt", sp, "--message-prompt", mp]
    if body.reply_message:
        args += ["--reply-message", body.reply_message]
    if body.only_invitations:
        args.append("--only-invitations")
    if body.period is not None:
        args += ["--period", str(body.period)]
    args += ["--max-pages", str(body.max_pages)]

    req = RunRequest(profile=body.profile, confirm_live=body.confirm_live)
    return _run_operation("reply-employers", req, extra=args)


# ---------------------------------------------------------------------------
# Dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("admin.app:app", host=constants.ADMIN_LOCALHOST, port=constants.ADMIN_DEFAULT_PORT, reload=True)
