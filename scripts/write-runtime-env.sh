#!/usr/bin/env bash
# Persist the scheduler environment for cron, which runs with a reduced environment.
# The output can contain the optional Telegram bot token and MUST stay mode 0600.

set -euo pipefail

OUTPUT_FILE="${1:-/tmp/hh-runtime.env}"
umask 077

{
    printf 'export HH_AUTOMATION_MODE=%q\n' "${HH_AUTOMATION_MODE:-off}"
    printf 'export CONFIG_DIR=%q\n' "${CONFIG_DIR:-/app/config}"
    printf 'export TZ=%q\n' "${TZ:-Europe/Moscow}"
    printf 'export HH_NAME=%q\n' "${HH_NAME:-}"
    printf 'export HH_TELEGRAM=%q\n' "${HH_TELEGRAM:-}"
    printf 'export HH_NOTIFY_TELEGRAM_ENABLED=%q\n' "${HH_NOTIFY_TELEGRAM_ENABLED:-0}"
    printf 'export HH_NOTIFY_TELEGRAM_BOT_TOKEN=%q\n' "${HH_NOTIFY_TELEGRAM_BOT_TOKEN:-}"
    printf 'export HH_NOTIFY_TELEGRAM_CHAT_ID=%q\n' "${HH_NOTIFY_TELEGRAM_CHAT_ID:-}"
    printf 'export SEARCH_QUERY=%q\n' "${SEARCH_QUERY:-Frontend разработчик}"
    printf 'export APPLY_LIMIT=%q\n' "${APPLY_LIMIT:-100}"
    printf 'export APPLY_PER_PAGE=%q\n' "${APPLY_PER_PAGE:-50}"
    printf 'export APPLY_PAGES=%q\n' "${APPLY_PAGES:-20}"
    printf 'export APPLY_RUN_TIMEOUT=%q\n' "${APPLY_RUN_TIMEOUT:-3600}"
    printf 'export APPLY_HARD_FILTER_FILE=%q\n' "${APPLY_HARD_FILTER_FILE:-}"
    printf 'export EXCLUDED_FILTER=%q\n' "${EXCLUDED_FILTER:-}"
    printf 'export REPLY_CHATS=%q\n' "${REPLY_CHATS:-100}"
    printf 'export CLEANUP_DELETE_CHAT=%q\n' "${CLEANUP_DELETE_CHAT:-1}"
    printf 'export HH_PROFILE_PARALLELISM=%q\n' "${HH_PROFILE_PARALLELISM:-10}"
} > "$OUTPUT_FILE"
chmod 0600 "$OUTPUT_FILE"
