#!/usr/bin/env bash
# Hermes skill/cron: render the Sleep · Activity · Recovery trend card from
# metrics.csv (Apple Watch archive) and send it to Telegram. The python sends the
# PHOTO itself via the Bot API (Hermes' deliver:telegram only handles text), so
# this wrapper prints the cron wake-gate {"wakeAgent": false} as its final line to
# skip the agent entirely. Uses the dedicated fitness venv (cairosvg + matplotlib).
#
#   trends_report.sh [DAYS]   # default 30
set -uo pipefail

export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"   # modules default to the Mac path otherwise
PY="$HOME/.hermes/venvs/fitness/bin/python3"
APP="$HOME/.hermes/scripts/fitness/metrics_trends.py"
LOG="$HOME/.hermes/health/fitness.log"
DAYS="${1:-30}"
mkdir -p "$(dirname "$LOG")"

{
  echo "=== $(date -Is) trends (${DAYS}d) ==="
  "$PY" "$APP" --days "$DAYS" --send || echo "metrics_trends FAILED"
} >> "$LOG" 2>&1

tail -n 300 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
echo '{"wakeAgent": false}'   # skip the agent: the chart is already sent
