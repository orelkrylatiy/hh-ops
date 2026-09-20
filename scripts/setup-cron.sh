#!/usr/bin/env bash
# Install the production HH schedule plus daily runtime diagnostics.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PROJECT_DIR="${PROJECT_DIR:-$PROJECT_ROOT}"

if ! command -v crontab >/dev/null 2>&1; then
    echo "crontab command not found" >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR/logs"

APPLY_TIME="${APPLY_TIME:-09:10}"
REPLY_START_HOUR="${REPLY_START_HOUR:-9}"
REPLY_END_HOUR="${REPLY_END_HOUR:-21}"
BOOST_TIME="${BOOST_TIME:-09:00}"
CLEANUP_TIME="${CLEANUP_TIME:-22:10}"
OPS_REPORT_TIME="${OPS_REPORT_TIME:-02:20}"
OPS_PUBLISH_TIME="${OPS_PUBLISH_TIME:-02:30}"
OPS_AUTO_PUBLISH="${OPS_AUTO_PUBLISH:-0}"
OPS_TIMEZONE="${OPS_TIMEZONE:-${TZ:-Europe/Moscow}}"
OPS_PYTHON="${OPS_PYTHON:-python3}"

parse_time() {
    local value="$1"
    local hour minute
    IFS=: read -r hour minute <<< "$value"
    if [[ ! "$hour" =~ ^([01]?[0-9]|2[0-3])$ || ! "$minute" =~ ^[0-5]?[0-9]$ ]]; then
        echo "Invalid time: $value (expected HH:MM)" >&2
        exit 2
    fi
    printf '%d %d\n' "$((10#$minute))" "$((10#$hour))"
}

read -r BOOST_MIN BOOST_HOUR < <(parse_time "$BOOST_TIME")
read -r APPLY_MIN APPLY_HOUR < <(parse_time "$APPLY_TIME")
read -r CLEANUP_MIN CLEANUP_HOUR < <(parse_time "$CLEANUP_TIME")
read -r OPS_REPORT_MIN OPS_REPORT_HOUR < <(parse_time "$OPS_REPORT_TIME")
read -r OPS_PUBLISH_MIN OPS_PUBLISH_HOUR < <(parse_time "$OPS_PUBLISH_TIME")

if (( REPLY_START_HOUR < 0 || REPLY_START_HOUR > 23 || REPLY_END_HOUR < REPLY_START_HOUR || REPLY_END_HOUR > 23 )); then
    echo "Invalid reply hour range: $REPLY_START_HOUR-$REPLY_END_HOUR" >&2
    exit 2
fi
if [[ "$OPS_AUTO_PUBLISH" != "0" && "$OPS_AUTO_PUBLISH" != "1" ]]; then
    echo "OPS_AUTO_PUBLISH must be 0 or 1" >&2
    exit 2
fi

MARKER="# work-optimization autonomous HH jobs"
OPS_MARKER="# work-optimization ops snapshots"
BOOST_JOB="$BOOST_MIN $BOOST_HOUR * * * cd $PROJECT_DIR && /bin/bash $PROJECT_DIR/scripts/cron-job.sh boost >> $PROJECT_DIR/logs/cron.log 2>&1"
APPLY_JOB="$APPLY_MIN $APPLY_HOUR * * * cd $PROJECT_DIR && /bin/bash $PROJECT_DIR/scripts/cron-job.sh apply >> $PROJECT_DIR/logs/cron.log 2>&1"
REPLY_JOB="25 $REPLY_START_HOUR-$REPLY_END_HOUR * * * cd $PROJECT_DIR && /bin/bash $PROJECT_DIR/scripts/cron-job.sh reply >> $PROJECT_DIR/logs/cron.log 2>&1"
CLEANUP_JOB="$CLEANUP_MIN $CLEANUP_HOUR * * * cd $PROJECT_DIR && /bin/bash $PROJECT_DIR/scripts/cron-job.sh cleanup >> $PROJECT_DIR/logs/cron.log 2>&1"
OPS_REPORT_JOB="$OPS_REPORT_MIN $OPS_REPORT_HOUR * * * cd $PROJECT_DIR && OPS_TIMEZONE=$OPS_TIMEZONE $OPS_PYTHON $PROJECT_DIR/scripts/ops/daily_report.py --date yesterday --timezone $OPS_TIMEZONE >> $PROJECT_DIR/logs/ops-daily.log 2>&1"
if [[ "$OPS_AUTO_PUBLISH" == "1" ]]; then
    OPS_PUBLISH_JOB="$OPS_PUBLISH_MIN $OPS_PUBLISH_HOUR * * * cd $PROJECT_DIR && OPS_TIMEZONE=$OPS_TIMEZONE OPS_PYTHON=$OPS_PYTHON /bin/bash $PROJECT_DIR/scripts/ops/daily_publish.sh yesterday >> $PROJECT_DIR/logs/ops-publish.log 2>&1"
else
    OPS_PUBLISH_JOB="# ops auto-publish disabled; run with OPS_AUTO_PUBLISH=1 to install it"
fi

TMP_CRON="$(mktemp)"
cleanup() {
    rm -f "$TMP_CRON"
}
trap cleanup EXIT

crontab -l 2>/dev/null | awk -v marker="$MARKER" -v ops_marker="$OPS_MARKER" '
    $0 == marker {skip=4; next}
    $0 == ops_marker {skip=2; next}
    skip > 0 {skip--; next}
    {print}
' > "$TMP_CRON" || true

{
    cat "$TMP_CRON"
    echo "$MARKER"
    echo "$BOOST_JOB"
    echo "$APPLY_JOB"
    echo "$REPLY_JOB"
    echo "$CLEANUP_JOB"
    echo "$OPS_MARKER"
    echo "$OPS_REPORT_JOB"
    echo "$OPS_PUBLISH_JOB"
} | crontab -

cat <<EOF
Cron installed:
  ops snapshot: $OPS_REPORT_TIME (always local/aggregate-only)
  ops publish:  $OPS_PUBLISH_TIME (enabled=$OPS_AUTO_PUBLISH)
  boost: $BOOST_TIME
  apply: $APPLY_TIME
  reply: hourly at :25, $REPLY_START_HOUR-$REPLY_END_HOUR
  cleanup rejected negotiations: $CLEANUP_TIME

Actual HH writes are controlled by $PROJECT_DIR/.env:
  HH_AUTOMATION_MODE=off      # disabled
  HH_AUTOMATION_MODE=dry-run  # inspect only
  HH_AUTOMATION_MODE=live     # send/publish

To publish aggregate ops snapshots to GitHub automatically, re-run once with:
  OPS_AUTO_PUBLISH=1 bash scripts/setup-cron.sh
This requires git push credentials on the runtime host.
EOF
