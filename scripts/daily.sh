#!/usr/bin/env bash
# daily.sh — manual one-shot workflow matching the scheduled automation paths.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.env"
    set +a
fi

# Windows consoles otherwise default to a legacy charmap and crash when the
# CLI prints Cyrillic or emoji status messages.
export PYTHONUTF8="${PYTHONUTF8:-1}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

SEARCH_QUERY="${SEARCH_QUERY:-Frontend разработчик}"
APPLY_LIMIT="${APPLY_LIMIT:-100}"
APPLY_PER_PAGE="${APPLY_PER_PAGE:-50}"
APPLY_PAGES="${APPLY_PAGES:-20}"
REPLY_CHATS="${REPLY_CHATS:-100}"
PROFILE_ID="${HH_PROFILE_ID:-}"
PROFILES_LIST=()
RUN_MODE="dry-run"
RUN_MODE_EXPLICIT=""
APPLY_ONLY=false
REPLY_ONLY=false
WITH_BOOST=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply-only)
            APPLY_ONLY=true
            shift
            ;;
        --reply-only)
            REPLY_ONLY=true
            shift
            ;;
        --with-boost)
            WITH_BOOST=true
            shift
            ;;
        --full)
            # Backward-compatible alias for a full apply+reply pass. Resume
            # publication remains a separate explicit --with-boost decision.
            shift
            ;;
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
        --search)
            [[ $# -ge 2 ]] || { echo "--search requires a value" >&2; exit 2; }
            SEARCH_QUERY="$2"
            shift 2
            ;;
        --limit)
            [[ $# -ge 2 ]] || { echo "--limit requires a value" >&2; exit 2; }
            APPLY_LIMIT="$2"
            shift 2
            ;;
        --pages)
            [[ $# -ge 2 ]] || { echo "--pages requires a value" >&2; exit 2; }
            APPLY_PAGES="$2"
            shift 2
            ;;
        --per-page)
            [[ $# -ge 2 ]] || { echo "--per-page requires a value" >&2; exit 2; }
            APPLY_PER_PAGE="$2"
            shift 2
            ;;
        --chats)
            [[ $# -ge 2 ]] || { echo "--chats requires a value" >&2; exit 2; }
            REPLY_CHATS="$2"
            shift 2
            ;;
        --profiles)
            shift
            _COLLECTED=0
            while [[ $# -gt 0 && "$1" != --* ]]; do
                IFS=', ' read -ra _PARTS <<< "$1"
                for _p in "${_PARTS[@]}"; do
                    [[ -z "$_p" ]] && continue
                    case " ${PROFILES_LIST[*]} " in
                        *" $_p "*) continue ;;
                    esac
                    PROFILES_LIST+=("$_p")
                    _COLLECTED=$((_COLLECTED + 1))
                done
                shift
            done
            ((_COLLECTED > 0)) || { echo "--profiles requires at least one profile id" >&2; exit 2; }
            ;;
        --profile)
            [[ $# -ge 2 ]] || { echo "--profile requires a value" >&2; exit 2; }
            PROFILE_ID="$2"
            export HH_PROFILE_ID="$2"
            shift 2
            ;;
        -h|--help)
            cat <<'EOF'
Usage: daily.sh [--dry-run|--live] [options]

One-shot pass: boost (optional) -> applications -> chat replies, for one,
several or all HH accounts. With no profile selection the full fleet from
.profiles is used; a single account runs its stage scripts directly.

  --profiles ID...  Target accounts (space- or comma-separated).
  --profile ID      Legacy single-account form; same as --profiles ID.
  --apply-only      Run only applications.
  --reply-only      Run only one bounded chat pass.
  --with-boost      Also boost resumes first. Requires --live. Scoped to the
                    same account selection.
  --search QUERY
  --limit N         Successful-application quota.
  --pages N         Maximum vacancy pages scanned.
  --per-page N      Vacancies per page.
  --chats N         Maximum candidate chats in the reply pass.

Default mode is dry-run. Unlike cron, this command performs one immediate pass
and exits; it does not loop or sleep between chat checks.
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

if [[ "$APPLY_ONLY" == true && "$REPLY_ONLY" == true ]]; then
    echo "Cannot combine --apply-only and --reply-only" >&2
    exit 2
fi
if [[ "$WITH_BOOST" == true && "$RUN_MODE" != "live" ]]; then
    echo "--with-boost requires explicit --live" >&2
    exit 2
fi

MODE_FLAG="--dry-run"
[[ "$RUN_MODE" == "live" ]] && MODE_FLAG="--live"

# Resolve the target profile list: explicit --profiles wins, then --profile,
# then the fleet default (.profiles file, PROFILES, HH_PROFILE_ID).
if (( ${#PROFILES_LIST[@]} == 0 )); then
    if [[ -n "$PROFILE_ID" ]]; then
        PROFILES_LIST=("$PROFILE_ID")
    elif [[ -f "$PROJECT_ROOT/.profiles" ]]; then
        while IFS= read -r _p; do
            [[ -n "$_p" ]] && PROFILES_LIST+=("$_p")
        done < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$PROJECT_ROOT/.profiles")
    elif [[ -n "${PROFILES:-}" ]]; then
        read -ra PROFILES_LIST <<< "$PROFILES"
    elif [[ -n "${HH_PROFILE_ID:-}" ]]; then
        PROFILES_LIST=("$HH_PROFILE_ID")
    fi
fi

# Fail closed on typos instead of silently skipping an account mid-run.
if (( ${#PROFILES_LIST[@]} > 0 )); then
    declare -A KNOWN_PROFILES=()
    if [[ -f "$PROJECT_ROOT/.profiles" ]]; then
        while IFS= read -r _p; do
            [[ -n "$_p" ]] && KNOWN_PROFILES["$_p"]=1
        done < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$PROJECT_ROOT/.profiles")
    fi
    CONFIG_BASE="${CONFIG_DIR:-$PROJECT_ROOT/config}"
    if [[ -d "$CONFIG_BASE" ]]; then
        shopt -s nullglob
        for _d in "$CONFIG_BASE"/*/; do
            [[ -f "$_d/config.json" ]] && KNOWN_PROFILES["$(basename "$_d")"]=1
        done
        shopt -u nullglob
    fi
    for _p in "${PROFILES_LIST[@]}"; do
        if [[ "$_p" != "." && ! "$_p" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
            echo "Invalid profile id: $_p" >&2
            exit 2
        fi
        if (( ${#KNOWN_PROFILES[@]} > 0 )) && [[ -z "${KNOWN_PROFILES["$_p"]:-}" ]]; then
            echo "Unknown profile: $_p (known: ${!KNOWN_PROFILES[*]})" >&2
            exit 2
        fi
    done
fi

FLEET_MODE=false
(( ${#PROFILES_LIST[@]} > 1 )) && FLEET_MODE=true

# Stage runners read the target set from PROFILES; all-profiles.sh prefers an
# explicit PROFILES value over the repository .profiles file.
export PROFILES="${PROFILES_LIST[*]}"

PROFILE_ARGS=()
if [[ "$FLEET_MODE" == false && ${#PROFILES_LIST[@]} -eq 1 ]]; then
    PROFILE_ARGS=(--profile "${PROFILES_LIST[0]}")
fi

echo "Daily HH pass: mode=$RUN_MODE profiles=${PROFILES_LIST[*]:-default}"

# An error in one stage must not suppress independent stages (especially chat
# replies after an application batch). Preserve a failing final exit status so
# callers, cron and monitoring still notice partial failures.
FAILED_STAGES=()
run_stage() {
    local stage="$1"
    shift
    local status=0
    if "$@"; then
        echo "HH_DAILY_STAGE stage=$stage status=0"
    else
        status=$?
        echo "HH_DAILY_STAGE stage=$stage status=$status" >&2
        FAILED_STAGES+=("$stage:$status")
    fi
}

if [[ "$WITH_BOOST" == true ]]; then
    # all-profiles.sh scopes boost to the exported PROFILES set; boost has no
    # preview mode, so it always runs live.
    run_stage boost "$SCRIPT_DIR/all-profiles.sh" boost --live
fi

if [[ "$REPLY_ONLY" == false ]]; then
    if [[ "$FLEET_MODE" == true ]]; then
        run_stage apply "$SCRIPT_DIR/all-profiles.sh" apply "$MODE_FLAG" \
            --search "$SEARCH_QUERY" \
            --limit "$APPLY_LIMIT" \
            --pages "$APPLY_PAGES" \
            --per-page "$APPLY_PER_PAGE"
    else
        run_stage apply "$SCRIPT_DIR/apply.sh" "$MODE_FLAG" \
            --search "$SEARCH_QUERY" \
            --limit "$APPLY_LIMIT" \
            --pages "$APPLY_PAGES" \
            --per-page "$APPLY_PER_PAGE" \
            "${PROFILE_ARGS[@]}"
    fi
fi

if [[ "$APPLY_ONLY" == false ]]; then
    if [[ "$FLEET_MODE" == true ]]; then
        run_stage reply "$SCRIPT_DIR/all-profiles.sh" reply "$MODE_FLAG" \
            --chats "$REPLY_CHATS"
    else
        run_stage reply "$SCRIPT_DIR/reply.sh" "$MODE_FLAG" \
            --chats "$REPLY_CHATS" \
            "${PROFILE_ARGS[@]}"
    fi
fi

if (( ${#FAILED_STAGES[@]} > 0 )); then
    echo "HH_DAILY_RESULT status=failed stages=${FAILED_STAGES[*]}" >&2
    exit 1
fi
echo "HH_DAILY_RESULT status=ok"
