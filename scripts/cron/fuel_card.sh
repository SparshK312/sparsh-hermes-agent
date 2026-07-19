#!/usr/bin/env bash
# Hermes cron / on-demand: daily "fuel card" — render today's nutrition infographic
# (calorie ring + macro bars + meal timeline + water + coach line) and send the photo
# to Telegram. The python sends the PHOTO itself via the Bot API; this wrapper runs as a
# --no-agent cron job, so its STDOUT is delivered verbatim — therefore it stays SILENT on
# success (empty stdout = no message; the photo already went out) and prints a one-line
# alert only on failure. Uses the fitness venv (cairosvg).
set -uo pipefail

export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
PY="$HOME/.hermes/venvs/fitness/bin/python3"
APP="$HOME/.hermes/scripts/fitness/nutrition_card.py"
LOG="$HOME/.hermes/health/fitness.log"
mkdir -p "$(dirname "$LOG")"

echo "=== $(date -Is) fuel-card ===" >> "$LOG"
out="$("$PY" "$APP" 2>>"$LOG")"
echo "$out" >> "$LOG"

tail -n 300 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true

# Retention: dated cards (fuel-/session-/week-food-/coverage-<date>.png) are regenerated
# daily and pile up. Prune anything older than 45 days so the Charts dir stays small.
# Undated charts (muscle-card.png, trends.png) are overwritten in place and have no date
# suffix, so the globs below never match them.
find "$HERMES_VAULT/07 - Health/Charts" -maxdepth 1 -type f \
  \( -name 'fuel-*.png' -o -name 'session-*.png' -o -name 'week-food-*.png' -o -name 'coverage-*.png' \) \
  -mtime +45 -delete 2>/dev/null || true

# [SILENT] = photo sent (or nothing logged today) → stay quiet. Anything else = a problem.
case "$out" in
  *"[SILENT]"*|"") : ;;
  *) echo "⚠️ fuel card failed to send — check ~/.hermes/health/fitness.log" ;;
esac
