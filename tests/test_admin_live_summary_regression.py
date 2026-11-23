"""Read-only admin live summary regression tests.

No HeadHunter requests or external sends: mock the HH API boundary.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import admin.app as admin


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ADMIN_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    return TestClient(admin.app)


def test_live_summary_respects_nested_hh_resume_counters(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_hh(profile: str, path: str, params: dict) -> dict:
        calls.append((profile, path))
        if path == "/negotiations":
            return {"found": 3, "items": []}
        if path == "/resumes/mine":
            return {
                "found": 4,
                "items": [
                    {"id": "nested", "title": "Nested", "counters": {
                        "total_views": 45, "new_views": 7,
                    }},
                    {"id": "old", "title": "Legacy", "total_views": 12, "new_views": 2},
                    {"id": "boxed", "title": "Boxed", "total_views": {"total": 21},
                     "new_views": {"total": 3}},
                    {"id": "missing", "title": "Missing"},
                ],
            }
        raise AssertionError(path)

    monkeypatch.setattr(admin, "_hh_get", fake_hh)
    response = client.get("/api/live-summary?profile=account-b")

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "hh_api"
    assert data["profile"] == "account-b"
    assert data["resumes"] == 4
    by_id = {item["id"]: item for item in data["resume_views"]}
    assert (by_id["nested"]["total_views"], by_id["nested"]["new_views"]) == (45, 7)
    assert (by_id["old"]["total_views"], by_id["old"]["new_views"]) == (12, 2)
    assert (by_id["boxed"]["total_views"], by_id["boxed"]["new_views"]) == (21, 3)
    assert by_id["missing"]["total_views"] is None
    assert by_id["missing"]["new_views"] is None
    assert calls == [("account-b", "/negotiations"), ("account-b", "/resumes/mine")]


def test_live_summary_invalid_counters_do_not_appear_as_real_zeros() -> None:
    assert admin._resume_view_counter({"counters": {"total_views": 0}}, "total_views") == 0
    assert admin._resume_view_counter({"counters": {"total_views": -2}}, "total_views") is None
    assert admin._resume_view_counter({"counters": {"total_views": "bad"}}, "total_views") is None
    assert admin._resume_view_counter({}, "total_views") is None


def test_live_summary_ui_preserves_explicit_profile() -> None:
    """Exercise the real inline API wrapper, when Node.js is available."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not available")

    html = (ROOT / "admin" / "index.html").read_text(encoding="utf-8")
    source = "async function api(path, opts = {}) {" + html.split(
        "async function api(path, opts = {}) {", 1
    )[1].split("\nfunction toast(", 1)[0]

    js = """
const assert = require('node:assert/strict');
const wrapper = new Function('fetch', 'window', 'currentProfile', 'viewScope',
                            %s + '\\nreturn api;');
const seen = [];
const mockFetch = async (url) => {
  seen.push(url);
  return {ok:true, json:async()=>({ok:true})};
};
const api = wrapper(mockFetch, {location:{origin:'https://admin.example'}}, 'account-a', 'all');
Promise.all([
  api('/api/live-summary?profile=account-b'),
  api('/api/resumes')
]).then(() => {
  const first = new URL(seen[0], 'https://admin.example');
  const second = new URL(seen[1], 'https://admin.example');
  assert.deepEqual(first.searchParams.getAll('profile'), ['account-b']);
  assert.deepEqual(second.searchParams.getAll('profile'), ['account-a']);
  assert.equal(first.searchParams.get('scope'), 'all');
  assert.equal(second.searchParams.get('scope'), 'all');
}).catch((error)=>{console.error(error);process.exitCode=1;});
""" % json.dumps(source)
    result = subprocess.run([node, "-e", js], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
