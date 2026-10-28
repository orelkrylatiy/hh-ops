"""Offline regression tests for the daily orchestration shell entrypoint.

The fake stage scripts record calls and exit with controlled codes; no HH, AI
provider, boost, email or CAPTCHA operations are invoked.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


STUB_BODY = (
    "#!/usr/bin/env bash\n"
    'printf "{stage}:PROFILES=${{PROFILES-}}:%s\\n" "$*" >> "$HH_DAILY_TEST_LOG"\n'
    'exit "${{HH_DAILY_TEST_{stage_upper}_EXIT:-0}}"\n'
)


@pytest.fixture
def daily_harness(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(repo_root / "scripts" / "daily.sh", scripts / "daily.sh")
    for stage, filename in (
        ("apply", "apply.sh"),
        ("reply", "reply.sh"),
        ("fleet", "all-profiles.sh"),
    ):
        script = scripts / filename
        script.write_text(
            STUB_BODY.format(stage=stage, stage_upper=stage.upper()),
            encoding="utf-8",
        )
        script.chmod(0o755)
    return scripts / "daily.sh", tmp_path / "stages.log"


def _run(daily: Path, log: Path, *args: str, apply: int = 0, reply: int = 0, fleet: int = 0):
    env = os.environ.copy()
    env.pop("HH_PROFILE_ID", None)
    env.pop("PROFILES", None)
    env["HH_DAILY_TEST_LOG"] = str(log)
    env["HH_DAILY_TEST_APPLY_EXIT"] = str(apply)
    env["HH_DAILY_TEST_REPLY_EXIT"] = str(reply)
    env["HH_DAILY_TEST_FLEET_EXIT"] = str(fleet)
    result = subprocess.run(
        ["bash", str(daily), *args],
        cwd=daily.parent.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    called = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, called


def _stages(called: list[str]) -> list[str]:
    return [line.split(":", 1)[0] for line in called]


def test_apply_failure_still_runs_chat_replies(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", apply=7)
    assert _stages(called) == ["apply", "reply"]
    assert result.returncode == 1
    assert "stage=apply status=7" in result.stderr
    assert "stage=reply status=0" in result.stdout
    assert "HH_DAILY_RESULT status=failed" in result.stderr


def test_reply_failure_is_reported_without_hiding_completed_apply(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", reply=9)
    assert _stages(called) == ["apply", "reply"]
    assert result.returncode == 1
    assert "stage=apply status=0" in result.stdout
    assert "stage=reply status=9" in result.stderr


def test_both_stages_succeed(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", "--profile", "test-account")
    assert _stages(called) == ["apply", "reply"]
    assert all("--profile test-account" in line for line in called)
    assert result.returncode == 0
    assert "HH_DAILY_RESULT status=ok" in result.stdout


def test_single_stage_flags_are_preserved(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", "--apply-only")
    assert result.returncode == 0
    assert _stages(called) == ["apply"]
    log.unlink()
    result, called = _run(daily, log, "--dry-run", "--reply-only")
    assert result.returncode == 0
    assert _stages(called) == ["reply"]


def test_failed_boost_does_not_hide_independent_stages(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--live", "--with-boost", fleet=4)
    assert _stages(called) == ["fleet", "apply", "reply"]
    assert result.returncode == 1
    assert "stage=boost status=4" in result.stderr


def test_boost_is_still_forbidden_in_dry_run(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", "--with-boost")
    assert result.returncode == 2
    assert not called


def test_multiple_profiles_run_fleet_stages_with_profiles_env(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--live", "--with-boost", "--profiles", "acc-a", "acc-b")
    assert _stages(called) == ["fleet", "fleet", "fleet"]
    assert all("PROFILES=acc-a acc-b" in line for line in called)
    assert called[0].endswith(":boost --live")
    assert "--search " in called[1]
    assert result.returncode == 0
    assert "profiles=acc-a acc-b" in result.stdout


def test_comma_separated_profiles_are_split(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", "--profiles", "acc-a,acc-b")
    assert _stages(called) == ["fleet", "fleet"]
    assert all("PROFILES=acc-a acc-b" in line for line in called)
    assert result.returncode == 0


def test_single_profile_from_list_stays_direct(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", "--profiles", "acc-a")
    assert _stages(called) == ["apply", "reply"]
    assert all("--profile acc-a" in line for line in called)
    assert result.returncode == 0


def test_unknown_profile_fails_closed(daily_harness, tmp_path):
    daily, log = daily_harness
    (daily.parent.parent / ".profiles").write_text("acc-a\nacc-b\n", encoding="utf-8")
    try:
        result, called = _run(daily, log, "--dry-run", "--profiles", "nope")
    finally:
        (daily.parent.parent / ".profiles").unlink()
    assert result.returncode == 2
    assert "Unknown profile: nope" in result.stderr
    assert "acc-a" in result.stderr
    assert not called


def test_invalid_profile_id_fails_closed(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", "--profiles", "bad/id")
    assert result.returncode == 2
    assert "Invalid profile id" in result.stderr
    assert not called
