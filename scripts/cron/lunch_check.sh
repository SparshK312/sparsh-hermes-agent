#!/usr/bin/env bash
# Hermes cron job: health-lunch-check — fires around midday (schedule in jobs.json,
# evaluated in the Hermes-configured timezone — see config/cron_additions.json).
# AGENT job: Hermes wakes the LLM with this stdout as context and composes the message
# (NOT verbatim). See hae-sync for the $0/no-LLM {"wakeAgent": false} pattern.

set -euo pipefail

cat <<'EOF'
🍽️ *Lunch check.*  Logged lunch yet?

Three ways:
• Just describe it: _"chicken sandwich and salad at the cafeteria"_
• Photo: snap + send, agent estimates macros
• Explicit: `/log-food <description>`

Office AYCE means easy 800-1000 kcal of protein-dense lunch — anchor of the day.

`/today` for running totals.
EOF
