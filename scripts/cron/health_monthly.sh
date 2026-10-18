#!/usr/bin/env bash
# Hermes cron: health-monthly — 1st of the month, 10:00. Last month's calendar card
# (sleep / steps / training / food heatmaps, vs the month before) from health_cards.py,
# then the coach's monthly check-in text (coach.py --mode monthly). Added 2026-10-17.
set -uo pipefail
# The scheduler allows the whole script 120 s; the card gets at most 50 so the coach's
# text (model call) still has room. A timed-out card is logged, the text still goes.
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/health/fitness.log"
mkdir -p "$(dirname "$LOG")"
MONTH="${1:-$(TZ=America/Toronto date -d "$(TZ=America/Toronto date +%Y-%m-01) -1 day" +%Y-%m)}"
{
  echo "=== $(date -Is) monthly card ${MONTH} ==="
  timeout 50 "$HOME/.hermes/venvs/fitness/bin/python3" "$HOME/.hermes/scripts/fitness/health_cards.py" month \
    --month "$MONTH" --send || echo "monthly card FAILED"
} >> "$LOG" 2>&1
tail -n 300 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
exec /usr/bin/python3 "$HOME/.hermes/scripts/fitness/coach.py" --mode monthly --month "$MONTH"
