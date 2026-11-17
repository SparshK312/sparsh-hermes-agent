#!/usr/bin/env bash
# Hermes cron: morning inbox read. Runs BEFORE daily-note-prefill (06:50) so the note
# has something to insert. Registered --no-agent: output goes to the log, stdout stays
# empty, and the agent never sees the raw inbox.
set -uo pipefail
LOG="$HOME/.hermes/health/email_triage.log"
mkdir -p "$(dirname "$LOG")"
{ echo "=== $(date -Is) email-triage ==="
  /home/hermes/.hermes/hermes-agent/venv/bin/python \
    "$HOME/.hermes/scripts/email/email_triage.py" --days 2
} >> "$LOG" 2>&1
