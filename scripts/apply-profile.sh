#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

PROFILE=""
ARGS=("$@")
for ((i = 0; i < ${#ARGS[@]}; i++)); do
    if [[ "${ARGS[$i]}" == "--profile" && $((i + 1)) -lt ${#ARGS[@]} ]]; then
        PROFILE="${ARGS[$((i + 1))]}"
        break
    fi
done

if [[ -n "$PROFILE" && "${HH_PROFILE_LOCK_HELD:-0}" != "1" ]]; then
    if ! command -v flock >/dev/null 2>&1; then
        echo "flock is required for crash-safe profile application locking" >&2
        exit 1
    fi
    PROFILE_LABEL="$PROFILE"
    [[ "$PROFILE" == "." ]] && PROFILE_LABEL="default"
    LOCK_DIR="${HH_PROFILES_LOCK_DIR:-/tmp/hh-profile-locks}"
    mkdir -p "$LOCK_DIR"
    exec 9>"$LOCK_DIR/$PROFILE_LABEL.lock"
    if ! flock -n 9; then
        echo "Profile $PROFILE is already being processed; apply skipped" >&2
        exit 75
    fi
    export HH_PROFILE_LOCK_HELD=1
fi

exec python3 "$SCRIPT_DIR/apply_profile.py" "$@"
