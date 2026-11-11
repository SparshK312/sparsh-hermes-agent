#!/usr/bin/env bash
# Hermes cron: Fall 2027 watcher (fall_watch.py). Hourly 08:15–22:15, offset from
# hot-watch; a weekly `--heartbeat` run proves it is alive. Arguments pass through
# (--seed / --dry-run / --heartbeat).
#
# 🔴 The script's exit status IS fall_watch.py's (non-zero when any source failed or a
# send failed), so the cron's last_status means something. Do not end this file with a
# command whose status would replace it — run_hot_watch.sh did exactly that until
# 2026-11-11.
set -uo pipefail
LOG="$HOME/.hermes/health/fall_watch.log"
mkdir -p "$(dirname "$LOG")"
rc=0
{ echo "=== $(date -Is) fall-watch $* ==="
  if cd "$HOME/.hermes/scripts/internship"; then
    timeout 600 "$HOME/.hermes/hermes-agent/venv/bin/python" fall_watch.py "$@"
    rc=$?
  else
    rc=1
  fi
  echo "=== exit $rc ==="
} >> "$LOG" 2>&1
tail -c 200000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit "$rc"
