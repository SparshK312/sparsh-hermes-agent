#!/usr/bin/env bash
# Hermes cron: assistant-checkin (hourly, 08:00-22:00). --no-agent.
# One check-in: look for ONE thing worth saying, say it or stay silent. SHADOW by default
# (writes "what I would have sent" to the vault report); it goes live only when
# ~/.hermes/assistant/LIVE exists (scripts/assistant/cutover.sh). Stdout stays empty;
# checkin.py prints one ⚠️ line the first time it fails in a day. Diagnostics -> the log.
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/assistant/checkin.log"
mkdir -p "$(dirname "$LOG")"
echo "=== $(date -Is) checkin ===" >> "$LOG"
/usr/bin/python3 "$HOME/.hermes/scripts/assistant/checkin.py" "$@" 2>> "$LOG"
rc=$?
tail -n 1500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
exit "$rc"
