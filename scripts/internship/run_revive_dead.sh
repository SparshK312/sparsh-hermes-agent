#!/usr/bin/env bash
# Hermes cron: verify "dead" postings against their employers' own ATS before the
# morning refresh, and revive the ones that are still open.
#
# WHY (2026-09-06). The refresh marks a posting dead after N consecutive harvests
# without seeing it — 2 strikes for an employer board, 14 for an aggregator row.
# Aggregator READMEs are rolling windows, so "not listed" is not "closed": on Sep 6
# two refreshes marked 110 rows dead with identical, healthy harvests, and
# revive_dead.py then found 141 of 277 verifiable dead rows (51%) still open on the
# employer's board. The check is cheap (one listing call per board, cached), so it
# runs daily, 30 minutes before the 08:00 refresh that renders the result.
set -uo pipefail

export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
export CURATED_STORE="$HOME/.hermes/internship/curated_postings.json"

PY="$HOME/.hermes/hermes-agent/venv/bin/python"
DIR="$HOME/.hermes/scripts/internship"
LOG="$HOME/.hermes/health/revive.log"
mkdir -p "$(dirname "$LOG")"

{
  t0=$(date +%s)
  echo "=== $(date -Is) revive_dead (cron) ==="
  cd "$DIR" || exit 1
  if pgrep -f "[c]urate.py" >/dev/null; then echo "refresh running — skipped"; exit 0; fi
  timeout 900 "$PY" revive_dead.py "$@"
  rc=$?
  echo "exit=$rc  duration=$(( $(date +%s) - t0 ))s"
} >> "$LOG" 2>&1

tail -c 300000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
