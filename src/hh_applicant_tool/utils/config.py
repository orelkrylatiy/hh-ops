from __future__ import annotations

import platform
import re
from functools import cache
from os import getenv
from pathlib import Path
from threading import Lock
from typing import Any

from . import json


PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@cache
def get_config_path() -> Path:
    match platform.system():
        case "Windows":
            return Path(getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        case "Darwin":
            return Path.home() / "Library" / "Application Support"
        case _:
            return Path(getenv("XDG_CONFIG_HOME", Path.home() / ".config"))


def resolve_profile_config_dir(
    base_dir: str | Path,
    profile_id: str | None = None,
) -> Path:
    """Resolve a profile directory consistently for CLI and cron workers."""
    base_path = Path(base_dir)
    normalized_profile = profile_id.strip() if profile_id else None

    if normalized_profile and normalized_profile != ".":
        if not PROFILE_NAME_RE.fullmatch(normalized_profile):
            raise ValueError(
                "Invalid profile name. Use letters, numbers, dot, dash or underscore."
            )

    if normalized_profile and normalized_profile != "default":
        return (base_path / normalized_profile).resolve()
    if (base_path / "config.json").exists():
        return base_path.resolve()
    if normalized_profile == "default":
        return (base_path / "default").resolve()
    return base_path.resolve()


class Config(dict):
    def __init__(self, config_path: str | Path | None = None):
        self._config_path = Path(config_path or get_config_path())
        self._lock = Lock()
        self.load()

    def load(self) -> None:
        if self._config_path.exists():
            with self._lock:
                try:
                    with self._config_path.open(
                        "r", encoding="utf-8", errors="replace"
                    ) as f:
                        self.update(json.load(f))
                except json.JSONDecodeError:
                    if not self._is_recoverable_load_error():
                        raise

    def save(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self.update(*args, **kwargs)
            self._config_path.parent.mkdir(exist_ok=True, parents=True)
            try:
                with self._config_path.open(
                    "w", encoding="utf-8", errors="replace"
                ) as fp:
                    json.dump(
                        fp,
                        self,
                        indent=2,
                        sort_keys=True,
                    )
            except PermissionError:
                return

    __getitem__ = dict.get

    def __repr__(self) -> str:
        return str(self._config_path)

    def _is_recoverable_load_error(self) -> bool:
        try:
            raw = self._config_path.read_bytes()
        except OSError:
            return True
        stripped = raw.lstrip()
        if stripped.startswith((b"{", b"[")):
            return True
        if stripped.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
            return True
        return b"\xef\xbf\xbd" in stripped
