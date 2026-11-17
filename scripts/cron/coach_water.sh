#!/usr/bin/env bash
# Hermes cron: health-water-check — mid-afternoon hydration nudge. coach.py self-sends
# via Bot API only if behind the paced 2.5L/day target + budget allows; otherwise silent.
# Registered --no-agent, so all output goes to the log and stdout stays empty (= silent).
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/health/coach.log"
mkdir -p "$(dirname "$LOG")"
{ echo "=== $(date -Is) water-check ==="; /usr/bin/python3 "$HOME/.hermes/scripts/fitness/coach.py" --mode water-check; } >> "$LOG" 2>&1
# no stdout on purpose (--no-agent → empty = silent; the message, if any, was already sent)
