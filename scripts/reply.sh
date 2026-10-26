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
ROUNDS="${REPLY_ROUNDS:-1}"
ROUND_INTERVAL="${REPLY_ROUND_INTERVAL:-60}"
# .env may be CRLF-formatted when sourced from WSL; strip the carriage return
# before validating numeric CLI settings.
ROUNDS="${ROUNDS//$'\r'/}"
ROUND_INTERVAL="${ROUND_INTERVAL//$'\r'/}"
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
        --rounds)
            [[ $# -ge 2 ]] || { echo "--rounds requires 1..5" >&2; exit 2; }
            ROUNDS="$2"
            shift 2
            ;;
        --round-interval)
            [[ $# -ge 2 ]] || { echo "--round-interval requires seconds" >&2; exit 2; }
            ROUND_INTERVAL="$2"
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
Usage: reply.sh [--dry-run|--live] [--chats N] [--rounds 1..5] [--round-interval SECONDS] [--profile ID]

  --dry-run       Inspect chats and print deterministic previews; never calls AI or sends.
  --live          Generate replies with AI and send them through /negotiations/{id}/messages.
  --chats N       Maximum negotiations inspected per scan (default: 100).
  --rounds N      Rescan chats N times in this run (default: 1, maximum: 5).
  --round-interval SECONDS  Pause between scans (default: 60).
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
if [[ ! "$ROUNDS" =~ ^[1-5]$ || ! "$ROUND_INTERVAL" =~ ^[0-9]+$ ]]; then
    echo "--rounds must be 1..5 and --round-interval must be non-negative" >&2
    exit 2
fi

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

python_path() {
    if [[ "$PYTHON_BIN" == *.exe && -n "$(command -v wslpath 2>/dev/null || true)" ]]; then
        wslpath -w "$1"
    else
        printf '%s\n' "$1"
    fi
}

if [[ "$RUN_MODE" == "live" ]]; then
    "$PYTHON_BIN" "$(python_path "$SCRIPT_DIR/check_framework.py")" --purpose chat
fi

ARGS=(--max-chats "$MAX_CHATS" --rounds "$ROUNDS" --round-interval "$ROUND_INTERVAL")
if [[ "$RUN_MODE" == "live" ]]; then
    ARGS+=(--live)
else
    ARGS+=(--dry-run)
fi
if [[ -n "$PROFILE_ID" ]]; then
    ARGS+=(--profile "$PROFILE_ID")
fi

"$PYTHON_BIN" "$(python_path "$SCRIPT_DIR/reply_iterative_ai.py")" "${ARGS[@]}"
