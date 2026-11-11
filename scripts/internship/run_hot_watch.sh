#!/usr/bin/env bash
# Hermes cron: poll the tier-S/A boards and ping Telegram the moment one opens a role.
# Runs every 30 min, 08:00–22:30. hot_watch.py holds alerts overnight itself, so the
# hour window here is belt-and-braces.
#
# 🔴 2026-11-11: the script's exit status is hot_watch.py's. It used to be the status of
# the final log-trim `mv`, so a failed poll or a failed Telegram send still showed the
# cron job as `ok`. A runner that swallows its child's exit code is a watchdog that
# cannot fail.
set -uo pipefail
LOG="$HOME/.hermes/health/hot_watch.log"
mkdir -p "$(dirname "$LOG")"
rc=0
{ echo "=== $(date -Is) hot-watch ==="
  if cd "$HOME/.hermes/scripts/internship"; then
    timeout 600 "$HOME/.hermes/hermes-agent/venv/bin/python" hot_watch.py "$@"
    rc=$?
  else
    rc=1
  fi
  echo "=== exit $rc ==="
} >> "$LOG" 2>&1
tail -c 200000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit "$rc"
