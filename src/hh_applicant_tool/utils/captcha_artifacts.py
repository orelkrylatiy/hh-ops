"""Persist CAPTCHA images for manual inspection and troubleshooting."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from ..constants import CAPTCHA_ARTIFACTS_DIR


def save_captcha_image(
    image_bytes: bytes,
    *,
    flow: str,
    attempt: int,
    profile_id: str = "default",
    root: Path | None = None,
) -> Path:
    """Save an image and update a stable pointer to the current challenge.

    ``CURRENT.json`` in the root always points to the most recently captured
    image, while the image itself retains its flow and attempt number.
    """
    base = Path(root or CAPTCHA_ARTIFACTS_DIR)
    safe_profile = re.sub(r"[^a-zA-Z0-9_-]+", "_", profile_id).strip("_") or "default"
    safe_flow = re.sub(r"[^a-zA-Z0-9_-]+", "_", flow).strip("_") or "captcha"
    run_dir = base / safe_profile / safe_flow
    run_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    image_path = run_dir / f"captcha_{safe_flow}_{stamp}_attempt_{attempt:02d}.png"
    image_path.write_bytes(image_bytes)
    metadata = {
        "flow": flow,
        "profile_id": profile_id,
        "attempt": attempt,
        "image": str(image_path.resolve()),
        "captured_at": datetime.now(UTC).isoformat(),
    }
    (run_dir / "CURRENT.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base.mkdir(parents=True, exist_ok=True)
    (base / "CURRENT.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return image_path
