#!/usr/bin/env bash
# cleanup.sh — remove rejected negotiations; optionally hide their HH web chats.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.env"
    set +a
fi

RUN_MODE="dry-run"
RUN_MODE_EXPLICIT=""
PROFILE_ID="${HH_PROFILE_ID:-}"
DELETE_CHAT="${CLEANUP_DELETE_CHAT:-1}"

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
        --profile)
            [[ $# -ge 2 ]] || { echo "--profile requires a value" >&2; exit 2; }
            PROFILE_ID="$2"
            shift 2
            ;;
        --delete-chat)
            DELETE_CHAT=1
            shift
            ;;
        --keep-chat)
            DELETE_CHAT=0
            shift
            ;;
        -h|--help)
            cat <<'EOF'
Usage: cleanup.sh [--dry-run|--live] [--profile ID] [--delete-chat|--keep-chat]

Removes only negotiations already in HH state=discard. It does not blacklist
employers and does not remove old active negotiations. By default rejected web
chats are hidden too (CLEANUP_DELETE_CHAT=1).
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

if [[ "$DELETE_CHAT" != "0" && "$DELETE_CHAT" != "1" ]]; then
    echo "CLEANUP_DELETE_CHAT must be 0 or 1" >&2
    exit 2
fi

cmd=(hh-applicant-tool --no-auto-auth)
if [[ -n "$PROFILE_ID" ]]; then
    cmd+=(--profile-id "$PROFILE_ID")
fi
cmd+=(clear-negotiations)

if [[ "$DELETE_CHAT" == "1" ]]; then
    cmd+=(--delete-chat)
fi
if [[ "$RUN_MODE" != "live" ]]; then
    cmd+=(--dry-run)
fi

exec "${cmd[@]}"
