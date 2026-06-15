#!/usr/bin/env bash
# Hermes cron: health-workout-rescue — missed-workout rescue (failure mode #2).
# coach.py stays SILENT unless he hasn't lifted in 2+ days AND is behind this week
# AND the budget allows; then it offers the 25-min fallback. Sends itself via Bot API.
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
exec /usr/bin/python3 "$HOME/.hermes/scripts/fitness/coach.py" --mode workout-rescue
