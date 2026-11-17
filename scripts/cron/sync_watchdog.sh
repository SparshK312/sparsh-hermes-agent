#!/usr/bin/env bash
# Hermes cron: vault-sync watchdog (every 30 min). Registered --no-agent: no LLM, output
# to the log; the script itself sends Telegram alerts via `hermes send`, only on a state
# change or every 12 h while broken. See scripts/monitor/sync_watchdog.py.
set -uo pipefail
LOG="$HOME/.hermes/health/sync_watchdog.log"
mkdir -p "$(dirname "$LOG")"
/usr/bin/python3 "$HOME/.hermes/scripts/monitor/sync_watchdog.py" >> "$LOG" 2>&1
rc=$?
tail -c 200000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
# Exit with the watchdog's status, not the trim's (audit 2026-10-07: the wrapper always
# exited 0, so the cron read `ok` even when the watchdog crashed or an alert was lost).
exit $rc
