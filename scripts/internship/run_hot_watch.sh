#!/usr/bin/env bash
# Hermes cron: poll the tier-S/A boards and ping Telegram the moment one opens a role.
# Runs every 30 min, 08:00–22:30. hot_watch.py holds alerts overnight itself, so the
# hour window here is belt-and-braces.
set -uo pipefail
LOG="$HOME/.hermes/health/hot_watch.log"
mkdir -p "$(dirname "$LOG")"
{ echo "=== $(date -Is) hot-watch ==="
  cd "$HOME/.hermes/scripts/internship" || exit 1
  timeout 600 "$HOME/.hermes/hermes-agent/venv/bin/python" hot_watch.py
} >> "$LOG" 2>&1
tail -c 200000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
