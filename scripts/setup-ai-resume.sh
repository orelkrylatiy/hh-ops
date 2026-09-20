#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

PROFILE_ID=""
VARIANT=""
RUN_MODE="dry-run"
REPLACE_ALIAS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            [[ $# -ge 2 ]] || { echo "--profile requires a value" >&2; exit 2; }
            PROFILE_ID="$2"
            shift 2
            ;;
        --variant)
            [[ $# -ge 2 ]] || { echo "--variant requires a value" >&2; exit 2; }
            VARIANT="$2"
            shift 2
            ;;
        --dry-run)
            RUN_MODE="dry-run"
            shift
            ;;
        --live)
            RUN_MODE="live"
            shift
            ;;
        --replace-alias)
            REPLACE_ALIAS=1
            shift
            ;;
        -h|--help)
            cat <<'EOF'
Usage: setup-ai-resume.sh --profile PROFILE [--variant FILE] [--dry-run|--live]

Creates an AI Engineer resume variant from the profile's existing primary/sole
HH resume. Personal data is inherited at runtime and is never stored in Git.

Default variant:
  resumes/variants/<PROFILE>-ai-engineer.toml

Dry-run is the default. --live creates and publishes the resume and stores
resume_aliases.primary + resume_aliases.ai-engineer in the profile config.
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$PROFILE_ID" ]]; then
    echo "--profile is required" >&2
    exit 2
fi

if [[ -z "$VARIANT" ]]; then
    VARIANT="$PROJECT_ROOT/resumes/variants/${PROFILE_ID}-ai-engineer.toml"
elif [[ "$VARIANT" != /* ]]; then
    VARIANT="$PROJECT_ROOT/$VARIANT"
fi

if [[ ! -f "$VARIANT" ]]; then
    echo "AI resume variant not found: $VARIANT" >&2
    exit 1
fi

cmd=(
    hh-applicant-tool
    --no-auto-auth
    --profile-id "$PROFILE_ID"
    create-resume-variant
    "$VARIANT"
    --alias ai-engineer
    --publish
)

if [[ "$RUN_MODE" == "dry-run" ]]; then
    cmd+=(--dry-run)
fi
if [[ "$REPLACE_ALIAS" == "1" ]]; then
    cmd+=(--replace-alias)
fi

echo "AI resume setup: profile=$PROFILE_ID mode=$RUN_MODE variant=$VARIANT"
exec "${cmd[@]}"
