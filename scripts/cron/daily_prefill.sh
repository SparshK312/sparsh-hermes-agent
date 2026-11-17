#!/usr/bin/env bash
# Hermes cron: daily-note-prefill (06:50). Registered --no-agent since 2026-11-16: the
# script IS the job. It replaced an agent cron that cost ~$11/month for mostly mechanical
# work. Stdout is delivered verbatim, so it stays EMPTY on success; daily_prefill.py prints
# one ⚠️ line only when it fails. Diagnostics go to the log.
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/health/daily_prefill.log"
mkdir -p "$(dirname "$LOG")"
echo "=== $(date -Is) daily-prefill ===" >> "$LOG"
/usr/bin/python3 "$HOME/.hermes/scripts/vault/daily_prefill.py" "$@" 2>> "$LOG"
rc=$?
tail -n 400 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
exit "$rc"
