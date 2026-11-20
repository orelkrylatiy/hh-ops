from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

import admin.app as admin_app
from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.resume import ResumeModel
from admin.automation_settings import AutomationSettings


def test_resume_model_reads_top_level_hh_view_counters() -> None:
    resume = ResumeModel.from_api({
        "id": "resume-1",
        "title": "Backend developer",
        "status": {"id": "published", "name": "Published"},
        "total_views": 88,
        "new_views": 4,
    })

    assert resume.total_views == 88
    assert resume.new_views == 4
    assert resume.to_db()["total_views"] == 88
    assert resume.to_db()["new_views"] == 4


def test_resume_model_does_not_turn_missing_counters_into_zero() -> None:
    resume = ResumeModel.from_api({
        "id": "resume-2",
        "title": "Frontend developer",
        "status": {"id": "published", "name": "Published"},
    })

    assert resume.total_views is None
    assert resume.new_views is None
    assert resume.views_updated_at is None


def test_resume_repository_persists_counter_freshness() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        storage = StorageFacade(conn)
        storage.resumes.save_batch([{
            "id": "resume-3",
            "title": "Data engineer",
            "status": {"id": "published", "name": "Published"},
            "total_views": 0,
            "new_views": 0,
        }])
        row = conn.execute(
            "SELECT total_views, new_views, views_updated_at FROM resumes WHERE id = ?",
            ("resume-3",),
        ).fetchone()
        assert row is not None
        assert row[0:2] == (0, 0)
        assert row[2]
    finally:
        conn.close()


def test_resume_repository_keeps_unavailable_counters_unknown() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        storage = StorageFacade(conn)
        storage.resumes.save_batch([{
            "id": "resume-4",
            "title": "Product manager",
            "status": {"id": "published", "name": "Published"},
        }])
        row = conn.execute(
            "SELECT total_views, new_views, views_updated_at FROM resumes WHERE id = ?",
            ("resume-4",),
        ).fetchone()
        assert row == (None, None, None)
    finally:
        conn.close()


