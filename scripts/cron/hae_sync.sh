#!/usr/bin/env bash
# Hermes cron: hae-sync — SILENT data job (no Telegram output).
#
# Runs a few times a day (aligned just before the nudges). Three steps:
#   1. prune raw HAE payloads older than 30 days (the metrics.csv archive keeps the data)
#   2. refresh 07 - Health/Metrics/metrics.csv from all raw payloads (hae_process.py)
#   3. write today's HAE metrics into the daily-note frontmatter (hae_daily_ingest.py)
#
# The final stdout line is the Hermes cron wake-gate {"wakeAgent": false}: it makes
# run_job skip the agent entirely (no LLM turn, nothing posted to Telegram) — the real
# "silent + $0" mechanism. ([SILENT] is an AGENT response marker, not a script-stdout
# one, so it would NOT reliably suppress here.) This is a background data sync, not a
# user-facing nudge. Diagnostics go to sync.log.
set -uo pipefail

PY=/usr/bin/python3
H="$HOME/.hermes/scripts"
RAW="$HOME/.hermes/health/hae/raw"
LOG="$HOME/.hermes/health/hae/sync.log"
mkdir -p "$(dirname "$LOG")"

{
  echo "=== $(date -Is) hae-sync ==="
  find "$RAW" -name '*.json' -type f -mtime +30 -delete 2>/dev/null || true
  rc=0
  "$PY" "$H/hae_process.py"       || { rc=$?; echo "hae_process FAILED (exit $rc)"; }
  "$PY" "$H/hae_daily_ingest.py"  || { rc=$?; echo "hae_daily_ingest FAILED (exit $rc)"; }
} >> "$LOG" 2>&1

# keep the log bounded
tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true

# A failed step used to be logged and then reported as success (the gate below + exit 0).
# Now a failure exits non-zero WITHOUT the gate, so the scheduler reports it.
[ "${rc:-1}" -eq 0 ] || exit "${rc:-1}"     # rc unset = the block never ran (log dir missing)
echo '{"wakeAgent": false}'   # skip the agent: silent, no LLM turn
