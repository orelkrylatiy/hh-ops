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


@pytest.fixture
def daily_harness(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(repo_root / "scripts" / "daily.sh", scripts / "daily.sh")
    for stage, filename in (
        ("apply", "apply.sh"),
        ("reply", "reply.sh"),
        ("boost", "all-profiles.sh"),
    ):
        script = scripts / filename
        script.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "{stage}:%s\\n" "$*" >> "$HH_DAILY_TEST_LOG"\n'
            f'exit "${{HH_DAILY_TEST_{stage.upper()}_EXIT:-0}}"\n',
            encoding="utf-8",
        )
        script.chmod(0o755)
    return scripts / "daily.sh", tmp_path / "stages.log"


def _run(daily: Path, log: Path, *args: str, apply: int = 0, reply: int = 0, boost: int = 0):
    env = os.environ.copy()
    env.pop("HH_PROFILE_ID", None)
    env["HH_DAILY_TEST_LOG"] = str(log)
    env["HH_DAILY_TEST_APPLY_EXIT"] = str(apply)
    env["HH_DAILY_TEST_REPLY_EXIT"] = str(reply)
    env["HH_DAILY_TEST_BOOST_EXIT"] = str(boost)
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


def test_apply_failure_still_runs_chat_replies(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", apply=7)
    assert [line.split(":", 1)[0] for line in called] == ["apply", "reply"]
    assert result.returncode == 1
    assert "stage=apply status=7" in result.stderr
    assert "stage=reply status=0" in result.stdout
    assert "HH_DAILY_RESULT status=failed" in result.stderr


def test_reply_failure_is_reported_without_hiding_completed_apply(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", reply=9)
    assert [line.split(":", 1)[0] for line in called] == ["apply", "reply"]
    assert result.returncode == 1
    assert "stage=apply status=0" in result.stdout
    assert "stage=reply status=9" in result.stderr


def test_both_stages_succeed(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", "--profile", "test-account")
    assert [line.split(":", 1)[0] for line in called] == ["apply", "reply"]
    assert all("--profile test-account" in line for line in called)
    assert result.returncode == 0
    assert "HH_DAILY_RESULT status=ok" in result.stdout


def test_single_stage_flags_are_preserved(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", "--apply-only")
    assert result.returncode == 0
    assert [line.split(":", 1)[0] for line in called] == ["apply"]
    log.unlink()
    result, called = _run(daily, log, "--dry-run", "--reply-only")
    assert result.returncode == 0
    assert [line.split(":", 1)[0] for line in called] == ["reply"]


def test_failed_boost_does_not_hide_independent_stages(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--live", "--with-boost", boost=4)
    assert [line.split(":", 1)[0] for line in called] == ["boost", "apply", "reply"]
    assert result.returncode == 1
    assert "stage=boost status=4" in result.stderr


def test_boost_is_still_forbidden_in_dry_run(daily_harness):
    daily, log = daily_harness
    result, called = _run(daily, log, "--dry-run", "--with-boost")
    assert result.returncode == 2
    assert not called
