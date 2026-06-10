#!/usr/bin/env bash
# Hermes cron: fitness-weekly — render the muscle-coverage card + coached caption and
# send it to Telegram. The python sends the PHOTO itself via the Bot API (Hermes'
# deliver:telegram only handles text), so this wrapper prints the cron wake-gate
# {"wakeAgent": false} as its final line to skip the agent entirely (no LLM turn,
# nothing for it to hijack) and routes all python output to a log. Uses the dedicated
# fitness venv (has cairosvg).
set -uo pipefail

export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"   # modules default to the Mac path otherwise
PY="$HOME/.hermes/venvs/fitness/bin/python3"
APP="$HOME/.hermes/scripts/fitness/fitness_report.py"
LOG="$HOME/.hermes/health/fitness.log"
mkdir -p "$(dirname "$LOG")"

{
  echo "=== $(date -Is) fitness-weekly ==="
  "$PY" "$APP" --days 7 || echo "fitness_report FAILED"
} >> "$LOG" 2>&1

tail -n 300 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
echo '{"wakeAgent": false}'   # skip the agent: the photo is already sent
