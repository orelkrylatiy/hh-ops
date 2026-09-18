#!/usr/bin/env bash
# Generate one aggregate runtime snapshot and publish only ops/ files to GitHub.
# Intended for a clean VPS/host clone that has git credentials configured.
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATE_SPEC="${1:-yesterday}"
TIMEZONE="${OPS_TIMEZONE:-${TZ:-Europe/Moscow}}"
BRANCH="${OPS_PUBLISH_BRANCH:-main}"
PYTHON_BIN="${OPS_PYTHON:-python3}"

cd "$BASE"

CURRENT_BRANCH="$(git branch --show-current)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
    echo "ops publish: current branch '$CURRENT_BRANCH', expected '$BRANCH'" >&2
    exit 2
fi

# Never pull/commit across unrelated tracked work on the runtime machine.
NON_OPS_DIRTY="$(git status --porcelain --untracked-files=no | awk '{print substr($0,4)}' | grep -v '^ops/' || true)"
if [[ -n "$NON_OPS_DIRTY" ]]; then
    echo "ops publish: tracked changes outside ops/; publish cancelled" >&2
    echo "$NON_OPS_DIRTY" >&2
    exit 2
fi

NON_OPS_STAGED="$(git diff --cached --name-only | grep -v '^ops/' || true)"
if [[ -n "$NON_OPS_STAGED" ]]; then
    echo "ops publish: staged changes outside ops/; publish cancelled" >&2
    echo "$NON_OPS_STAGED" >&2
    exit 2
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
    echo "ops publish: Python not found: $PYTHON_BIN" >&2
    exit 1
}

git pull --ff-only origin "$BRANCH"

REPORT_PATH="$(OPS_TIMEZONE="$TIMEZONE" "$PYTHON_BIN" scripts/ops/daily_report.py --date "$DATE_SPEC" --timezone "$TIMEZONE")"
if [[ ! -f "$REPORT_PATH" ]]; then
    echo "ops publish: report was not created: $REPORT_PATH" >&2
    exit 1
fi

git add -- "$REPORT_PATH" ops/latest.json
if git diff --cached --quiet -- "$REPORT_PATH" ops/latest.json; then
    echo "ops publish: no changes for $DATE_SPEC"
    exit 0
fi

REPORT_DATE="$(basename "$REPORT_PATH" .json)"
# Keep quality checks from the repository hook, but do not let that hook rebuild
# a `today` snapshot while we are intentionally publishing another date.
OPS_SKIP_HOOK_SNAPSHOT=1 git commit -m "ops: daily snapshot $REPORT_DATE" -- "$REPORT_PATH" ops/latest.json
git push origin "$BRANCH"
echo "ops publish: published $REPORT_PATH"
