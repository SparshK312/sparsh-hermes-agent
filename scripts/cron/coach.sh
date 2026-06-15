#!/usr/bin/env bash
# Hermes cron: health-coach — weekly proactive coaching narrative (Phase 4b).
#
# coach.py reads the week's data (training/nutrition/sleep/weight) + the coaching
# context (Profile + Training Plan + Coach Memory), composes a blunt execution-first
# narrative via a frontier model, SENDS it itself via the Telegram Bot API, then prints
# {"wakeAgent": false} so Hermes skips the agent (no LLM turn, nothing to hijack). On a
# send failure it prints the message so Hermes delivers it as a fallback.
#
# coach.py is stdlib-only (no cairosvg/matplotlib) → runs on system python, not the
# fitness venv. Its stdout (the gate or the message) is passed straight through to Hermes,
# so DON'T redirect it to a log here.
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
exec /usr/bin/python3 "$HOME/.hermes/scripts/fitness/coach.py" --days 7
