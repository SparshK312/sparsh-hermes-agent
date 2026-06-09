#!/usr/bin/env bash
# Hermes cron: hae-sync — SILENT data job (no Telegram output).
#
# Runs a few times a day (aligned just before the nudges). Three steps:
#   1. prune raw HAE payloads older than 30 days (the metrics.csv archive keeps the data)
#   2. refresh 07 - Health/Metrics/metrics.csv from all raw payloads (hae_process.py)
#   3. write today's HAE metrics into the daily-note frontmatter (hae_daily_ingest.py)
#
# Output is suppressed via the [SILENT] marker so Hermes does NOT post to Telegram —
# this is a background data sync, not a user-facing nudge. Diagnostics go to sync.log.
set -uo pipefail

PY=/usr/bin/python3
H="$HOME/.hermes/scripts"
RAW="$HOME/.hermes/health/hae/raw"
LOG="$HOME/.hermes/health/hae/sync.log"

{
  echo "=== $(date -Is) hae-sync ==="
  find "$RAW" -name '*.json' -type f -mtime +30 -delete 2>/dev/null || true
  "$PY" "$H/hae_process.py"       || echo "hae_process FAILED"
  "$PY" "$H/hae_daily_ingest.py"  || echo "hae_daily_ingest FAILED"
} >> "$LOG" 2>&1

# keep the log bounded
tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true

echo "[SILENT]"
