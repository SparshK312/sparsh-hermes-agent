#!/usr/bin/env bash
# Hermes cron: health-coach — Sun + Wed 18:05.
#
# SUNDAY (2026-10-17): first the weekly cards as ONE album (Week in review + Recovery &
# rhythm, plus Week in Food / muscle coverage when he logged that week), rendered by
# health_cards.py in the fitness venv (cairosvg). Then the coach's check-in text (one win,
# one focus, one action). The album replaced the 18:00 fitness-weekly and 18:10
# nutrition-week-food sends, so Sunday evening is two messages instead of three.
# WEDNESDAY: the mid-week check-in text only.
#
# coach.py sends itself via the Bot API and prints {"wakeAgent": false}; on a send failure
# it prints the message so Hermes delivers it. A card failure is logged and never blocks
# the text. Its stdout goes straight to Hermes, so DON'T redirect coach.py's output.
set -uo pipefail
# The scheduler allows the whole script 120 s; the cards get at most 50 so the coach's
# text (model call) still has room. A timed-out card is logged, the text still goes.
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/health/fitness.log"
mkdir -p "$(dirname "$LOG")"
if [ "$(TZ=America/Toronto date +%u)" = "7" ]; then
  {
    echo "=== $(date -Is) weekly cards ==="
    timeout 50 "$HOME/.hermes/venvs/fitness/bin/python3" "$HOME/.hermes/scripts/fitness/health_cards.py" week --send \
      || echo "weekly cards FAILED"
  } >> "$LOG" 2>&1
  tail -n 300 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
fi
exec /usr/bin/python3 "$HOME/.hermes/scripts/fitness/coach.py" --days 7
