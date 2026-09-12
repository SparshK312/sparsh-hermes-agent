#!/usr/bin/env bash
# Hermes cron: the weekly coverage digest — what the board's tier table hid this week.
#
# WHY (2026-09-12). The board only shows companies its tier table names; the rest are
# tier C and deleted before any other check. Over 16 SWElist digests, 79% of in-lane
# postings were at companies the table did not name, and every one had zero rows on any
# tab (American Express, Tradeweb, Epic Games, Akuna, Domino, Tanium, Formlabs...). The
# table grew only after someone noticed. This job reports the hidden set every Saturday
# so a miss becomes a line to promote, not a question nobody thinks to ask.
set -uo pipefail

export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
export CURATED_STORE="$HOME/.hermes/internship/curated_postings.json"

PY="$HOME/.hermes/hermes-agent/venv/bin/python"
DIR="$HOME/.hermes/scripts/internship"
LOG="$HOME/.hermes/health/coverage.log"
mkdir -p "$(dirname "$LOG")"

{
  t0=$(date +%s)
  echo "=== $(date -Is) coverage_digest (cron) ==="
  cd "$DIR" || exit 1
  timeout 900 "$PY" coverage_digest.py "$@"
  rc=$?
  echo "exit=$rc  duration=$(( $(date +%s) - t0 ))s"
} >> "$LOG" 2>&1

tail -c 300000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
