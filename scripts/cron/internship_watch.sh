#!/usr/bin/env bash
# Hermes cron: internship-watcher (proper-B, replaces the old hybrid agent loop).
#
# Scrape the GitHub aggregators -> ONE GPT-5.5 triage call -> write Pipeline.md +
# postings_seen.json deterministically -> send a Telegram digest if anything is
# actionable. internship_triage.py prints ONLY the wake-gate {"wakeAgent": false}
# to stdout, so Hermes runs NO agent (no 55K-context loop, no execute_code thrash,
# no codex broken-pipe). System python (stdlib + bs4, both present).
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
exec /usr/bin/python3 "$HOME/.hermes/scripts/internship/internship_triage.py"
