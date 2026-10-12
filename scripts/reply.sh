#!/usr/bin/env bash
# reply.sh — safe scheduled replies for HH chats.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.env"
    set +a
fi

MAX_CHATS="${REPLY_CHATS:-${CHATS:-100}}"
REPLY_PROMPT_TEMPLATE="${REPLY_PROMPT_TEMPLATE:-$PROJECT_ROOT/prompts/reply_employer.txt}"
RUN_MODE="dry-run"
RUN_MODE_EXPLICIT=""
PROFILE_ID="${HH_PROFILE_ID:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            [[ "$RUN_MODE_EXPLICIT" == "live" ]] && { echo "Cannot combine --dry-run and --live" >&2; exit 1; }
            RUN_MODE="dry-run"
            RUN_MODE_EXPLICIT="dry-run"
            shift
            ;;
        --live)
            [[ "$RUN_MODE_EXPLICIT" == "dry-run" ]] && { echo "Cannot combine --dry-run and --live" >&2; exit 1; }
            RUN_MODE="live"
            RUN_MODE_EXPLICIT="live"
            shift
            ;;
        --chats|--max-chats)
            [[ $# -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
            MAX_CHATS="$2"
            shift 2
            ;;
        --iterations)
            [[ $# -ge 2 ]] || { echo "--iterations requires a value" >&2; exit 2; }
            echo "Warning: --iterations is deprecated and ignored; use hourly cron instead." >&2
            shift 2
            ;;
        --profile)
            [[ $# -ge 2 ]] || { echo "--profile requires a value" >&2; exit 2; }
            PROFILE_ID="$2"
            export HH_PROFILE_ID="$2"
            shift 2
            ;;
        --telegram)
            [[ $# -ge 2 ]] || { echo "--telegram requires a value" >&2; exit 2; }
            export HH_TELEGRAM="$2"
            shift 2
            ;;
        -h|--help)
            cat <<'EOF'
Usage: reply.sh [--dry-run|--live] [--chats N] [--profile ID]

  --dry-run       Inspect chats and print deterministic previews; never calls AI or sends.
  --live          Generate replies with AI and send them through /negotiations/{id}/messages.
  --chats N       Maximum candidate chats for this cron run (default: 100).
  --profile ID    HH profile id.
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

if [[ ! "$MAX_CHATS" =~ ^[1-9][0-9]*$ ]]; then
    echo "--chats must be a positive integer: $MAX_CHATS" >&2
    exit 2
fi

if [[ ! -f "$REPLY_PROMPT_TEMPLATE" ]]; then
    echo "Reply prompt not found: $REPLY_PROMPT_TEMPLATE" >&2
    exit 1
fi
# envsubst.exe портит кириллицу в переменных окружения (Максим -> ??????),
# поэтому рендерим и запускаемся через venv-python: scripts/render_prompt.py.
if [[ -z "${PYTHON_BIN:-}" ]]; then
    if [[ -f "$PROJECT_ROOT/.venv/Scripts/python.exe" ]]; then
        PYTHON_BIN="$PROJECT_ROOT/.venv/Scripts/python.exe"
    elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
        PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Python not found: $PYTHON_BIN" >&2
    exit 1
fi

REPLY_SYSTEM_PROMPT_FILE="$(mktemp "${TMPDIR:-/tmp}/hh-reply-prompt.XXXXXX")"
cleanup() {
    rm -f "$REPLY_SYSTEM_PROMPT_FILE"
}
trap cleanup EXIT

"$PYTHON_BIN" "$SCRIPT_DIR/render_prompt.py" "$REPLY_PROMPT_TEMPLATE" "$REPLY_SYSTEM_PROMPT_FILE"
export REPLY_SYSTEM_PROMPT_FILE

if [[ "$RUN_MODE" == "live" ]]; then
    CHECK_ARGS=(--purpose reply)
    if [[ -n "$PROFILE_ID" ]]; then
        CHECK_ARGS+=(--profile "$PROFILE_ID")
    fi
    "$PYTHON_BIN" "$SCRIPT_DIR/check_ai.py" "${CHECK_ARGS[@]}"
fi

ARGS=(--max-chats "$MAX_CHATS")
if [[ "$RUN_MODE" == "live" ]]; then
    ARGS+=(--live)
else
    ARGS+=(--dry-run)
fi
if [[ -n "$PROFILE_ID" ]]; then
    ARGS+=(--profile "$PROFILE_ID")
fi

"$PYTHON_BIN" "$SCRIPT_DIR/reply_iterative_ai.py" "${ARGS[@]}"
