"""Offline regression tests for the Linux HH admin control plane."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import admin.app as admin
from admin.automation_settings import AutomationSettings, save_settings


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ADMIN_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    return TestClient(admin.app)


def test_admin_requires_explicit_login_when_docker_auth_enforced(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_REQUIRE_AUTH", "1")
    assert client.get("/health").status_code == 200
    assert client.get("/api/accounts").status_code == 500
    monkeypatch.setenv("ADMIN_USERNAME", "operator")
    monkeypatch.setenv("ADMIN_PASSWORD", "the-test-only-strong-password")
    assert client.get("/api/accounts").status_code == 401
    token = base64.b64encode(b"operator:the-test-only-strong-password").decode()
    resp = client.get("/api/accounts", headers={"Authorization": "Basic " + token})
    assert resp.status_code == 200


def test_admin_cron_configuration_is_persistent_and_requires_live_confirmation(
    client: TestClient, tmp_path: Path
) -> None:
    initial = client.get("/api/automation-settings")
    assert initial.status_code == 200
    assert initial.json()["source"] == "environment"
    settings = dict(initial.json()["settings"])
    settings.update({
        "mode": "live",
        "search_query": "React developer; $(touch /tmp/NO_HH_EXECUTION)",
        "apply_limit": 7,
        "followups_enabled": True,
    })
    unconfirmed = client.put("/api/automation-settings", json={"settings": settings})
    assert unconfirmed.status_code == 409
    assert not (tmp_path / "admin_automation.env").exists()

    confirmed = client.put(
        "/api/automation-settings", json={"settings": settings, "confirm_live": True}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["applies_to"] == "next scheduled cron job"
    assert (tmp_path / "admin_automation.json").exists()
    env = tmp_path / "admin_automation.env"
    assert env.exists()
    assert "HH_AUTOMATION_MODE" in env.read_text(encoding="utf-8")
    assert "token" not in env.read_text(encoding="utf-8").lower()
    assert env.stat().st_mode & 0o077 == 0

    probe = subprocess.run(
        ["bash", "-c", f"source {env}; printf '%s' \"$SEARCH_QUERY\""],
        text=True, capture_output=True, check=True,
    )
    assert probe.stdout == settings["search_query"]
    assert not Path("/tmp/NO_HH_EXECUTION").exists()
    persisted = client.get("/api/automation-settings")
    assert persisted.json()["source"] == "admin_override"
    assert persisted.json()["settings"]["apply_limit"] == 7

    invalid = client.put(
        "/api/automation-settings",
        json={"settings": {**settings, "followup_after_days": 60, "max_chat_age_days": 5},
              "confirm_live": True},
    )
    assert invalid.status_code == 400
    assert client.get("/api/automation-settings").json()["settings"]["apply_limit"] == 7


def test_cron_override_can_switch_automation_off_without_modifying_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_settings(tmp_path, AutomationSettings(mode="off"))
    env = os.environ.copy()
    env["CONFIG_DIR"] = str(tmp_path)
    env["HH_AUTOMATION_MODE"] = "live"
    result = subprocess.run(
        ["bash", "scripts/cron-job.sh", "reply"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "automation disabled" in result.stdout


def test_live_summary_reads_hh_on_demand_and_exposes_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_hh(profile: str, path: str, params: dict) -> dict:
        calls.append((profile, path))
        if path == "/negotiations":
            return {"found": 97, "items": []}
        if path == "/resumes/mine":
            return {
                "found": 2,
                "items": [
                    {"id": "r1", "title": "Backend", "total_views": 88, "new_views": 4}
                ],
            }
        raise AssertionError(path)

    monkeypatch.setattr(admin, "_hh_get", fake_hh)
    response = client.get("/api/live-summary?profile=account-a")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "hh_api"
    assert body["profile"] == "account-a"
    assert body["negotiations"] == 97
    assert body["resumes"] == 2
    assert body["resume_views"][0]["total_views"] == 88
    assert calls == [
        ("account-a", "/negotiations"),
        ("account-a", "/resumes/mine"),
    ]
    assert client.get("/api/live-summary?profile=..").status_code == 400


def test_dashboard_account_funnel_and_date_range(
    client: TestClient, tmp_path: Path
) -> None:
    for profile in ("account-a", "account-b"):
        assert client.post("/api/profiles", json={"profile": profile}).status_code == 200

    for profile, rows in (
        ("account-a", [("invitation", "2026-11-16"), ("discard", "2026-11-17")]),
        ("account-b", [("interview", "2026-11-17"), ("response", "2026-11-18")]),
    ):
        with sqlite3.connect(tmp_path / profile / "data") as conn:
            for index, (state, created) in enumerate(rows):
                conn.execute(
                    """INSERT INTO negotiations
                    (id, state, vacancy_id, chat_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (index + 1, state, index + 10, index + 20, created, created),
                )

    response = client.get(
        "/api/stats",
        params={"profile": "account-a", "scope": "all", "days": 30},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "local_sqlite"
    assert data["totals"]["negotiations"] == 4
    assert len(data["daily_applications"]) == 30
    assert len(data["daily_applications_by_profile"]) == 60
    by_profile = {row["profile"]: row for row in data["profile_totals"]}
    assert by_profile["account-a"]["states"]["invitation"] == 1
    assert by_profile["account-a"]["progress_rate"] == 50
    assert by_profile["account-b"]["states"]["interview"] == 1
    assert by_profile["account-b"]["progress_rate"] == 50
    assert data["freshness_note"].startswith("SQLite")
    assert client.get("/api/stats?days=365").status_code == 422


def test_safe_admin_jobs_route_to_real_linux_workers_and_confirm_live(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.post(
        "/api/run/safe-reply",
        json={"profile": "account-a", "dry_run": False},
    ).status_code == 409
    assert client.post(
        "/api/run/safe-apply",
        json={"profile": "account-a", "dry_run": False},
    ).status_code == 409

    captured: list[tuple[str, list[str]]] = []

    def fake_run(operation: str, body, extra=None) -> dict:
        captured.append((operation, list(extra or [])))
        return {"op_id": "safe-test-job"}

    monkeypatch.setattr(admin, "_run_operation", fake_run)
    assert client.post(
        "/api/run/safe-reply",
        json={"profile": "account-a", "dry_run": True, "max_chats": 17, "rounds": 2},
    ).status_code == 200
    assert client.post(
        "/api/run/safe-apply",
        json={"profile": "account-a", "dry_run": True, "limit": 5, "pages": 4},
    ).status_code == 200
    assert captured[0][0] == "safe-reply"
    assert captured[0][1] == ["--max-chats", "17", "--rounds", "2", "--dry-run"]
    assert captured[1][0] == "safe-apply"
    assert "--limit" in captured[1][1]
    assert "--dry-run" in captured[1][1]


def test_account_contact_is_editable_without_destroying_token(
    client: TestClient, tmp_path: Path
) -> None:
    client.post("/api/profiles", json={"profile": "account-a"})
    path = tmp_path / "account-a" / "config.json"
    path.write_text(json.dumps({"token": {"access_token": "secret-token"}}))
    response = client.put(
        "/api/config?profile=account-a",
        json={"data": {"contact": {"telegram": "@demo_recruiting"}}},
    )
    assert response.status_code == 200
    config = json.loads(path.read_text())
    assert config["token"]["access_token"] == "secret-token"
    assert config["contact"]["telegram"] == "@demo_recruiting"
    assert "secret-token" not in client.get("/api/config?profile=account-a").text


def test_linux_admin_assets_and_entrypoints_are_in_sync() -> None:
    index = (ROOT / "admin" / "index.html").read_text(encoding="utf-8")
    assert 'data-page="automation"' in index
    assert "/api/automation-settings" in index
    assert "/api/live-summary" in index
    assert "/api/run/safe-reply" in index
    assert "/api/run/safe-apply" in index
    assert "dailyTrendChart" in index
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'ADMIN_REQUIRE_AUTH: "1"' in compose
    assert "ADMIN_PASSWORD" in compose
    assert "admin_automation.env" in (
        ROOT / "scripts" / "cron-job.sh"
    ).read_text(encoding="utf-8")
    for name in ("hh.bat", "daily.bat", "admin.bat", "open-browsers.bat",
                 "run_tests.bat", "run_tests_coverage.bat"):
        assert not (ROOT / name).exists()
    assert not (ROOT / "scripts" / "setup-windows.ps1").exists()


def test_admin_inline_javascript_parses_if_node_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed on this environment")
    source = (ROOT / "admin" / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script>([\s\S]*?)</script>", source)
    assert len(scripts) == 1
    result = subprocess.run(
        [node, "--check"], input=scripts[0], text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
