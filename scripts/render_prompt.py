#!/usr/bin/env python3
"""Render a prompt template with ${VAR} substitution, UTF-8-safe on Windows.

envsubst.exe receives environment values through the ANSI codepage and
mangles Cyrillic (Максим -> ??????). This helper substitutes variables
from .env and the environment directly, keeping UTF-8 end to end.

Usage: render_prompt.py TEMPLATE OUTPUT [VAR=VALUE ...]
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_env_file(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = root / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: render_prompt.py TEMPLATE OUTPUT [VAR=VALUE ...]", file=sys.stderr)
        return 2
    template_path = Path(argv[0])
    output_path = Path(argv[1])
    overrides = dict(arg.split("=", 1) for arg in argv[2:])

    values = load_env_file(template_path.resolve().parents[1])
    values.update(os.environ)
    values.update(overrides)

    def substitute(match: re.Match[str]) -> str:
        return values.get(match.group(1), match.group(0))

    rendered = PLACEHOLDER.sub(substitute, template_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
