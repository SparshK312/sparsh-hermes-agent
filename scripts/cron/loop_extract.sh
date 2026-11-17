#!/usr/bin/env bash
# Hermes cron: open-loops-extract (daily, early morning, before the brief). --no-agent.
# Reads new vault log entries into the open-loops store and refreshes the vault view
# (00 - Dashboard/Open Loops.md). It SENDS NOTHING. Stdout stays empty on success;
# loop_extract.py prints one ⚠️ line only when it fails. Diagnostics go to the log.
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/assistant/loop_extract.log"
mkdir -p "$(dirname "$LOG")"
echo "=== $(date -Is) loop-extract ===" >> "$LOG"
/usr/bin/python3 "$HOME/.hermes/scripts/assistant/loop_extract.py" "$@" 2>> "$LOG"
rc=$?
tail -n 600 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
exit "$rc"
