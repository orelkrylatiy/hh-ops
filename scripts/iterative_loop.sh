#!/usr/bin/env bash
# Server scheduler: one loop per host, with staggered account runs.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

exec 9>"/tmp/hh-iterative.lock"
flock -n 9 || exit 0
LOG="$PROJECT_ROOT/logs/iterative.log"
ts() { date "+%F %T"; }

while true; do
    MSK_H=$(TZ=Europe/Moscow date +%H)
    MODE="${HH_AUTOMATION_MODE:-off}"
    if [ "$MSK_H" -ge 8 ] && [ "$MSK_H" -lt 22 ] && [ "$MODE" = "live" ]; then
        mapfile -t PROFS < <(grep -v '^\s*$' "$PROJECT_ROOT/.profiles" | tr -d '\r')
        mapfile -t SHUF < <(printf '%s\n' "${PROFS[@]}" | shuf)
        echo "[$(ts)] цикл: порядок=${SHUF[*]}"
        for P in "${SHUF[@]}"; do
            sleep $((60 + RANDOM % 240))
            echo "[$(ts)] $P: reply"
            PROFILES="$P" "$SCRIPT_DIR/all-profiles.sh" reply --live --chats 40 >> "$LOG" 2>&1
            sleep $((30 + RANDOM % 120))
            LIMIT=$((20 + RANDOM % 40))
            echo "[$(ts)] $P: apply (limit=$LIMIT)"
            PROFILES="$P" "$SCRIPT_DIR/all-profiles.sh" apply --live \
                --limit "$LIMIT" --pages 8 --per-page 50 >> "$LOG" 2>&1
        done
        echo "[$(ts)] цикл завершён"
    fi
    sleep $((600 + RANDOM % 900))
done
