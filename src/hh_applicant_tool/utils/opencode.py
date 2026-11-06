"""Small process launcher for handing an operator prompt to OpenCode."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from ..ai.openai import _normalize_captcha_text
from ..constants import (
    OPENCODE_CAPTCHA_MODEL,
    OPENCODE_COMMAND,
    OPENCODE_FALLBACK_MODEL,
    OPENCODE_PRIMARY_MODEL,
)

logger = logging.getLogger(__name__)

_OPENCODE_DIAGNOSTIC_LIMIT = 4000


def _redact_opencode_diagnostic(value: str) -> str:
    """Keep child-process diagnostics useful without leaking access URLs."""
    value = re.sub(
        r"(?i)(https?://[^\s]+(?:state|token|code|key)=[^\s]+)",
        "<redacted-url>",
        value,
    )
    if len(value) > _OPENCODE_DIAGNOSTIC_LIMIT:
        return value[:_OPENCODE_DIAGNOSTIC_LIMIT] + "…<truncated>"
    return value


def _captcha_ocr_variants(image_path: Path) -> list[Path]:
    """Create enlarged rotations so vision OCR can inspect curved HH text."""
    try:
        with Image.open(image_path) as source:
            base = ImageOps.autocontrast(source.convert("L"))
            base = ImageEnhance.Contrast(base).enhance(1.35)
            base = base.filter(ImageFilter.SHARPEN)
            paths: list[Path] = []
            for angle in (0, 90, 180, 270):
                rotated = base.rotate(angle, expand=True, fillcolor=255)
                enlarged = rotated.resize(
                    (rotated.width * 3, rotated.height * 3),
                    Image.Resampling.LANCZOS,
                )
                path = image_path.with_name(f"{image_path.stem}.ocr-{angle}.png")
                enlarged.save(path)
                paths.append(path)
            return paths
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not prepare captcha OCR variants: %s", exc)
        return [image_path]


def spawn_opencode(
    prompt: str,
    *,
    models: Sequence[str] = (OPENCODE_PRIMARY_MODEL, OPENCODE_FALLBACK_MODEL),
    cwd: str | None = None,
    files: Sequence[str | Path] = (),
    standalone: bool = False,
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
    child_env = os.environ.copy()
    # apply.sh sources the HH account proxy from .env.  Passing that proxy to
    # the Windows OpenCode child makes its subscription/OAuth requests travel
    # through the HH/WSL proxy and hang, while the same command works from a
    # clean PowerShell.  OpenCode owns its transport; keep the HH proxy scoped
    # to requests/Playwright.
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        child_env.pop(key, None)
    for model in models:
        try:
            args = [executable, "run"]
            if standalone:
                args.append("--standalone")
            args.extend(["--model", model])
            for file_path in files:
                args.extend(["--file", str(Path(file_path).resolve())])
            args.append(prompt)
            return subprocess.Popen(
                args,
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
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
    standalone: bool = False,
) -> str:
    """Run OpenCode and return its textual answer for the caller to consume."""
    started_at = time.monotonic()
    logger.info(
        "OpenCode start: models=%s standalone=%s files=%d timeout=%ss",
        ",".join(models),
        standalone,
        len(files),
        timeout,
    )
    process = spawn_opencode(
        prompt,
        models=models,
        cwd=cwd,
        files=files,
        standalone=standalone,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            process.kill()
        stdout, stderr = process.communicate()
        logger.error(
            "OpenCode timeout: models=%s elapsed=%.1fs timeout=%ss stdout=%r stderr=%r",
            ",".join(models),
            time.monotonic() - started_at,
            timeout,
            _redact_opencode_diagnostic(stdout or ""),
            _redact_opencode_diagnostic(stderr or ""),
        )
        raise TimeoutError(f"OpenCode timed out after {timeout:g} seconds") from None
    if process.returncode:
        detail = _redact_opencode_diagnostic((stderr or stdout).strip())
        logger.error(
            "OpenCode exit failure: models=%s returncode=%s elapsed=%.1fs "
            "stdout=%r stderr=%r",
            ",".join(models),
            process.returncode,
            time.monotonic() - started_at,
            _redact_opencode_diagnostic(stdout or ""),
            _redact_opencode_diagnostic(stderr or ""),
        )
        raise RuntimeError(f"OpenCode exited with code {process.returncode}: {detail}")
    logger.info(
        "OpenCode success: models=%s elapsed=%.1fs stdout_chars=%d",
        ",".join(models),
        time.monotonic() - started_at,
        len(stdout or ""),
    )
    return stdout.strip()


def solve_captcha_with_opencode(current_json: Path) -> str:
    """Ask OpenCode to inspect the image referenced by CURRENT.json."""
    metadata = json.loads(current_json.read_text(encoding="utf-8"))
    image_path = Path(metadata["image"])
    image_variants = _captcha_ocr_variants(image_path)
    prompt = (
        "Распознай фото на прикреплённых вариантах одного изображения, повёрнутых "
        "на 0, 90, 180 и 270 градусов. На ней два искажённых русских "
        "слова; отдельный символ может выглядеть как латинская M. Верни лучший "
        "возможный вариант ровно из двух слов через пробел, без пояснений. "
        "Перепиши все видимые буквы буквально: не исправляй грамматику, форму "
        "слова или смысл. Все буквы в нижнем регистре."
    )
    # A live HH pass must not spend several minutes retrying one image.  In
    # practice the model answers in ~10 seconds; longer waits are usually a
    # stuck OpenCode child/server and used to turn every live run into an
    # empty captcha result.
    # Standalone starts a private OpenCode server.  A warm direct call usually
    # finishes in ~13s, but Playwright/Edge load can push the cold start past
    # 20s; allow enough startup time without falling back to the shared server.
    timeout = float(os.environ.get("HH_CAPTCHA_OPENCODE_TIMEOUT", "60"))
    # CAPTCHA uses only the explicitly selected vision model. A timeout must
    # not silently repeat that model via the general text fallback setting.
    model = OPENCODE_CAPTCHA_MODEL
    try:
        answer = run_opencode(
            prompt,
            models=(model,),
            cwd=str(current_json.parent.parent.parent),
            files=image_variants,
            timeout=timeout,
            standalone=True,
        )
    except Exception as exc:
        logger.warning("OpenCode captcha model failed: model=%s error=%s", model, exc)
        return ""
    normalized = _normalize_captcha_text(answer)
    # This path asks for two Russian words, not an arbitrary token from prose.
    if not re.fullmatch(r"[А-Яа-яЁёA-Za-z-]+ [А-Яа-яЁёA-Za-z-]+", normalized) or not re.search(
        r"[А-Яа-яЁё]", normalized
    ):
        logger.warning("OpenCode captcha response rejected: model=%s reason=invalid_ocr_format", model)
        return ""
    logger.info("OpenCode captcha model returned: model=%s chars=%d", model, len(normalized))
    return normalized
