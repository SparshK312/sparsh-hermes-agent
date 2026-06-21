#!/usr/bin/env bash
# Hermes cron: cost-monitor — read state.db, compute Anthropic spend (today + MTD),
# Telegram-alert on threshold crossings. Pure stdlib python under system python3.
# Registered with `hermes cron --no-agent`: stdout is delivered verbatim, EMPTY
# stdout = silent. So everything is routed to the log and NOTHING is printed to
# stdout — the python sends any alert itself via `hermes send`. Dedupe lives in
# the python. Result: silent unless a threshold trips. No agent turn ever runs.
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/logs/cost_monitor.log"
mkdir -p "$(dirname "$LOG")"

{
  echo "=== $(date -Is) cost-monitor ==="
  /usr/bin/python3 "$HOME/.hermes/scripts/monitor/cost_monitor.py" || echo "cost_monitor FAILED"
} >> "$LOG" 2>&1

tail -n 300 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
# No stdout on purpose (--no-agent → empty stdout = silent delivery).
