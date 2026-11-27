#!/usr/bin/env python3
"""Route OAuth-backed OpenCode calls through the user's authenticated service.

Humanizer Framework adds ``--standalone`` to every OpenCode CLI invocation.
On this VPS, the OpenAI OAuth credential is available through the regular
OpenCode service, while a standalone process has no usable route for it.
"""

from __future__ import annotations

import os
import shutil
import sys


def main() -> None:
    args = [arg for arg in sys.argv[1:] if arg != "--standalone"]
    # The repo's OpenAI API key is for the API-backed fallback, and it has no
    # credits. For this OAuth-backed model, let OpenCode use its saved account.
    os.environ.pop("OPENAI_API_KEY", None)

    # The VPS OpenAI OAuth session belongs to the root managed OpenCode
    # service. Use that credential store explicitly instead of starting an
    # ubuntu service that can collide with root's listener and has no OAuth.
    root_command = os.environ.get("HUMANIZER_OPENCODE_ROOT_COMMAND", "").strip()
    if root_command:
        sudo = shutil.which("sudo")
        if sudo is None:
            raise SystemExit("sudo is required for the configured OpenCode OAuth service")
        os.execvpe(sudo, [sudo, "-n", root_command, *args], os.environ)

    command = os.environ.get("OPENCODE_COMMAND", "opencode").strip() or "opencode"
    executable = shutil.which(command)
    if executable is None:
        raise SystemExit(f"OpenCode executable not found: {command}")
    os.execv(executable, [executable, *args])


if __name__ == "__main__":
    main()
