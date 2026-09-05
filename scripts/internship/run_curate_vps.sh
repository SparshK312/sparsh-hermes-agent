#!/usr/bin/env bash
# Hermes cron: the curated-board refresh, running ON THE VPS.
#
# WHY THIS MOVED (2026-09-04). The board ran on launchd on Sparsh's Mac, twice daily,
# which meant it only scraped while that laptop was awake. He was in Peru Aug 29 – Sep 3
# and it captured NOTHING — during the single most important week of the cycle, when
# Google, Microsoft, Stripe, Atlassian, ByteDance, Databricks and TikTok all opened.
# The board's last harvest before this was 2026-08-30 23:47.
#
# A single point of failure on the system the whole pipeline depends on, and it would
# have recurred on every trip. The VPS runs 24/7.
#
# PATHS: the store lives OUTSIDE the vault at ~/.hermes/internship/ on purpose.
# `.json` does not sync through Obsidian, so a copy inside the vault would diverge
# silently from the Mac's. Out here, ownership is unambiguous: the VPS owns it.
# The Sheet remains the shared surface both machines read and write.
set -uo pipefail

export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
export CURATED_STORE="$HOME/.hermes/internship/curated_postings.json"
export CURATED_XLSX="$HOME/.hermes/internship/Curated Board.xlsx"

PY="$HOME/.hermes/hermes-agent/venv/bin/python"
DIR="$HOME/.hermes/scripts/internship"
LOG="$HOME/.hermes/health/curate.log"
mkdir -p "$(dirname "$LOG")" "$HOME/.hermes/internship"

{
  echo "=== $(date -Is) curate (vps) ==="
  cd "$DIR" || exit 1
  timeout 1800 "$PY" curate.py "$@"
  echo "exit=$?"
} >> "$LOG" 2>&1

# Keep the log bounded — this runs twice a day forever.
tail -c 400000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
