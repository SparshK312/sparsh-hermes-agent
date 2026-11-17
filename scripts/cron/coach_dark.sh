#!/usr/bin/env bash
# Hermes cron: health-tracking-dark — the "you've stopped logging food" message.
# coach.py --mode tracking-dark was built and never scheduled, so a long logging gap
# produced no message. It speaks only on day 2, 5, 10 and 20 of a gap, then at most
# every 14 days, and only if the daily message budget allows. Sends itself via the Bot API.
# Registered --no-agent, so all output goes to the log and stdout stays empty (= silent).
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/health/coach.log"
mkdir -p "$(dirname "$LOG")"
{ echo "=== $(date -Is) tracking-dark ==="; /usr/bin/python3 "$HOME/.hermes/scripts/fitness/coach.py" --mode tracking-dark; } >> "$LOG" 2>&1
# no stdout on purpose (--no-agent → empty = silent; the message, if any, was already sent)
