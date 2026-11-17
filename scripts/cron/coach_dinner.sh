#!/usr/bin/env bash
# Hermes cron: health-dinner-check — evening under-eating guard. coach.py self-sends
# via Bot API only if no dinner logged AND the day is light on kcal + budget allows.
# Registered --no-agent, so all output goes to the log and stdout stays empty (= silent).
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/health/coach.log"
mkdir -p "$(dirname "$LOG")"
{ echo "=== $(date -Is) dinner-check ==="; /usr/bin/python3 "$HOME/.hermes/scripts/fitness/coach.py" --mode dinner-check; } >> "$LOG" 2>&1
# no stdout on purpose (--no-agent → empty = silent; the message, if any, was already sent)
