#!/usr/bin/env bash
# Hermes cron: internship-accountability — weekday-evening "did you apply today?" nudge.
# coach.py self-sends via Bot API only if nothing was applied today AND the Apply-Now
# Worklist has a backlog + budget allows; otherwise silent. Registered --no-agent, so all
# output goes to the log and stdout stays empty (= silent).
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/health/coach.log"
mkdir -p "$(dirname "$LOG")"
{ echo "=== $(date -Is) internship-check ==="; /usr/bin/python3 "$HOME/.hermes/scripts/fitness/coach.py" --mode internship-check; } >> "$LOG" 2>&1
# no stdout on purpose (--no-agent → empty = silent; the message, if any, was already sent)
