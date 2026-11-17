#!/usr/bin/env bash
# Hermes cron: coach-memory-refresh — rewrites the coach-OWNED 'Adherence snapshot (auto)'
# section of Coach Memory.md from the last 28 days of logged data + bumps last_updated, so
# the coaching memory never goes stale (it used to sit at "_TBD — fill in as adherence data
# comes in_" for a month). Silent maintenance: file write only, NO Telegram. Runs as a
# --no-agent cron, so STDOUT is delivered verbatim — it stays quiet on success and emits a
# one-line alert only on failure. coach.py is stdlib-only → system python (not the fitness venv).
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/health/fitness.log"
mkdir -p "$(dirname "$LOG")"

out="$(/usr/bin/python3 "$HOME/.hermes/scripts/fitness/coach.py" --mode refresh-memory 2>>"$LOG")"
echo "=== $(date -Is) coach-memory-refresh: $out ===" >> "$LOG"

case "$out" in
  *"refreshed coach memory"*) : ;;   # success → stay silent
  *) echo "⚠️ coach memory refresh failed — check ~/.hermes/health/fitness.log" ;;
esac
