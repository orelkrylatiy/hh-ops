#!/usr/bin/env bash
# Admin-managed scheduled HH cycle: boost, bounded applications, then replies.
set -uo pipefail
export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

load_env_file() {
    local file="$1"
    if [[ -f "$file" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$file"
        set +a
    fi
}

load_env_file "$PROJECT_ROOT/.env"
load_env_file "${CONFIG_DIR:-$PROJECT_ROOT/config}/admin_automation.env"
export HH_ADMIN_AUTOMATION_OVERRIDE=1
export PROFILES="0056 0555 1206 1558 5457 8755"
export HH_PROFILE_PARALLELISM=3

MODE="${HH_AUTOMATION_MODE:-off}"
case "$MODE" in
    off)
        echo "$(date -Is) HH-ops cycle disabled by admin setting"
        exit 0
        ;;
    dry-run)
        APPLY_MODE="--dry-run"
        REPLY_MODE="--dry-run"
        ;;
    live)
        APPLY_MODE="--live"
        REPLY_MODE="--live"
        ;;
    *)
        echo "Invalid HH_AUTOMATION_MODE='$MODE'" >&2
        exit 2
        ;;
esac

run_stage() {
    local stage="$1"
    local status=0
    shift
    "$@" || status=$?
    if (( status == 0 )); then
        echo "HH_CYCLE_STAGE stage=$stage status=0"
        return 0
    fi
    echo "HH_CYCLE_STAGE stage=$stage status=$status" >&2
    return "$status"
}

FAILED=0
if [[ "$MODE" == "live" ]]; then
    run_stage boost /bin/bash "$SCRIPT_DIR/all-profiles.sh" boost --live || FAILED=1
else
    echo "$(date -Is) dry-run: boost skipped"
fi

run_stage apply /bin/bash "$SCRIPT_DIR/all-profiles.sh" apply "$APPLY_MODE" \
    --search "${SEARCH_QUERY:-Frontend разработчик}" \
    --limit "${APPLY_LIMIT:-10}" \
    --pages "${APPLY_PAGES:-5}" \
    --per-page "${APPLY_PER_PAGE:-50}" || FAILED=1

run_stage reply /bin/bash "$SCRIPT_DIR/all-profiles.sh" reply "$REPLY_MODE" \
    --chats "${REPLY_CHATS:-100}" --rounds 1 || FAILED=1

if (( FAILED )); then
    echo "HH_CYCLE_RESULT status=failed" >&2
    exit 1
fi
echo "HH_CYCLE_RESULT status=ok"
