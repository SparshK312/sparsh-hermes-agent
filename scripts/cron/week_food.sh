#!/usr/bin/env bash
# Hermes cron / on-demand: weekly "Week in Food" report — 7-day calorie + protein columns,
# protein hit-rate, avg macro split. The python sends the photo via the Bot API itself.
# Runs as a --no-agent cron, so STDOUT is delivered verbatim: SILENT on success (photo
# already out), one-line alert only on failure. Fitness venv (cairosvg).
set -uo pipefail

export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
PY="$HOME/.hermes/venvs/fitness/bin/python3"
APP="$HOME/.hermes/scripts/fitness/nutrition_card.py"
LOG="$HOME/.hermes/health/fitness.log"
mkdir -p "$(dirname "$LOG")"

echo "=== $(date -Is) week-food ===" >> "$LOG"
out="$("$PY" "$APP" --weekly --days 7 2>>"$LOG")"
echo "$out" >> "$LOG"

tail -n 300 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true

# See fuel_card.sh: [NO-DATA] is NOT success. This wrapper reported ok every Sunday
# since 2026-07-19 while rendering nothing, because both cases printed [SILENT].
case "$out" in
  *"[SILENT]"*|"") : ;;
  *"[NO-DATA]"*) echo "no-data: $out" >> "$LOG" ;;
  *) echo "⚠️ weekly food report failed to send — check ~/.hermes/health/fitness.log" ;;
esac
