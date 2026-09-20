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

SEARCH_QUERY="${SEARCH_QUERY:-Frontend разработчик}"
APPLY_LIMIT="${APPLY_LIMIT:-100}"
APPLY_PER_PAGE="${APPLY_PER_PAGE:-50}"
APPLY_PAGES="${APPLY_PAGES:-20}"
REPLY_CHATS="${REPLY_CHATS:-100}"
PROFILE_ID="${HH_PROFILE_ID:-}"
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
        --profile)
            [[ $# -ge 2 ]] || { echo "--profile requires a value" >&2; exit 2; }
            PROFILE_ID="$2"
            export HH_PROFILE_ID="$2"
            shift 2
            ;;
        -h|--help)
            cat <<'EOF'
Usage: daily.sh [--dry-run|--live] [options]

  --apply-only      Run only applications.
  --reply-only      Run only one bounded chat pass.
  --with-boost      Also boost resumes. Requires --live.
  --search QUERY
  --limit N         Successful-application quota.
  --pages N         Maximum vacancy pages scanned.
  --per-page N      Vacancies per page.
  --chats N         Maximum candidate chats in the reply pass.
  --profile ID

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

PROFILE_ARGS=()
if [[ -n "$PROFILE_ID" ]]; then
    PROFILE_ARGS=(--profile "$PROFILE_ID")
fi

echo "Daily HH pass: mode=$RUN_MODE profile=${PROFILE_ID:-default}"

if [[ "$WITH_BOOST" == true ]]; then
    "$SCRIPT_DIR/all-profiles.sh" boost --live
fi

if [[ "$REPLY_ONLY" == false ]]; then
    "$SCRIPT_DIR/apply.sh" "$MODE_FLAG" \
        --search "$SEARCH_QUERY" \
        --limit "$APPLY_LIMIT" \
        --pages "$APPLY_PAGES" \
        --per-page "$APPLY_PER_PAGE" \
        "${PROFILE_ARGS[@]}"
fi

if [[ "$APPLY_ONLY" == false ]]; then
    "$SCRIPT_DIR/reply.sh" "$MODE_FLAG" \
        --chats "$REPLY_CHATS" \
        "${PROFILE_ARGS[@]}"
fi
