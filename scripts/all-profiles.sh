#!/usr/bin/env bash
# all-profiles.sh — run one operation for configured HH profiles with bounded concurrency.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.env"
    set +a
fi

PROFILES_LIST=()
if [[ -f "$PROJECT_ROOT/.profiles" ]]; then
    while IFS= read -r profile; do
        [[ -n "$profile" ]] && PROFILES_LIST+=("$profile")
    done < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$PROJECT_ROOT/.profiles")
elif [[ -n "${PROFILES:-}" ]]; then
    read -ra PROFILES_LIST <<< "$PROFILES"
else
    PROFILES_LIST=("1206")
fi

if [[ ${#PROFILES_LIST[@]} -eq 0 ]]; then
    echo "Profile list is empty; check .profiles or PROFILES" >&2
    exit 1
fi

# Ten HH accounts are expected to be able to work concurrently. Keep this
# configurable for larger fleets or temporarily constrained hosts/providers.
PARALLELISM="${HH_PROFILE_PARALLELISM:-10}"
if [[ ! "$PARALLELISM" =~ ^[1-9][0-9]*$ ]]; then
    echo "HH_PROFILE_PARALLELISM must be a positive integer: $PARALLELISM" >&2
    exit 2
fi

COMMAND="${1:-daily}"
shift || true
ARGS=("$@")

case "$COMMAND" in
    apply) CMD_SCRIPT="$SCRIPT_DIR/apply.sh" ;;
    reply) CMD_SCRIPT="$SCRIPT_DIR/reply.sh" ;;
    cleanup) CMD_SCRIPT="$SCRIPT_DIR/cleanup.sh" ;;
    daily) CMD_SCRIPT="$SCRIPT_DIR/daily.sh" ;;
    boost|update|refresh) CMD_SCRIPT="" ;;
    *)
        echo "Unknown command: $COMMAND (apply | reply | cleanup | daily | boost | update | refresh)" >&2
        exit 1
        ;;
esac

RUN_MODE_MARKER="utility"
if [[ "$COMMAND" == "apply" || "$COMMAND" == "reply" || "$COMMAND" == "cleanup" || "$COMMAND" == "daily" ]]; then
    RUN_MODE_MARKER="dry-run"
    for arg in "${ARGS[@]}"; do
        [[ "$arg" == "--live" ]] && RUN_MODE_MARKER="live"
    done
elif [[ "$COMMAND" == "boost" || "$COMMAND" == "update" ]]; then
    RUN_MODE_MARKER="live"
fi

# Publishing/resume boost changes the HH account and has no native preview.
if [[ "$COMMAND" == "boost" || "$COMMAND" == "update" ]]; then
    LIVE_CONFIRMED=false
    FILTERED_ARGS=()
    for arg in "${ARGS[@]}"; do
        case "$arg" in
            --live) LIVE_CONFIRMED=true ;;
            --dry-run)
                echo "$COMMAND has no preview mode; use --live explicitly" >&2
                exit 1
                ;;
            *) FILTERED_ARGS+=("$arg") ;;
        esac
    done
    if [[ "$LIVE_CONFIRMED" != true ]]; then
        echo "$COMMAND publishes/updates resumes; re-run with --live" >&2
        exit 1
    fi
    ARGS=("${FILTERED_ARGS[@]}")
fi

if ! command -v flock >/dev/null 2>&1; then
    echo "flock is required for crash-safe per-profile locking" >&2
    exit 1
fi

# Keep wrapper logs persistent so daily ops snapshots can reconstruct every run.
# Raw logs stay git-ignored; only aggregate ops/*.json is published.
LOG_DIR="${HH_PROFILES_LOG_DIR:-$PROJECT_ROOT/logs/profiles}"
LOCK_DIR="${HH_PROFILES_LOCK_DIR:-/tmp/hh-profile-locks}"
mkdir -p "$LOG_DIR" "$LOCK_DIR"

echo "Running '$COMMAND' for ${#PROFILES_LIST[@]} profile(s), parallelism=$PARALLELISM"
echo "Profiles: ${PROFILES_LIST[*]}"
echo "Logs: $LOG_DIR/<profile>-$COMMAND.log"

PIDS=()
RUN_PROFILES=()
FAILED=()

wait_batch() {
    local index pid profile
    for index in "${!PIDS[@]}"; do
        pid="${PIDS[$index]}"
        profile="${RUN_PROFILES[$index]}"
        if wait "$pid"; then
            echo "OK: $profile"
        else
            echo "FAILED: $profile (see $LOG_DIR/${profile}-${COMMAND}.log)" >&2
            FAILED+=("$profile")
        fi
    done
    PIDS=()
    RUN_PROFILES=()
}

start_profile() {
    local profile="$1"
    local log="$LOG_DIR/${profile}-${COMMAND}.log"
    local lock="$LOCK_DIR/${profile}.lock"

    (
        # File-descriptor locks are released by the kernel even on crash/OOM/SIGKILL.
        exec 9>"$lock"
        if ! flock -n 9; then
            echo "[$(date '+%F %T')] HH_RUN_SKIP profile=$profile command=$COMMAND mode=$RUN_MODE_MARKER status=0"
            echo "Profile $profile is already being processed; skipped"
            exit 0
        fi

        echo "[$(date '+%F %T')] HH_RUN_START profile=$profile command=$COMMAND mode=$RUN_MODE_MARKER"
        set +e
        case "$COMMAND" in
            boost)
                hh-applicant-tool --no-auto-auth --profile-id "$profile" boost-resume
                ;;
            update)
                hh-applicant-tool --no-auto-auth --profile-id "$profile" update-resumes
                ;;
            refresh)
                hh-applicant-tool --no-auto-auth --profile-id "$profile" refresh-token
                ;;
            *)
                if [[ ${#ARGS[@]} -gt 0 ]]; then
                    bash "$CMD_SCRIPT" --profile "$profile" "${ARGS[@]}"
                else
                    bash "$CMD_SCRIPT" --profile "$profile"
                fi
                ;;
        esac
        status=$?
        set -e
        echo "[$(date '+%F %T')] HH_RUN_END profile=$profile command=$COMMAND mode=$RUN_MODE_MARKER status=$status"
        exit "$status"
    ) >> "$log" 2>&1 &

    PIDS+=("$!")
    RUN_PROFILES+=("$profile")
}

for profile in "${PROFILES_LIST[@]}"; do
    if [[ ! "$profile" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
        echo "Invalid profile id: $profile" >&2
        FAILED+=("$profile")
        continue
    fi

    start_profile "$profile"
    if (( ${#PIDS[@]} >= PARALLELISM )); then
        wait_batch
    fi
done

if (( ${#PIDS[@]} > 0 )); then
    wait_batch
fi

if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo "Profiles with errors: ${FAILED[*]}" >&2
    exit 1
fi

echo "All profiles completed successfully"
