#!/usr/bin/env bash
# Hermes cron: upstream-watch (daily). --no-agent. Checks GitHub for the framework fix the
# upgrade is waiting on and sends ONE Telegram message when it merges and one when a stable
# release contains it (scripts/monitor/upstream_watch.py). Stdout stays empty.
set -uo pipefail
LOG="$HOME/.hermes/logs/upstream_watch.log"
mkdir -p "$(dirname "$LOG")"
{ echo "=== $(date -Is) upstream-watch ==="; /usr/bin/python3 "$HOME/.hermes/scripts/monitor/upstream_watch.py"; } >> "$LOG" 2>&1
tail -n 300 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