def test_selected_analytics_scope_aggregates_only_selected_profiles(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    client = TestClient(admin_app.app)
    for profile in ("account-a", "account-b", "account-c"):
        assert client.post("/api/profiles", json={"profile": profile}).status_code == 200
        with sqlite3.connect(tmp_path / profile / "data") as conn:
            conn.execute(
                """INSERT INTO negotiations
                (id, state, vacancy_id, chat_id, created_at, updated_at)
                VALUES (1, 'response', 100, 200, '2026-11-18', '2026-11-18')"""
            )

    response = client.get("/api/stats", params=[
        ("profile", "account-a"),
        ("scope", "selected"),
        ("days", "30"),
        ("profiles", "account-a"),
        ("profiles", "account-c"),
    ])

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "selected"
    assert payload["profiles"] == ["account-a", "account-c"]
    assert payload["totals"]["negotiations"] == 2
    assert {item["profile"] for item in payload["profile_totals"]} == {
        "account-a", "account-c"
    }


def test_selected_analytics_scope_rejects_more_than_five_profiles(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    client = TestClient(admin_app.app)
    profiles = [f"account-{index}" for index in range(6)]
    for profile in profiles:
        assert client.post("/api/profiles", json={"profile": profile}).status_code == 200

    params = [("scope", "selected"), ("days", "30")]
    params.extend(("profiles", profile) for profile in profiles)
    response = client.get("/api/stats", params=params)

    assert response.status_code == 422


def test_vacancies_can_be_sorted_and_show_the_applying_account(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    client = TestClient(admin_app.app)
    for profile in ("account-a", "account-b"):
        assert client.post("/api/profiles", json={"profile": profile}).status_code == 200

    with sqlite3.connect(tmp_path / "account-a" / "data") as conn:
        conn.execute(
            """INSERT INTO vacancies (id, name, salary_from, created_at, updated_at)
            VALUES (101, 'Backend developer', 100000, '2026-11-18', '2026-11-18')"""
        )
        conn.execute(
            """INSERT INTO negotiations
            (id, state, vacancy_id, chat_id, resume_id, created_at, updated_at)
            VALUES (501, 'response', 101, 9001, 'resume-a', '2026-11-19', '2026-11-19')"""
        )
    with sqlite3.connect(tmp_path / "account-b" / "data") as conn:
        conn.execute(
            """INSERT INTO vacancies (id, name, salary_from, created_at, updated_at)
            VALUES (202, 'Senior engineer', 200000, '2026-11-17', '2026-11-17')"""
        )

    response = client.get(
        "/api/vacancies",
        params={"profile": "account-a", "scope": "all", "sort_by": "salary_from", "sort_order": "desc"},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [202, 101]
    applied = next(item for item in items if item["id"] == 101)
    assert applied["profile"] == "account-a"
    assert applied["has_applied"] == 1
    assert applied["application_state"] == "response"
    assert applied["applied_resume_id"] == "resume-a"


def test_batch_run_starts_each_selected_profile_and_requires_live_confirmation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    client = TestClient(admin_app.app)
    for profile in ("account-a", "account-b"):
        assert client.post("/api/profiles", json={"profile": profile}).status_code == 200

    calls = []

    def fake_run(operation, request, extra=None):
        calls.append((operation, request.profile, list(extra or []), request.confirm_live))
        return {
            "op_id": f"op-{request.profile}",
            "operation": operation,
            "profile": request.profile,
            "dry_run": request.dry_run,
        }

    monkeypatch.setattr(admin_app, "_run_operation", fake_run)
    response = client.post("/api/run/batch", json={
        "profiles": ["account-a", "account-b"],
        "operation": "safe-apply",
        "dry_run": True,
    })

    assert response.status_code == 200
    assert response.json()["started"] == 2
    assert [call[1] for call in calls] == ["account-a", "account-b"]
    assert all("--dry-run" in call[2] for call in calls)

    live_response = client.post("/api/run/batch", json={
        "profiles": ["account-a"],
        "operation": "safe-apply",
        "dry_run": False,
    })
    assert live_response.status_code == 409


def test_refresh_resumes_route_uses_read_only_list_resumes_operation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    client = TestClient(admin_app.app)
    calls = []

    def fake_run(operation, request, extra=None):
        calls.append((operation, request.profile, request.dry_run, request.confirm_live))
        return {"op_id": "refresh-1", "profile": request.profile}

    monkeypatch.setattr(admin_app, "_run_operation", fake_run)
    response = client.post(
        "/api/run/refresh-resumes",
        json={"profile": "account-a"},
    )

    assert response.status_code == 200
    assert calls == [("list-resumes", "account-a", True, False)]


def test_paused_automation_disables_cron_without_losing_selected_mode() -> None:
    settings = AutomationSettings(mode="live", paused=True)
    env = settings.to_env()

    assert env["HH_AUTOMATION_MODE"] == "off"
    assert env["HH_AUTOMATION_PAUSED"] == "on"
    assert settings.mode == "live"


def test_live_summary_distinguishes_real_zero_from_missing_hh_counters(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    client = TestClient(admin_app.app)

    def fake_hh_get(profile, endpoint, params=None):
        if endpoint == "/negotiations":
            return {"found": 1, "items": [{"created_at": "2026-11-19T10:00:00Z"}]}
        if endpoint == "/resumes/mine":
            return {
                "found": 2,
                "items": [
                    {"id": "resume-zero", "title": "No views", "total_views": 0, "new_views": 0},
                    {"id": "resume-unknown", "title": "No counters in response"},
                ],
            }
        raise AssertionError(f"Unexpected HH endpoint: {endpoint}")

    monkeypatch.setattr(admin_app, "_hh_get", fake_hh_get)
    response = client.get("/api/live-summary", params={"profile": "account-a"})

    assert response.status_code == 200
    counters = response.json()["resume_views"]
    assert counters[0]["total_views"] == 0
    assert counters[0]["new_views"] == 0
    assert counters[0]["views_updated_at"]
    assert counters[1]["total_views"] is None
    assert counters[1]["new_views"] is None
    assert counters[1]["views_updated_at"] is None


def test_cover_letter_endpoint_rejects_blank_vacancy_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    client = TestClient(admin_app.app)

    response = client.post(
        "/api/generate-letter",
        json={"profile": "account-a", "vacancy_name": "   "},
    )

    assert response.status_code == 422


def test_partial_resume_sync_preserves_last_known_counters_as_stale() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        storage = StorageFacade(conn)
        storage.resumes.save_batch([{
            "id": "resume-partial",
            "title": "Data engineer",
            "status": {"id": "published", "name": "Published"},
            "total_views": 12,
            "new_views": 3,
        }])
        storage.resumes.save_batch([{
            "id": "resume-partial",
            "status": {"id": "published", "name": "Published"},
        }])
        row = conn.execute(
            "SELECT total_views, new_views, views_updated_at, title FROM resumes WHERE id = ?",
            ("resume-partial",),
        ).fetchone()
        assert row is not None
        assert row[0:2] == (12, 3)
        assert row[2] is None
        assert row[3] == "Data engineer"
    finally:
        conn.close()


def test_automation_can_be_paused_without_enabling_live_actions(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    client = TestClient(admin_app.app)

    paused = client.put("/api/automation-settings", json={
        "settings": {"mode": "live", "paused": True},
        "confirm_live": False,
    })
    assert paused.status_code == 200
    assert paused.json()["settings"]["mode"] == "live"
    assert paused.json()["settings"]["paused"] is True
    env_text = (tmp_path / "admin_automation.env").read_text(encoding="utf-8")
    assert 'HH_AUTOMATION_MODE=off' in env_text
    assert 'HH_AUTOMATION_PAUSED=on' in env_text

    resume_without_confirmation = client.put("/api/automation-settings", json={
        "settings": {"mode": "live", "paused": False},
        "confirm_live": False,
    })
    assert resume_without_confirmation.status_code == 409

    resume_confirmed = client.put("/api/automation-settings", json={
        "settings": {"mode": "live", "paused": False},
        "confirm_live": True,
    })
    assert resume_confirmed.status_code == 200

