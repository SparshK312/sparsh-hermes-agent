#!/usr/bin/env bash
# Hermes cron: fitbit-sync (every 20 min). Registered --no-agent: no LLM; output to the log.
# Pulls the Fitbit Air's last 7 days from the Google Health API into fitbit.csv, re-derives
# metrics.csv, and refreshes the daily notes only when sleep/heart/zone-minute values moved.
# The script sends its own Telegram alerts (auth broken, band not syncing, repeated failures),
# once per state change. See scripts/hae/fitbit_sync.py.
set -uo pipefail
LOG="$HOME/.hermes/health/hae/fitbit_sync.log"
mkdir -p "$(dirname "$LOG")"
{ echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) fitbit-sync ==="
  T="$(command -v timeout || true)"      # GNU timeout on the VPS; absent on macOS (tests)
  ${T:+$T 900} /usr/bin/python3 "$HOME/.hermes/scripts/fitbit_sync.py"
} >> "$LOG" 2>&1
rc=$?
tail -c 200000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
# Exit codes 2 (partial) and 3 (auth/config) are EXPECTED states the script already alerts
# on, once per change. Passing them through made Hermes deliver "script failed" every run
# (every 20 min) — review 2026-10-08. Only a crash or a timeout (124) is passed through.
echo '{"wakeAgent": false}'
case "$rc" in 0|2|3) exit 0 ;; *) exit "$rc" ;; esac
