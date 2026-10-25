#!/usr/bin/env bash
# apply.sh — bounded HH vacancy applications with AI cover letters.

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
MAX_RESPONSES="${APPLY_LIMIT:-${LIMIT:-100}}"
PER_PAGE="${APPLY_PER_PAGE:-50}"
TOTAL_PAGES="${APPLY_PAGES:-20}"
RUN_TIMEOUT="${APPLY_RUN_TIMEOUT:-3600}"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-$PROJECT_ROOT/prompts/cover_letter_frontend.txt}"
HARD_FILTER_FILE="${APPLY_HARD_FILTER_FILE:-$PROJECT_ROOT/rules/apply-hard-filter.regex}"
EXCLUDED_FILTER="${EXCLUDED_FILTER:-}"
RUN_MODE="dry-run"
RUN_MODE_EXPLICIT=""
PROFILE_ID="${HH_PROFILE_ID:-}"
RESUME_ID=""
RESUME_ALIAS=""
AI_FILTER=""

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
        --search)
            [[ $# -ge 2 ]] || { echo "--search requires a value" >&2; exit 2; }
            SEARCH_QUERY="$2"
            shift 2
            ;;
        --resume-id)
            [[ $# -ge 2 ]] || { echo "--resume-id requires a value" >&2; exit 2; }
            [[ -z "$RESUME_ALIAS" ]] || { echo "Cannot combine --resume-id and --resume-alias" >&2; exit 2; }
            RESUME_ID="$2"
            shift 2
            ;;
        --resume-alias)
            [[ $# -ge 2 ]] || { echo "--resume-alias requires a value" >&2; exit 2; }
            [[ -z "$RESUME_ID" ]] || { echo "Cannot combine --resume-id and --resume-alias" >&2; exit 2; }
            RESUME_ALIAS="$2"
            shift 2
            ;;
        --ai-filter)
            [[ $# -ge 2 ]] || { echo "--ai-filter requires heavy or light" >&2; exit 2; }
            case "$2" in
                heavy|light) AI_FILTER="$2" ;;
                *) echo "--ai-filter must be heavy or light" >&2; exit 2 ;;
            esac
            shift 2
            ;;
        --limit|--max-responses)
            [[ $# -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
            MAX_RESPONSES="$2"
            shift 2
            ;;
        --per-page)
            [[ $# -ge 2 ]] || { echo "--per-page requires a value" >&2; exit 2; }
            PER_PAGE="$2"
            shift 2
            ;;
        --pages)
            [[ $# -ge 2 ]] || { echo "--pages requires a value" >&2; exit 2; }
            TOTAL_PAGES="$2"
            shift 2
            ;;
        --timeout)
            [[ $# -ge 2 ]] || { echo "--timeout requires seconds" >&2; exit 2; }
            RUN_TIMEOUT="$2"
            shift 2
            ;;
        --system-prompt)
            [[ $# -ge 2 ]] || { echo "--system-prompt requires a value" >&2; exit 2; }
            SYSTEM_PROMPT="$2"
            shift 2
            ;;
        --hard-filter-file)
            [[ $# -ge 2 ]] || { echo "--hard-filter-file requires a value" >&2; exit 2; }
            HARD_FILTER_FILE="$2"
            EXCLUDED_FILTER=""
            shift 2
            ;;
        --excluded-filter)
            [[ $# -ge 2 ]] || { echo "--excluded-filter requires a value" >&2; exit 2; }
            EXCLUDED_FILTER="$2"
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
Usage: apply.sh [--dry-run|--live] [options]

  --live                    Send real applications. Default is dry-run.
  --search QUERY            Search query.
  --resume-id ID            Apply only with this resume ID.
  --resume-alias ALIAS      Apply only with this profile-local resume alias.
  --ai-filter MODE          Vacancy AI filter: light or heavy.
  --limit N                 Maximum successful applications for this run.
  --per-page N              Search results per page (default: 50).
  --pages N                 Maximum search pages (default: 20).
  --timeout SECONDS         Upper bound for the whole batch (default: 3600).
  --system-prompt FILE      AI system prompt template.
  --hard-filter-file FILE   File with one exclusion regex per line.
  --excluded-filter REGEX   Inline emergency override for the hard filter.
  --profile ID

The default hard filter is loaded from rules/apply-hard-filter.regex. Override
its path with APPLY_HARD_FILTER_FILE. EXCLUDED_FILTER remains available as an
inline runtime override, but the repository does not hardcode vacancy stop words
inside the application code.

Cover letters are generated by Humanizer Framework using its own provider.
If generation or quality checks fail, that vacancy is skipped and the run fails.
Email sending is enabled by default (`APPLY_SEND_EMAIL=1`) and sends the same
validated letter after a successful HH application, when Gmail OAuth or SMTP
settings are configured.

The scan depth is intentionally independent from --limit. This lets the worker
skip irrelevant/already-applied vacancies and continue until it reaches the
successful-application quota or exhausts the configured pages.
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

for value_name in MAX_RESPONSES PER_PAGE TOTAL_PAGES RUN_TIMEOUT; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        echo "$value_name must be a positive integer: $value" >&2
        exit 2
    fi
done
if (( PER_PAGE > 100 )); then
    echo "PER_PAGE cannot exceed 100" >&2
    exit 2
fi

FILTER_SOURCE="EXCLUDED_FILTER"
if [[ -z "$EXCLUDED_FILTER" ]]; then
    FILTER_SOURCE="$HARD_FILTER_FILE"
    if [[ ! -f "$HARD_FILTER_FILE" ]]; then
        echo "Hard-filter file not found: $HARD_FILTER_FILE" >&2
        exit 1
    fi
    EXCLUDED_FILTER="$(
        sed \
            -e 's/\r$//' \
            -e '/^[[:space:]]*#/d' \
            -e '/^[[:space:]]*$/d' \
            "$HARD_FILTER_FILE" \
            | paste -sd'|' -
    )"
    if [[ -z "$EXCLUDED_FILTER" ]]; then
        echo "Hard-filter file has no active patterns: $HARD_FILTER_FILE" >&2
        exit 1
    fi
fi

"$PYTHON_BIN" - "$EXCLUDED_FILTER" <<'PY'
import re
import sys

try:
    re.compile(sys.argv[1], re.IGNORECASE)
except re.error as exc:
    print(f"Invalid hard-filter regex: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc
PY

if [[ ! -f "$SYSTEM_PROMPT" ]]; then
    echo "Cover-letter prompt not found: $SYSTEM_PROMPT" >&2
    exit 1
fi

# render_prompt.py (venv-python, UTF-8 end-to-end) вместо envsubst:
# envsubst на Windows портит кириллицу в значениях переменных (Максим -> ??????).
RENDERED_SYSTEM_PROMPT="$(mktemp "${TMPDIR:-/tmp}/hh-apply-prompt.XXXXXX")"
cleanup() {
    rm -f "$RENDERED_SYSTEM_PROMPT"
}
trap cleanup EXIT

if ! "$PYTHON_BIN" "$SCRIPT_DIR/render_prompt.py" "$SYSTEM_PROMPT" "$RENDERED_SYSTEM_PROMPT"; then
    echo "Prompt rendering failed: $SYSTEM_PROMPT" >&2
    exit 1
fi

# Префлайт LLM обязателен: с выключенным cover_letter_fallback (fail-closed)
# продолжать без живого ИИ нельзя — отклики без AI запрещены правилом Макса.
if ! "$PYTHON_BIN" "$SCRIPT_DIR/check_framework.py" --purpose application; then
    echo "Cover-letter AI preflight failed; refusing to run without AI" >&2
    exit 1
fi

HH_EXECUTABLE="hh-applicant-tool"
if [[ -f "$PROJECT_ROOT/.venv/Scripts/hh-applicant-tool.exe" ]]; then
    HH_EXECUTABLE="$PROJECT_ROOT/.venv/Scripts/hh-applicant-tool.exe"
elif [[ -x "$PROJECT_ROOT/.venv/bin/hh-applicant-tool" ]]; then
    HH_EXECUTABLE="$PROJECT_ROOT/.venv/bin/hh-applicant-tool"
fi
HH_CMD=("$HH_EXECUTABLE" --no-auto-auth)
if [[ -n "$PROFILE_ID" ]]; then
    HH_CMD+=(--profile-id "$PROFILE_ID")
fi

MODE_ARGS=()
if [[ "$RUN_MODE" == "dry-run" ]]; then
    MODE_ARGS+=(--dry-run)
fi

RESUME_ARGS=()
if [[ -n "$RESUME_ID" ]]; then
    RESUME_ARGS+=(--resume-id "$RESUME_ID")
elif [[ -n "$RESUME_ALIAS" ]]; then
    RESUME_ARGS+=(--resume-alias "$RESUME_ALIAS")
fi

AI_FILTER_ARGS=()
if [[ -n "$AI_FILTER" ]]; then
    AI_FILTER_ARGS+=(--ai-filter "$AI_FILTER")
fi

EMAIL_ARGS=()
if [[ "${APPLY_SEND_EMAIL:-1}" == "1" ]]; then
    EMAIL_ARGS+=(--send-email)
fi

APPLY_CMD=(
    "${HH_CMD[@]}" apply-safe
    --search "$SEARCH_QUERY"
    --ai
    --system-prompt "$RENDERED_SYSTEM_PROMPT"
    --force-message
    --excluded-filter "$EXCLUDED_FILTER"
    --skip-tests
    --max-responses "$MAX_RESPONSES"
    --per-page "$PER_PAGE"
    --total-pages "$TOTAL_PAGES"
    "${RESUME_ARGS[@]}"
    "${AI_FILTER_ARGS[@]}"
    "${EMAIL_ARGS[@]}"
    "${MODE_ARGS[@]}"
)

# Необязательные фильтры поиска из apply-safe (area, work-format, period, order-by,
# response-delay...), например: APPLY_EXTRA_ARGS="--work-format remote --area 113 --period 7"
if [[ -n "${APPLY_EXTRA_ARGS:-}" ]]; then
    read -r -a APPLY_EXTRA <<< "$APPLY_EXTRA_ARGS"
    APPLY_CMD+=("${APPLY_EXTRA[@]}")
fi

selector="all-published"
[[ -n "$RESUME_ID" ]] && selector="id:$RESUME_ID"
[[ -n "$RESUME_ALIAS" ]] && selector="alias:$RESUME_ALIAS"
echo "HH apply: mode=$RUN_MODE query='$SEARCH_QUERY' resume='$selector' ai_filter='${AI_FILTER:-off}' hard_filter='$FILTER_SOURCE' max_responses=$MAX_RESPONSES scan=$TOTAL_PAGES*$PER_PAGE timeout=${RUN_TIMEOUT}s"

if command -v timeout >/dev/null 2>&1; then
    timeout --signal=TERM --kill-after=30 "${RUN_TIMEOUT}s" "${APPLY_CMD[@]}"
else
    echo "Warning: 'timeout' command unavailable; running without process deadline" >&2
    "${APPLY_CMD[@]}"
fi
