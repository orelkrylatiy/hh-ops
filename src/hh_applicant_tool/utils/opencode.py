"""Small process launcher for handing an operator prompt to OpenCode."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from ..constants import (
    OPENCODE_COMMAND,
    OPENCODE_FALLBACK_MODEL,
    OPENCODE_PRIMARY_MODEL,
)


def spawn_opencode(
    prompt: str,
    *,
    models: Sequence[str] = (OPENCODE_PRIMARY_MODEL, OPENCODE_FALLBACK_MODEL),
    cwd: str | None = None,
    files: Sequence[str | Path] = (),
) -> subprocess.Popen[str]:
    """Start OpenCode with ``prompt`` and return without blocking the caller.

    The first model is tried first; fallback is used when the executable
    rejects that model during process start.  The executable can be overridden
    with ``OPENCODE_COMMAND`` for installations using a custom command.
    """
    if not prompt.strip():
        raise ValueError("OpenCode prompt must not be empty")
    command = os.environ.get("OPENCODE_COMMAND", OPENCODE_COMMAND)
    executable = shutil.which(command) or shutil.which(f"{command}.cmd")
    if executable is None:
        raise FileNotFoundError(f"OpenCode executable not found: {command}")

    last_error: OSError | None = None
    for model in models:
        try:
            args = [executable, "run", "--model", model]
            for file_path in files:
                args.extend(["--file", str(Path(file_path).resolve())])
            args.append(prompt)
            return subprocess.Popen(
                args,
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            last_error = exc
    raise last_error or RuntimeError("Unable to start OpenCode")


def run_opencode(
    prompt: str,
    *,
    models: Sequence[str] = (OPENCODE_PRIMARY_MODEL, OPENCODE_FALLBACK_MODEL),
    cwd: str | None = None,
    timeout: float = 300,
    files: Sequence[str | Path] = (),
) -> str:
    """Run OpenCode and return its textual answer for the caller to consume."""
    process = spawn_opencode(prompt, models=models, cwd=cwd, files=files)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError(f"OpenCode timed out after {timeout:g} seconds") from None
    if process.returncode:
        detail = (stderr or stdout).strip()
        raise RuntimeError(f"OpenCode exited with code {process.returncode}: {detail}")
    return stdout.strip()


def solve_captcha_with_opencode(current_json: Path) -> str:
    """Ask OpenCode to inspect the image referenced by CURRENT.json."""
    metadata = json.loads(current_json.read_text(encoding="utf-8"))
    image_path = Path(metadata["image"])
    prompt = (
        "Распознай прикреплённую картинку CAPTCHA. "
        "Верни только 4-8 латинских букв и цифр с сохранением регистра, "
        "без пробелов, кавычек и пояснений."
    )
    answer = run_opencode(
        prompt,
        cwd=str(current_json.parent.parent.parent),
        files=[image_path],
    )
    match = re.search(r"\b[A-Za-z0-9]{4,8}\b", answer)
    return match.group(0) if match else ""
