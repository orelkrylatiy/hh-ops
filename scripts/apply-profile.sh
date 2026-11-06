#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
    if [[ -f "$PROJECT_ROOT/.venv/Scripts/python.exe" ]]; then
        PYTHON_BIN="$PROJECT_ROOT/.venv/Scripts/python.exe"
    elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
        PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
    else
        PYTHON_BIN=python3
    fi
fi
PROFILE_SCRIPT="$SCRIPT_DIR/apply_profile.py"
if [[ "$PYTHON_BIN" == *.exe ]] && command -v wslpath >/dev/null 2>&1; then
    PROFILE_SCRIPT="$(wslpath -w "$PROFILE_SCRIPT")"
fi
export PYTHONUTF8=1
exec "$PYTHON_BIN" "$PROFILE_SCRIPT" "$@"
